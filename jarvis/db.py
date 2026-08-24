"""SQLite-Zugriff: Schema, Verbindung und die Umwandlung der Spalten.

Zeitstempel liegen als ISO-8601 in UTC in der Datenbank. Der Homeserver läuft
in UTC und bleibt dabei; in Ortszeit gerechnet wird nur dort, wo es um
Zeitfenster geht (siehe ``scheduler.py``).

Listen und Objekte (Tags, Payload, Ergebnis) stehen als JSON in einer
TEXT-Spalte. Für die paar Dutzend Zeilen pro Tag braucht es keine
Normalisierung, und ein JSON-Feld hält Erweiterungen aus, ohne dass jedes Mal
eine Migration fällig wird.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    payload           TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL,
    priority          INTEGER NOT NULL DEFAULT 50,
    window            TEXT NOT NULL DEFAULT 'any',
    required_tags     TEXT NOT NULL DEFAULT '[]',
    min_vram_mb       INTEGER NOT NULL DEFAULT 0,
    model             TEXT,
    assigned_worker   TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    result            TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    lease_expires_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_prio
    ON jobs (status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease
    ON jobs (lease_expires_at) WHERE lease_expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS workers (
    name            TEXT PRIMARY KEY,
    tags            TEXT NOT NULL DEFAULT '[]',
    models          TEXT NOT NULL DEFAULT '[]',
    vram_mb         INTEGER NOT NULL DEFAULT 0,
    address         TEXT,
    wake_strategy   TEXT NOT NULL DEFAULT 'none',
    registered_at   TEXT NOT NULL,
    last_heartbeat  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_logs (
    job_id   TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL DEFAULT 'info',
    message  TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    """Jetzt, immer mit Zeitzone. Naive Zeitstempel gibt es hier nicht."""
    return datetime.now(UTC)


def to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Zeitstempel ohne Zeitzone gehören nicht in die Datenbank")
    return value.astimezone(UTC).isoformat()


def from_db(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def connect(path: Path | str) -> sqlite3.Connection:
    """Öffnet die Datenbank und legt das Schema an, falls es fehlt."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL: Lesen blockiert Schreiben nicht. Bei einer Datei im Speicher gibt es
    # kein WAL, deshalb der Zweig.
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    return conn


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Schreibtransaktion, die das Schreibrecht sofort nimmt.

    Nötig für das Zuteilen von Jobs: Ohne ``IMMEDIATE`` könnten zwei Worker
    denselben Job lesen, bevor einer von beiden schreibt, und beide bekämen ihn.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
