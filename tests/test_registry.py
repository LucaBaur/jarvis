"""Tests für die Worker-Registry.

Worker melden sich selbst an und halten sich mit Heartbeats am Leben. Der
Orchestrator ruft nie einen Worker auf, deshalb ist ein Worker ohne Heartbeat
schlicht offline und nicht etwa kaputt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jarvis import queue, registry
from jarvis.models import JobCreate, WakeStrategy, WorkerRegistration, WorkerState

NOON = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)

GPU_PC = WorkerRegistration(
    name="gpu-pc",
    tags=["gpu", "coding"],
    models=["llama3.1:8b", "phi3:mini"],
    vram_mb=6144,
    address="192.168.8.124",
    wake_strategy=WakeStrategy.ALWAYS_ON,
)


def test_registrierung_legt_den_worker_an(conn):
    worker = registry.register(conn, GPU_PC, now=NOON)

    assert worker.name == "gpu-pc"
    assert worker.tags == ["gpu", "coding"]
    assert worker.vram_mb == 6144
    assert worker.wake_strategy is WakeStrategy.ALWAYS_ON
    assert worker.registered_at == NOON
    assert worker.last_heartbeat == NOON
    assert worker.state is WorkerState.ONLINE


def test_erneute_registrierung_aktualisiert_statt_zu_verdoppeln(conn):
    """Der Worker startet neu, etwa nach einem Reboot des PCs."""
    registry.register(conn, GPU_PC, now=NOON)

    neu = GPU_PC.model_copy(update={"models": ["llama3.1:8b", "qwen2.5-coder:7b"]})
    aktualisiert = registry.register(conn, neu, now=NOON + timedelta(hours=1))

    assert len(registry.list_workers(conn, now=NOON + timedelta(hours=1))) == 1
    assert "qwen2.5-coder:7b" in aktualisiert.models
    # Der ursprüngliche Anmeldezeitpunkt bleibt erhalten.
    assert aktualisiert.registered_at == NOON


def test_heartbeat_haelt_den_worker_online(conn):
    registry.register(conn, GPU_PC, now=NOON)

    spaet = NOON + timedelta(minutes=30)
    worker = registry.heartbeat(conn, "gpu-pc", now=spaet)

    assert worker.last_heartbeat == spaet
    assert worker.state is WorkerState.ONLINE


def test_heartbeat_eines_unbekannten_workers_ist_kein_treffer(conn):
    assert registry.heartbeat(conn, "gibt-es-nicht", now=NOON) is None


def test_ohne_heartbeat_gilt_der_worker_als_offline(conn):
    """Der PC wird ausgeschaltet. Das ist der Normalfall, kein Fehler."""
    registry.register(conn, GPU_PC, now=NOON)

    spaeter = NOON + timedelta(seconds=200)
    worker = registry.get_worker(conn, "gpu-pc", now=spaeter, heartbeat_timeout=90)
    assert worker.state is WorkerState.OFFLINE


def test_liste_zeigt_online_und_offline(conn):
    registry.register(conn, GPU_PC, now=NOON)
    registry.register(
        conn,
        WorkerRegistration(name="light", tags=["cpu"], models=["phi3:mini"], vram_mb=0),
        now=NOON + timedelta(seconds=190),
    )

    jetzt = NOON + timedelta(seconds=200)
    zustaende = {
        w.name: w.state for w in registry.list_workers(conn, now=jetzt, heartbeat_timeout=90)
    }
    assert zustaende == {"gpu-pc": WorkerState.OFFLINE, "light": WorkerState.ONLINE}


def test_worker_zeigt_seinen_laufenden_job(conn, windows):
    worker = registry.register(conn, GPU_PC, now=NOON)
    job = queue.create_job(conn, JobCreate(prompt="Sag Hallo"), now=NOON)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    assert registry.get_worker(conn, "gpu-pc", now=NOON).current_job == job.id


def test_worker_ohne_job_hat_kein_current_job(conn):
    registry.register(conn, GPU_PC, now=NOON)
    assert registry.get_worker(conn, "gpu-pc", now=NOON).current_job is None


def test_abgemeldeter_worker_verschwindet(conn):
    registry.register(conn, GPU_PC, now=NOON)
    assert registry.unregister(conn, "gpu-pc") is True
    assert registry.get_worker(conn, "gpu-pc", now=NOON) is None
    assert registry.unregister(conn, "gpu-pc") is False


def test_offline_gegangener_worker_gibt_seinen_job_frei(conn, windows):
    """Zusammenspiel von Registry und Queue: PC aus, Job zurück in die Queue."""
    worker = registry.register(conn, GPU_PC, now=NOON)
    job = queue.create_job(conn, JobCreate(prompt="lange Aufgabe"), now=NOON)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    # Ab hier kein Heartbeat mehr.
    spaeter = NOON + timedelta(seconds=120)
    assert registry.get_worker(conn, "gpu-pc", now=spaeter, heartbeat_timeout=90).state is (
        WorkerState.OFFLINE
    )
    assert queue.reap_expired_leases(conn, now=spaeter) == [job.id]
    assert queue.get_job(conn, job.id).status.value == "queued"
