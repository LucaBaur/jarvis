"""Worker-Registry.

Worker melden sich selbst an und halten sich mit Heartbeats am Leben. Der
Orchestrator kennt keine Worker-Adresse, mit der er von sich aus etwas
anstoßen könnte — ``address`` ist nur Information für die Oberfläche.

Deshalb wird ``online`` auch nicht gespeichert, sondern beim Lesen aus dem
letzten Heartbeat berechnet. Ein Worker, dessen Rechner ausgeht, kann sich ja
nicht mehr abmelden.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from jarvis.db import dump_json, from_db, load_json, to_db, utcnow
from jarvis.models import JobStatus, Worker, WorkerRegistration, WorkerState

DEFAULT_HEARTBEAT_TIMEOUT = 90


def register(
    conn: sqlite3.Connection,
    spec: WorkerRegistration,
    *,
    now: datetime | None = None,
) -> Worker:
    """Meldet einen Worker an oder aktualisiert seine Fähigkeiten.

    Ein Neustart des Workers ist der Normalfall, kein Sonderfall: Die Zeile
    wird überschrieben, nur ``registered_at`` bleibt stehen.
    """
    now = now or utcnow()
    conn.execute(
        "INSERT INTO workers (name, tags, models, vram_mb, address, wake_strategy, "
        "registered_at, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (name) DO UPDATE SET "
        "tags = excluded.tags, models = excluded.models, vram_mb = excluded.vram_mb, "
        "address = excluded.address, wake_strategy = excluded.wake_strategy, "
        "last_heartbeat = excluded.last_heartbeat",
        (
            spec.name,
            dump_json(spec.tags),
            dump_json(spec.models),
            spec.vram_mb,
            spec.address,
            spec.wake_strategy.value,
            to_db(now),
            to_db(now),
        ),
    )
    worker = get_worker(conn, spec.name, now=now)
    assert worker is not None  # gerade geschrieben
    return worker


def heartbeat(
    conn: sqlite3.Connection,
    name: str,
    *,
    now: datetime | None = None,
    heartbeat_timeout: int = DEFAULT_HEARTBEAT_TIMEOUT,
) -> Worker | None:
    """Setzt den Zeitstempel neu. ``None`` heißt: unbekannter Worker."""
    now = now or utcnow()
    cursor = conn.execute(
        "UPDATE workers SET last_heartbeat = ? WHERE name = ?", (to_db(now), name)
    )
    if cursor.rowcount == 0:
        return None
    return get_worker(conn, name, now=now, heartbeat_timeout=heartbeat_timeout)


def get_worker(
    conn: sqlite3.Connection,
    name: str,
    *,
    now: datetime | None = None,
    heartbeat_timeout: int = DEFAULT_HEARTBEAT_TIMEOUT,
) -> Worker | None:
    row = conn.execute("SELECT * FROM workers WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return _row_to_worker(conn, row, now=now or utcnow(), heartbeat_timeout=heartbeat_timeout)


def list_workers(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    heartbeat_timeout: int = DEFAULT_HEARTBEAT_TIMEOUT,
) -> list[Worker]:
    now = now or utcnow()
    rows = conn.execute("SELECT * FROM workers ORDER BY name")
    return [_row_to_worker(conn, row, now=now, heartbeat_timeout=heartbeat_timeout) for row in rows]


def unregister(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("DELETE FROM workers WHERE name = ?", (name,)).rowcount > 0


def is_online(worker: Worker, now: datetime, heartbeat_timeout: int) -> bool:
    return worker.last_heartbeat > now - timedelta(seconds=heartbeat_timeout)


def _current_job(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE assigned_worker = ? AND status IN (?, ?) "
        "ORDER BY started_at DESC LIMIT 1",
        (name, JobStatus.ASSIGNED.value, JobStatus.RUNNING.value),
    ).fetchone()
    return row["id"] if row else None


def _row_to_worker(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    now: datetime,
    heartbeat_timeout: int,
) -> Worker:
    last = from_db(row["last_heartbeat"])
    online = last > now - timedelta(seconds=heartbeat_timeout)
    return Worker(
        name=row["name"],
        tags=load_json(row["tags"], []),
        models=load_json(row["models"], []),
        vram_mb=row["vram_mb"],
        address=row["address"],
        wake_strategy=row["wake_strategy"],
        last_heartbeat=last,
        registered_at=from_db(row["registered_at"]),
        state=WorkerState.ONLINE if online else WorkerState.OFFLINE,
        current_job=_current_job(conn, row["name"]),
    )
