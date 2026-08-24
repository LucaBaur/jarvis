"""Tests für den Zustandsautomaten der Jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jarvis import queue
from jarvis.models import JobCreate, JobStatus, JobWindow, LogLevel
from tests.conftest import make_worker

NOON = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)  # 12:00 Berliner Zeit


def new_job(conn, **kwargs):
    spec = JobCreate(prompt=kwargs.pop("prompt", "Sag Hallo"), **kwargs)
    return queue.create_job(conn, spec, now=NOON)


def test_neuer_job_ist_queued_und_hat_keinen_worker(conn):
    job = new_job(conn)
    assert job.status is JobStatus.QUEUED
    assert job.assigned_worker is None
    assert job.attempts == 0
    assert job.payload["prompt"] == "Sag Hallo"


def test_ohne_worker_bleibt_der_job_liegen(conn, windows):
    """Die Kernanforderung: Ist die GPU aus, wartet der Job einfach."""
    job = new_job(conn)
    assert queue.get_job(conn, job.id).status is JobStatus.QUEUED
    assert queue.list_jobs(conn, status=JobStatus.QUEUED) != []


def test_durchlauf_bis_done(conn, windows, worker):
    job = new_job(conn)

    claimed = queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    assert claimed.id == job.id
    assert claimed.status is JobStatus.ASSIGNED
    assert claimed.assigned_worker == worker.name

    running = queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)
    assert running.status is JobStatus.RUNNING
    assert running.attempts == 1
    assert running.started_at is not None

    done = queue.complete_job(conn, job.id, worker.name, {"text": "Hallo"}, now=NOON)
    assert done.status is JobStatus.DONE
    assert done.result == {"text": "Hallo"}
    assert done.finished_at is not None
    assert done.lease_expires_at is None


def test_zwei_worker_bekommen_nie_denselben_job(conn, windows):
    """Bei einem einzigen Job darf nur einer der beiden etwas bekommen."""
    job = new_job(conn)
    erster = make_worker("gpu-pc")
    zweiter = make_worker("gpu-3090")

    a = queue.claim_job(conn, erster, window_config=windows, lease_seconds=60, now=NOON)
    b = queue.claim_job(conn, zweiter, window_config=windows, lease_seconds=60, now=NOON)

    assert a is not None
    assert b is None
    assert queue.get_job(conn, job.id).assigned_worker == erster.name


def test_prioritaet_schlaegt_alter(conn, windows, worker):
    new_job(conn, prompt="unwichtig, aber zuerst da", priority=90)
    wichtig = new_job(conn, prompt="wichtig", priority=10)

    claimed = queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    assert claimed.id == wichtig.id


def test_abgelaufener_lease_legt_den_job_zurueck(conn, windows, worker):
    """Der PC wird mitten im Job ausgeschaltet."""
    job = new_job(conn)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    spaeter = NOON + timedelta(seconds=61)
    assert queue.reap_expired_leases(conn, now=spaeter) == [job.id]

    zurueck = queue.get_job(conn, job.id)
    assert zurueck.status is JobStatus.QUEUED
    assert zurueck.assigned_worker is None
    assert zurueck.attempts == 1  # der Versuch zählt

    # und er lässt sich erneut übernehmen
    erneut = queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=spaeter)
    assert erneut.id == job.id


def test_lease_wird_durch_heartbeat_verlaengert(conn, windows, worker):
    """Ein Job, der stundenlang rechnet, darf nicht eingesammelt werden."""
    job = new_job(conn)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    for schritt in range(1, 10):
        moment = NOON + timedelta(seconds=30 * schritt)
        assert queue.renew_lease(conn, worker.name, lease_seconds=60, now=moment) == 1
        assert queue.reap_expired_leases(conn, now=moment) == []

    assert queue.get_job(conn, job.id).status is JobStatus.RUNNING


def test_nach_max_attempts_wird_der_job_failed(conn, windows, worker):
    job = new_job(conn, max_attempts=2)

    for runde in range(2):
        moment = NOON + timedelta(minutes=runde)
        queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=moment)
        queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=moment)
        queue.fail_job(conn, job.id, worker.name, "Ollama antwortet nicht", now=moment)

    endstand = queue.get_job(conn, job.id)
    assert endstand.status is JobStatus.FAILED
    assert "Ollama" in endstand.error
    assert endstand.finished_at is not None


def test_fehler_mit_verbleibenden_versuchen_geht_zurueck_in_die_queue(conn, windows, worker):
    job = new_job(conn, max_attempts=3)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    danach = queue.fail_job(conn, job.id, worker.name, "Verbindung weg", now=NOON)
    assert danach.status is JobStatus.QUEUED
    assert danach.assigned_worker is None


def test_reap_setzt_auf_failed_wenn_keine_versuche_mehr_uebrig(conn, windows, worker):
    job = new_job(conn, max_attempts=1)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    queue.reap_expired_leases(conn, now=NOON + timedelta(seconds=61))
    assert queue.get_job(conn, job.id).status is JobStatus.FAILED


def test_fremder_worker_kann_den_job_nicht_abschliessen(conn, windows, worker):
    """Ein Worker mit abgelaufenem Lease darf nichts mehr überschreiben."""
    job = new_job(conn)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)

    assert queue.complete_job(conn, job.id, "anderer-worker", {"text": "nope"}) is None
    assert queue.get_job(conn, job.id).status is JobStatus.RUNNING


def test_endzustand_ist_endgueltig(conn, windows, worker):
    job = new_job(conn)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)
    queue.complete_job(conn, job.id, worker.name, {"text": "fertig"}, now=NOON)

    assert queue.cancel_job(conn, job.id) is None
    assert queue.fail_job(conn, job.id, worker.name, "zu spät") is None
    assert queue.get_job(conn, job.id).status is JobStatus.DONE


def test_abbrechen_aus_queued(conn):
    job = new_job(conn)
    assert queue.cancel_job(conn, job.id, now=NOON).status is JobStatus.CANCELLED


def test_abgebrochener_job_wird_nicht_mehr_vergeben(conn, windows, worker):
    job = new_job(conn)
    queue.cancel_job(conn, job.id, now=NOON)
    assert queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON) is None


def test_logzeilen_sind_fortlaufend_nummeriert(conn):
    job = new_job(conn)
    queue.append_log(conn, job.id, LogLevel.INFO, "erste", now=NOON)
    queue.append_log(conn, job.id, LogLevel.ERROR, "zweite", now=NOON)

    zeilen = queue.get_logs(conn, job.id)
    assert [z.seq for z in zeilen] == [1, 2]
    assert zeilen[1].level is LogLevel.ERROR
    assert queue.get_logs(conn, job.id, after_seq=1)[0].message == "zweite"


def test_fehler_landet_im_joblog_nicht_im_nirvana(conn, windows, worker):
    job = new_job(conn, max_attempts=1)
    queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON)
    queue.start_job(conn, job.id, worker.name, lease_seconds=60, now=NOON)
    queue.fail_job(conn, job.id, worker.name, "Modell nicht gefunden", now=NOON)

    texte = " ".join(z.message for z in queue.get_logs(conn, job.id))
    assert "Modell nicht gefunden" in texte


def test_zaehlung_nach_status(conn, windows, worker):
    new_job(conn)
    zweiter = new_job(conn)
    queue.cancel_job(conn, zweiter.id, now=NOON)

    assert queue.count_by_status(conn) == {"queued": 1, "cancelled": 1}


def test_window_wird_beim_claim_beachtet(conn, windows, worker):
    """Ein Nacht-Job bleibt mittags liegen und läuft nachts an."""
    job = new_job(conn, window=JobWindow.NIGHT)

    assert queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=NOON) is None

    nachts = datetime(2026, 8, 25, 0, 30, tzinfo=UTC)  # 02:30 Berliner Zeit
    claimed = queue.claim_job(conn, worker, window_config=windows, lease_seconds=60, now=nachts)
    assert claimed.id == job.id
