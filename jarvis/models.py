"""Datentypen für Jobs, Worker und Logzeilen.

Diese Schemas sind zugleich das API-Format. Es gibt bewusst keine zweite
Schicht aus ORM-Klassen: die Tabellen sind flach, und jede Umwandlung mehr
wäre eine Stelle mehr, an der etwas auseinanderläuft.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Zustände, aus denen ein Job noch etwas werden kann.
ACTIVE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.ASSIGNED, JobStatus.RUNNING})
#: Endzustände. Wer hier ist, bleibt hier.
TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})


class JobWindow(StrEnum):
    ANY = "any"
    DAY = "day"
    NIGHT = "night"


class JobType(StrEnum):
    CHAT = "chat"


class WorkerState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class WakeStrategy(StrEnum):
    """Wie der Rechner eines Workers aufwacht. Siehe docs/ARCHITECTURE.md."""

    NONE = "none"
    ALWAYS_ON = "always_on"
    SELF_TIMER = "self_timer"
    WOL = "wol"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class JobCreate(BaseModel):
    """Was beim Absetzen eines Jobs hereinkommt."""

    type: JobType = JobType.CHAT
    prompt: str = Field(min_length=1)
    model: str | None = None
    system: str | None = None
    priority: int = Field(default=50, ge=0, le=999)
    window: JobWindow = JobWindow.ANY
    required_tags: list[str] = Field(default_factory=list)
    min_vram_mb: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @property
    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {"prompt": self.prompt}
        if self.system:
            data["system"] = self.system
        return data


class Job(BaseModel):
    id: str
    type: JobType
    payload: dict[str, Any]
    status: JobStatus
    priority: int
    window: JobWindow
    required_tags: list[str]
    min_vram_mb: int
    model: str | None
    assigned_worker: str | None
    attempts: int
    max_attempts: int
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    lease_expires_at: datetime | None


class JobLogLine(BaseModel):
    seq: int
    ts: datetime
    level: LogLevel
    message: str


class WorkerRegistration(BaseModel):
    """Was ein Worker beim Anmelden über sich sagt."""

    name: str = Field(min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    vram_mb: int = Field(default=0, ge=0)
    address: str | None = None
    wake_strategy: WakeStrategy = WakeStrategy.NONE


class Worker(BaseModel):
    name: str
    tags: list[str]
    models: list[str]
    vram_mb: int
    address: str | None
    wake_strategy: WakeStrategy
    last_heartbeat: datetime
    registered_at: datetime
    state: WorkerState
    current_job: str | None = None
