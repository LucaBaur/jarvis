"""Entscheidet, welcher Job zu welchem Worker passt.

Alles hier ist reine Logik ohne Datenbank, damit es sich ohne Aufbau testen
lässt. ``queue.claim_job`` holt sich die Kandidaten aus SQLite und lässt sie
durch ``worker_can_run``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.models import Job, JobWindow, Worker


@dataclass(frozen=True)
class WindowConfig:
    """Wann „Nacht" ist. Tag ist der Rest."""

    night_start: time
    night_end: time
    tz: ZoneInfo

    def is_night(self, moment: datetime) -> bool:
        local = moment.astimezone(self.tz).time()
        if self.night_start <= self.night_end:
            # Fenster innerhalb eines Tages, etwa 01:00 bis 05:00.
            return self.night_start <= local < self.night_end
        # Fenster über Mitternacht, der Normalfall bei 23:00 bis 07:00.
        return local >= self.night_start or local < self.night_end


def window_is_open(window: JobWindow, moment: datetime, config: WindowConfig) -> bool:
    if window is JobWindow.ANY:
        return True
    night = config.is_night(moment)
    return night if window is JobWindow.NIGHT else not night


def next_window_start(
    window: JobWindow, moment: datetime, config: WindowConfig
) -> datetime | None:
    """Wann öffnet das Fenster das nächste Mal?

    Gibt ``None`` zurück, wenn es gerade offen ist. Der Worker braucht das in
    Phase 4, um vor dem Einschlafen einen Weck-Timer zu stellen.
    """
    if window_is_open(window, moment, config):
        return None
    local = moment.astimezone(config.tz)
    target = config.night_start if window is JobWindow.NIGHT else config.night_end
    candidate = local.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(moment.tzinfo)


def worker_can_run(job: Job, worker: Worker) -> bool:
    """Erfüllt der Worker alle Anforderungen des Jobs?"""
    if job.min_vram_mb > worker.vram_mb:
        return False
    if not set(job.required_tags) <= set(worker.tags):
        return False
    # Kein gewünschtes Modell heißt: der Worker nimmt sein eigenes Standardmodell.
    return not (job.model and job.model not in worker.models)


def explain_mismatch(job: Job, worker: Worker) -> str | None:
    """Warum passt der Job nicht? Für Log und Oberfläche."""
    if job.min_vram_mb > worker.vram_mb:
        return f"braucht {job.min_vram_mb} MB VRAM, {worker.name} hat {worker.vram_mb} MB"
    missing_tags = sorted(set(job.required_tags) - set(worker.tags))
    if missing_tags:
        return f"{worker.name} fehlen die Tags: {', '.join(missing_tags)}"
    if job.model and job.model not in worker.models:
        return f"{worker.name} hat das Modell {job.model} nicht"
    return None
