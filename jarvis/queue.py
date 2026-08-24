"""Zustandsautomat der Jobs.

Alle Übergänge laufen über dieses Modul, damit es genau eine Stelle gibt, an
der ein Job seinen Status wechselt. Die Regeln:

* Ein Job wird nicht zugewiesen, sondern verpachtet. ``lease_expires_at`` sagt,
  bis wann sich der Worker gemeldet haben muss.
* Übergänge prüfen immer den erwarteten Ausgangszustand mit. Eine späte
  Rückmeldung eines Workers, dessen Lease schon abgelaufen ist, darf einen
  inzwischen neu vergebenen Job nicht überschreiben.
* Endzustände sind endgültig. Aus ``done`` wird nichts mehr.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

from jarvis import scheduler
from jarvis.db import dump_json, from_db, immediate, load_json, to_db, utcnow
from jarvis.models import (
    ACTIVE_STATUSES,
    Job,
    JobCreate,
    JobLogLine,
    JobStatus,
    JobWindow,
    LogLevel,
    Worker,
)
from jarvis.scheduler import WindowConfig

_JOB_COLUMNS = (
    'id, type, payload, status, priority, "window", required_tags, min_vram_mb, '
    "model, assigned_worker, attempts, max_attempts, result, error, "
    "created_at, started_at, finished_at, lease_expires_at"
)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        type=row["type"],
        payload=load_json(row["payload"], {}),
        status=row["status"],
        priority=row["priority"],
        window=row["window"],
        required_tags=load_json(row["required_tags"], []),
        min_vram_mb=row["min_vram_mb"],
        model=row["model"],
        assigned_worker=row["assigned_worker"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        result=load_json(row["result"]),
        error=row["error"],
        created_at=from_db(row["created_at"]),
        started_at=from_db(row["started_at"]),
        finished_at=from_db(row["finished_at"]),
        lease_expires_at=from_db(row["lease_expires_at"]),
    )


def create_job(conn: sqlite3.Connection, spec: JobCreate, *, now: datetime | None = None) -> Job:
    now = now or utcnow()
    job_id = uuid.uuid4().hex
    conn.execute(
        'INSERT INTO jobs (id, type, payload, status, priority, "window", required_tags, '
        "min_vram_mb, model, max_attempts, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            spec.type.value,
            dump_json(spec.payload),
            JobStatus.QUEUED.value,
            spec.priority,
            spec.window.value,
            dump_json(spec.required_tags),
            spec.min_vram_mb,
            spec.model,
            spec.max_attempts,
            to_db(now),
        ),
    )
    job = get_job(conn, job_id)
    assert job is not None  # gerade eingefügt
    return job


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    conn: sqlite3.Connection,
    *,
    status: JobStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    """Jobs, neueste zuerst. Für die Übersichtsseite."""
    sql = f"SELECT {_JOB_COLUMNS} FROM jobs"
    params: list[object] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status.value)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [_row_to_job(row) for row in conn.execute(sql, params)]


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    return {row["status"]: row["n"] for row in rows}


def claim_job(
    conn: sqlite3.Connection,
    worker: Worker,
    *,
    window_config: WindowConfig,
    lease_seconds: int,
    now: datetime | None = None,
) -> Job | None:
    """Vergibt den passendsten wartenden Job an den Worker.

    Läuft in einer ``BEGIN IMMEDIATE``-Transaktion: Zwei Worker, die im selben
    Moment fragen, bekommen nie denselben Job.
    """
    now = now or utcnow()
    with immediate(conn):
        rows = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE status = ? "
            "ORDER BY priority ASC, created_at ASC, id ASC",
            (JobStatus.QUEUED.value,),
        ).fetchall()

        for row in rows:
            job = _row_to_job(row)
            if not scheduler.window_is_open(job.window, now, window_config):
                continue
            if not scheduler.worker_can_run(job, worker):
                continue
            conn.execute(
                "UPDATE jobs SET status = ?, assigned_worker = ?, lease_expires_at = ? "
                "WHERE id = ? AND status = ?",
                (
                    JobStatus.ASSIGNED.value,
                    worker.name,
                    to_db(now + timedelta(seconds=lease_seconds)),
                    job.id,
                    JobStatus.QUEUED.value,
                ),
            )
            claimed = job.id
            break
        else:
            return None

    append_log(conn, claimed, LogLevel.INFO, f"Von {worker.name} übernommen")
    return get_job(conn, claimed)


def start_job(
    conn: sqlite3.Connection,
    job_id: str,
    worker_name: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> Job | None:
    """``assigned`` -> ``running``. Der Worker hat mit der Arbeit begonnen."""
    now = now or utcnow()
    cursor = conn.execute(
        "UPDATE jobs SET status = ?, started_at = COALESCE(started_at, ?), "
        "attempts = attempts + 1, lease_expires_at = ? "
        "WHERE id = ? AND status = ? AND assigned_worker = ?",
        (
            JobStatus.RUNNING.value,
            to_db(now),
            to_db(now + timedelta(seconds=lease_seconds)),
            job_id,
            JobStatus.ASSIGNED.value,
            worker_name,
        ),
    )
    if cursor.rowcount == 0:
        return None
    return get_job(conn, job_id)


def renew_lease(
    conn: sqlite3.Connection,
    worker_name: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> int:
    """Verlängert den Lease aller Jobs, an denen der Worker gerade arbeitet.

    Wird vom Heartbeat aufgerufen. Ein Job, der stundenlang rechnet, bleibt
    dadurch dem Worker erhalten, ohne dass der Lease absurd lang sein muss.
    """
    now = now or utcnow()
    cursor = conn.execute(
        "UPDATE jobs SET lease_expires_at = ? WHERE assigned_worker = ? AND status IN (?, ?)",
        (
            to_db(now + timedelta(seconds=lease_seconds)),
            worker_name,
            JobStatus.ASSIGNED.value,
            JobStatus.RUNNING.value,
        ),
    )
    return cursor.rowcount


def complete_job(
    conn: sqlite3.Connection,
    job_id: str,
    worker_name: str,
    result: dict[str, object],
    *,
    now: datetime | None = None,
) -> Job | None:
    now = now or utcnow()
    cursor = conn.execute(
        "UPDATE jobs SET status = ?, result = ?, finished_at = ?, lease_expires_at = NULL "
        "WHERE id = ? AND status = ? AND assigned_worker = ?",
        (
            JobStatus.DONE.value,
            dump_json(result),
            to_db(now),
            job_id,
            JobStatus.RUNNING.value,
            worker_name,
        ),
    )
    if cursor.rowcount == 0:
        return None
    append_log(conn, job_id, LogLevel.INFO, "Fertig")
    return get_job(conn, job_id)


def fail_job(
    conn: sqlite3.Connection,
    job_id: str,
    worker_name: str,
    error: str,
    *,
    now: datetime | None = None,
    retry: bool = True,
) -> Job | None:
    """Meldet einen Fehlschlag.

    Solange noch Versuche übrig sind und ``retry`` gesetzt ist, geht der Job
    zurück in die Queue statt auf ``failed``.
    """
    now = now or utcnow()
    job = get_job(conn, job_id)
    if job is None or job.assigned_worker != worker_name:
        return None
    if job.status not in {JobStatus.ASSIGNED, JobStatus.RUNNING}:
        return None

    can_retry = retry and job.attempts < job.max_attempts
    if can_retry:
        conn.execute(
            "UPDATE jobs SET status = ?, assigned_worker = NULL, lease_expires_at = NULL, "
            "error = ? WHERE id = ?",
            (JobStatus.QUEUED.value, error, job_id),
        )
        append_log(
            conn,
            job_id,
            LogLevel.WARNING,
            f"Versuch {job.attempts} von {job.max_attempts} fehlgeschlagen: {error}",
        )
    else:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, finished_at = ?, lease_expires_at = NULL "
            "WHERE id = ?",
            (JobStatus.FAILED.value, error, to_db(now), job_id),
        )
        append_log(conn, job_id, LogLevel.ERROR, f"Endgültig fehlgeschlagen: {error}")
    return get_job(conn, job_id)


def cancel_job(
    conn: sqlite3.Connection, job_id: str, *, now: datetime | None = None
) -> Job | None:
    """Bricht einen Job ab. Ein laufender Worker merkt das beim nächsten Log."""
    now = now or utcnow()
    cursor = conn.execute(
        "UPDATE jobs SET status = ?, finished_at = ?, lease_expires_at = NULL "
        "WHERE id = ? AND status IN (?, ?, ?)",
        (
            JobStatus.CANCELLED.value,
            to_db(now),
            job_id,
            *(s.value for s in (JobStatus.QUEUED, JobStatus.ASSIGNED, JobStatus.RUNNING)),
        ),
    )
    if cursor.rowcount == 0:
        return None
    append_log(conn, job_id, LogLevel.WARNING, "Abgebrochen")
    return get_job(conn, job_id)


def reap_expired_leases(conn: sqlite3.Connection, *, now: datetime | None = None) -> list[str]:
    """Holt Jobs zurück, deren Worker sich nicht mehr meldet.

    Ohne das bliebe ein Job für immer in ``running`` hängen, sobald der PC
    mitten in der Arbeit heruntergefahren wird. Gibt die IDs der betroffenen
    Jobs zurück.
    """
    now = now or utcnow()
    rows = conn.execute(
        f"SELECT {_JOB_COLUMNS} FROM jobs "
        "WHERE status IN (?, ?) AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
        (JobStatus.ASSIGNED.value, JobStatus.RUNNING.value, to_db(now)),
    ).fetchall()

    reaped: list[str] = []
    for row in rows:
        job = _row_to_job(row)
        worker = job.assigned_worker or "unbekannt"
        if job.attempts >= job.max_attempts:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, finished_at = ?, "
                "lease_expires_at = NULL WHERE id = ?",
                (
                    JobStatus.FAILED.value,
                    f"Worker {worker} hat sich nicht mehr gemeldet",
                    to_db(now),
                    job.id,
                ),
            )
            append_log(
                conn,
                job.id,
                LogLevel.ERROR,
                f"Lease abgelaufen, keine Versuche mehr übrig (Worker {worker})",
            )
        else:
            conn.execute(
                "UPDATE jobs SET status = ?, assigned_worker = NULL, lease_expires_at = NULL "
                "WHERE id = ?",
                (JobStatus.QUEUED.value, job.id),
            )
            append_log(
                conn,
                job.id,
                LogLevel.WARNING,
                f"Lease abgelaufen, zurück in die Queue (Worker {worker})",
            )
        reaped.append(job.id)
    return reaped


def append_log(
    conn: sqlite3.Connection,
    job_id: str,
    level: LogLevel,
    message: str,
    *,
    now: datetime | None = None,
) -> int:
    """Hängt eine Logzeile an. Fehler sollen im Job stehen, nicht in stderr."""
    now = now or utcnow()
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS last FROM job_logs WHERE job_id = ?", (job_id,)
    ).fetchone()
    seq = row["last"] + 1
    conn.execute(
        "INSERT INTO job_logs (job_id, seq, ts, level, message) VALUES (?, ?, ?, ?, ?)",
        (job_id, seq, to_db(now), level.value, message),
    )
    return seq


def get_logs(conn: sqlite3.Connection, job_id: str, *, after_seq: int = 0) -> list[JobLogLine]:
    rows = conn.execute(
        "SELECT seq, ts, level, message FROM job_logs WHERE job_id = ? AND seq > ? ORDER BY seq",
        (job_id, after_seq),
    )
    return [
        JobLogLine(
            seq=row["seq"],
            ts=from_db(row["ts"]),
            level=row["level"],
            message=row["message"],
        )
        for row in rows
    ]


def is_active(status: JobStatus) -> bool:
    return status in ACTIVE_STATUSES


__all__ = [
    "JobWindow",
    "append_log",
    "cancel_job",
    "claim_job",
    "complete_job",
    "count_by_status",
    "create_job",
    "fail_job",
    "get_job",
    "get_logs",
    "is_active",
    "list_jobs",
    "reap_expired_leases",
    "renew_lease",
    "start_job",
]
