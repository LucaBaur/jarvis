"""Tests für Zeitfenster und Capability-Abgleich."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from jarvis import queue, scheduler
from jarvis.models import JobCreate, JobWindow
from jarvis.scheduler import WindowConfig
from tests.conftest import BERLIN, make_worker


def berlin(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Ein Zeitpunkt in Berliner Ortszeit, gespeichert wird trotzdem UTC."""
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN).astimezone(UTC)


class TestZeitfenster:
    def test_nacht_ueber_mitternacht(self, windows):
        assert windows.is_night(berlin(2026, 8, 24, 23, 30))
        assert windows.is_night(berlin(2026, 8, 25, 3, 0))
        assert windows.is_night(berlin(2026, 8, 25, 6, 59))
        assert not windows.is_night(berlin(2026, 8, 25, 7, 0))
        assert not windows.is_night(berlin(2026, 8, 25, 14, 0))
        assert not windows.is_night(berlin(2026, 8, 24, 22, 59))

    def test_fenster_innerhalb_eines_tages(self):
        config = WindowConfig(night_start=time(1, 0), night_end=time(5, 0), tz=BERLIN)
        assert config.is_night(berlin(2026, 8, 24, 3, 0))
        assert not config.is_night(berlin(2026, 8, 24, 23, 0))

    def test_any_ist_immer_offen(self, windows):
        for stunde in (0, 6, 12, 18, 23):
            moment = berlin(2026, 8, 24, stunde)
            assert scheduler.window_is_open(JobWindow.ANY, moment, windows)

    def test_day_ist_das_gegenteil_von_night(self, windows):
        for stunde in range(24):
            moment = berlin(2026, 8, 24, stunde)
            nacht = scheduler.window_is_open(JobWindow.NIGHT, moment, windows)
            tag = scheduler.window_is_open(JobWindow.DAY, moment, windows)
            assert nacht is not tag

    def test_sommerzeit_wird_richtig_umgerechnet(self, windows):
        """Der Server rechnet in UTC, das Fenster gilt in Ortszeit.

        Im Sommer ist Berlin UTC+2: 21:30 UTC sind 23:30 Ortszeit, also Nacht.
        Im Winter ist Berlin UTC+1, dieselbe UTC-Zeit ist dann 22:30 und Tag.
        """
        sommer = datetime(2026, 7, 15, 21, 30, tzinfo=UTC)
        winter = datetime(2026, 12, 15, 21, 30, tzinfo=UTC)
        assert windows.is_night(sommer)
        assert not windows.is_night(winter)


class TestNaechstesFenster:
    def test_offenes_fenster_liefert_none(self, windows):
        moment = berlin(2026, 8, 25, 2, 0)
        assert scheduler.next_window_start(JobWindow.NIGHT, moment, windows) is None
        assert scheduler.next_window_start(JobWindow.ANY, moment, windows) is None

    def test_naechster_nachtbeginn_am_selben_tag(self, windows):
        moment = berlin(2026, 8, 24, 14, 0)
        naechstes = scheduler.next_window_start(JobWindow.NIGHT, moment, windows)
        lokal = naechstes.astimezone(BERLIN)
        assert (lokal.day, lokal.hour, lokal.minute) == (24, 23, 0)

    def test_naechster_tagbeginn_ist_morgen_frueh(self, windows):
        moment = berlin(2026, 8, 24, 23, 30)
        naechstes = scheduler.next_window_start(JobWindow.DAY, moment, windows)
        lokal = naechstes.astimezone(BERLIN)
        assert (lokal.day, lokal.hour) == (25, 7)


class TestCapabilities:
    def job(self, **kwargs):
        conn_spec = JobCreate(prompt="egal", **kwargs)
        # Ein Job-Objekt ohne Datenbank, für den reinen Abgleich.
        return _als_job(conn_spec)

    def test_zu_wenig_vram(self):
        job = self.job(min_vram_mb=24000)
        worker = make_worker(vram_mb=6144)
        assert not scheduler.worker_can_run(job, worker)
        assert "24000 MB VRAM" in scheduler.explain_mismatch(job, worker)

    def test_genug_vram(self):
        job = self.job(min_vram_mb=4000)
        assert scheduler.worker_can_run(job, make_worker(vram_mb=6144))

    def test_fehlender_tag(self):
        job = self.job(required_tags=["stt"])
        worker = make_worker(tags=["gpu", "coding"])
        assert not scheduler.worker_can_run(job, worker)
        assert "stt" in scheduler.explain_mismatch(job, worker)

    def test_alle_tags_vorhanden(self):
        job = self.job(required_tags=["gpu", "coding"])
        assert scheduler.worker_can_run(job, make_worker(tags=["gpu", "coding", "stt"]))

    def test_modell_nicht_vorhanden(self):
        job = self.job(model="qwen2.5-coder:7b")
        worker = make_worker(models=["llama3.1:8b"])
        assert not scheduler.worker_can_run(job, worker)
        assert "qwen2.5-coder:7b" in scheduler.explain_mismatch(job, worker)

    def test_ohne_modellwunsch_passt_jeder_worker(self):
        job = self.job()
        assert job.model is None
        assert scheduler.worker_can_run(job, make_worker(models=[]))

    def test_passender_job_hat_keine_erklaerung(self):
        job = self.job(model="llama3.1:8b", required_tags=["gpu"], min_vram_mb=4000)
        assert scheduler.explain_mismatch(job, make_worker()) is None


class TestGrosserWorkerDocktAn:
    """Phase 5: Der 3090-Build bekommt die Jobs, die der 1660er nicht stemmt."""

    def test_grosser_job_wartet_auf_den_grossen_worker(self, conn, windows):
        klein = make_worker("gpu-pc", vram_mb=6144, models=["llama3.1:8b"])
        gross = make_worker("gpu-3090", vram_mb=49152, models=["llama3.1:8b", "llama3.1:70b"])

        job = queue.create_job(
            conn, JobCreate(prompt="grosse Aufgabe", model="llama3.1:70b", min_vram_mb=40000)
        )
        jetzt = berlin(2026, 8, 24, 14, 0)

        zu_klein = queue.claim_job(conn, klein, window_config=windows, lease_seconds=60, now=jetzt)
        assert zu_klein is None
        claimed = queue.claim_job(conn, gross, window_config=windows, lease_seconds=60, now=jetzt)
        assert claimed.id == job.id


def _als_job(spec: JobCreate):
    """Baut ein Job-Objekt aus einer Spezifikation, ohne Datenbank."""
    from jarvis.db import connect

    conn = connect(":memory:")
    try:
        return queue.create_job(conn, spec)
    finally:
        conn.close()


@pytest.fixture
def windows() -> WindowConfig:
    return WindowConfig(night_start=time(23, 0), night_end=time(7, 0), tz=ZoneInfo("Europe/Berlin"))
