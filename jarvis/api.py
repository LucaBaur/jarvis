"""REST-Schnittstelle.

Zwei Gruppen von Endpunkten hinter demselben Token: die, die ich vom Handy aus
benutze, und die, die ein Worker anspricht. Getrennt sind sie nur im Pfad —
ein Rollenmodell wäre bei einem Nutzer Aufwand ohne Gewinn.

Statuscodes mit Bedeutung: 204 beim Claim heißt „nichts zu tun" und ist der
Normalfall, kein Fehler. 409 heißt, dass der Job nicht mehr in dem Zustand ist,
den der Aufrufer erwartet hat — typischerweise ein Worker, dessen Lease
abgelaufen ist und dessen Job längst neu vergeben wurde.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from jarvis import queue, registry
from jarvis.audio import AudioFehler, lies_wav
from jarvis.auth import require_token
from jarvis.config import Settings
from jarvis.db import to_db, utcnow
from jarvis.deps import get_db, get_settings_dep
from jarvis.models import (
    Job,
    JobCreate,
    JobLogLine,
    JobStatus,
    JobWindow,
    LogLevel,
    Worker,
    WorkerRegistration,
)
from jarvis.stt import SttFehler

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings_dep)]


class JobList(BaseModel):
    jobs: list[Job]


class LogList(BaseModel):
    lines: list[JobLogLine]


class WorkerList(BaseModel):
    workers: list[Worker]


class WorkerRef(BaseModel):
    worker: str = Field(min_length=1)


class LogWrite(WorkerRef):
    message: str
    level: LogLevel = LogLevel.INFO


class CompleteWrite(WorkerRef):
    result: dict[str, Any] = Field(default_factory=dict)


class FailWrite(WorkerRef):
    error: str
    retry: bool = True


class ReapResult(BaseModel):
    requeued: list[str]


def _job_or_404(conn: sqlite3.Connection, job_id: str) -> Job:
    job = queue.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    return job


def _conflict(job: Job, was: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        f"Job {job.id} ist {job.status.value}, {was} nicht möglich",
    )


# --- Jobs -------------------------------------------------------------------


@router.post("/jobs", response_model=Job, status_code=status.HTTP_201_CREATED)
def create_job(spec: JobCreate, conn: Db) -> Job:
    return queue.create_job(conn, spec)


@router.post("/jobs/audio", response_model=Job, status_code=status.HTTP_201_CREATED)
def create_job_from_audio(
    request: Request,
    conn: Db,
    audio: Annotated[UploadFile, File()],
    model: Annotated[str | None, Form()] = None,
    priority: Annotated[int, Form()] = 50,
    window: Annotated[JobWindow, Form()] = JobWindow.ANY,
) -> Job:
    """Sprachnachricht hochladen, transkribieren, als Auftrag einreihen.

    Die Erkennung laeuft hier in der Anfrage, nicht als eigener Job. Das ist
    eine bewusste Vereinfachung: Whisper braucht auf dem Homeserver ungefaehr
    so lange wie die Aufnahme dauert, und eine Sprachnachricht ist typisch ein
    paar Sekunden lang. Die Rechenarbeit macht ohnehin der Wyoming-Container,
    nicht der Orchestrator.
    """
    erkenner = request.app.state.transcriber
    if erkenner is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Die Spracherkennung ist abgeschaltet (JARVIS_STT_ENABLED=false).",
        )

    try:
        aufnahme = lies_wav(audio.file.read())
    except AudioFehler as fehler:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(fehler)) from fehler

    try:
        text = erkenner.transcribe(aufnahme)
    except SttFehler as fehler:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(fehler)) from fehler

    job = queue.create_job(
        conn,
        JobCreate(prompt=text, model=model or None, priority=priority, window=window),
    )
    # Herkunft festhalten, damit spaeter unterscheidbar ist, was gesprochen war.
    conn.execute(
        "UPDATE jobs SET payload = json_set(payload, '$.quelle', 'sprache') WHERE id = ?",
        (job.id,),
    )
    queue.append_log(
        conn,
        job.id,
        LogLevel.INFO,
        f"Aus {aufnahme.dauer_s:.1f} s Sprache erkannt: {text}",
    )
    return queue.get_job(conn, job.id)


@router.get("/jobs", response_model=JobList)
def list_jobs(
    conn: Db,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: int = 100,
    offset: int = 0,
) -> JobList:
    return JobList(jobs=queue.list_jobs(conn, status=status_filter, limit=limit, offset=offset))


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, conn: Db) -> Job:
    return _job_or_404(conn, job_id)


@router.get("/jobs/{job_id}/logs", response_model=LogList)
def get_logs(job_id: str, conn: Db, after: int = 0) -> LogList:
    _job_or_404(conn, job_id)
    return LogList(lines=queue.get_logs(conn, job_id, after_seq=after))


@router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(job_id: str, conn: Db) -> Job:
    job = _job_or_404(conn, job_id)
    abgebrochen = queue.cancel_job(conn, job_id)
    if abgebrochen is None:
        raise _conflict(job, "Abbrechen")
    return abgebrochen


# --- Worker-Protokoll -------------------------------------------------------


@router.post("/workers/register", response_model=Worker)
def register_worker(spec: WorkerRegistration, conn: Db) -> Worker:
    return registry.register(conn, spec)


@router.get("/workers", response_model=WorkerList)
def list_workers(conn: Db, settings: Config) -> WorkerList:
    return WorkerList(
        workers=registry.list_workers(conn, heartbeat_timeout=settings.heartbeat_timeout)
    )


@router.post("/workers/{name}/heartbeat", response_model=Worker)
def worker_heartbeat(name: str, conn: Db, settings: Config) -> Worker:
    worker = registry.heartbeat(conn, name, heartbeat_timeout=settings.heartbeat_timeout)
    if worker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Worker {name} ist nicht angemeldet")
    # Solange der Worker lebt, darf sein Job nicht eingesammelt werden.
    queue.renew_lease(conn, name, lease_seconds=settings.lease_seconds)
    return worker


@router.post("/workers/{name}/claim", response_model=Job)
def claim_job(name: str, conn: Db, settings: Config, response: Response) -> Job | Response:
    worker = registry.get_worker(conn, name, heartbeat_timeout=settings.heartbeat_timeout)
    if worker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Worker {name} ist nicht angemeldet")

    job = queue.claim_job(
        conn,
        worker,
        window_config=settings.window_config,
        lease_seconds=settings.lease_seconds,
    )
    if job is None:
        # Nichts zu tun ist der Normalfall, kein Fehler.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return job


@router.post("/jobs/{job_id}/start", response_model=Job)
def start_job(job_id: str, body: WorkerRef, conn: Db, settings: Config) -> Job:
    job = _job_or_404(conn, job_id)
    gestartet = queue.start_job(
        conn, job_id, body.worker, lease_seconds=settings.lease_seconds
    )
    if gestartet is None:
        raise _conflict(job, "Starten")
    return gestartet


@router.post("/jobs/{job_id}/log", status_code=status.HTTP_202_ACCEPTED)
def write_log(job_id: str, body: LogWrite, conn: Db) -> dict[str, int]:
    _job_or_404(conn, job_id)
    return {"seq": queue.append_log(conn, job_id, body.level, body.message)}


@router.post("/jobs/{job_id}/complete", response_model=Job)
def complete_job(job_id: str, body: CompleteWrite, conn: Db) -> Job:
    job = _job_or_404(conn, job_id)
    fertig = queue.complete_job(conn, job_id, body.worker, body.result)
    if fertig is None:
        raise _conflict(job, "Abschließen")
    return fertig


@router.post("/jobs/{job_id}/fail", response_model=Job)
def fail_job(job_id: str, body: FailWrite, conn: Db) -> Job:
    job = _job_or_404(conn, job_id)
    gescheitert = queue.fail_job(conn, job_id, body.worker, body.error, retry=body.retry)
    if gescheitert is None:
        raise _conflict(job, "Fehler melden")
    return gescheitert


# --- Wartung ----------------------------------------------------------------


@router.post("/maintenance/reap", response_model=ReapResult)
def reap(conn: Db, force: Annotated[bool, Body(embed=True)] = False) -> ReapResult:
    """Holt Jobs zurück, deren Worker sich nicht mehr meldet.

    Läuft im Hintergrund automatisch. ``force`` lässt alle laufenden Leases
    sofort verfallen und ist für Tests und zum Aufräumen von Hand da.
    """
    now: datetime = utcnow()
    if force:
        conn.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE status IN (?, ?)",
            (to_db(now), JobStatus.ASSIGNED.value, JobStatus.RUNNING.value),
        )
        now = now + timedelta(seconds=1)
    return ReapResult(requeued=queue.reap_expired_leases(conn, now=now))
