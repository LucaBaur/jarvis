"""Der Orchestrator unter gleichzeitigen Anfragen.

Hintergrund: Die erste Fassung teilte sich eine SQLite-Verbindung und schützte
sie mit einem ``threading.RLock``, genommen in einer FastAPI-Abhängigkeit mit
``yield``. Der Teil nach dem ``yield`` läuft aber nicht zwingend im selben
Threadpool-Thread wie der davor — die Sperre wurde aus dem falschen Thread
freigegeben, blieb hängen, und der Dienst antwortete nach wenigen Minuten gar
nicht mehr.

Der ``TestClient`` hat das nicht gezeigt, weil er Anfragen nacheinander
abarbeitet. Deshalb läuft hier ein echter Uvicorn mit echten parallelen
Anfragen.
"""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import uvicorn

from jarvis.config import Settings
from jarvis.main import create_app

TOKEN = "nebenlaeufig-token"
AUTH = {"X-Jarvis-Token": TOKEN}


def freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """Ein echter Uvicorn im Hintergrund-Thread."""
    port = freier_port()
    settings = Settings(
        _env_file=None, token=TOKEN, db=tmp_path / "nebenlaeufig.db", host="127.0.0.1", port=port
    )
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_level="warning"
    )
    dienst = uvicorn.Server(config)
    thread = threading.Thread(target=dienst.run, daemon=True)
    thread.start()

    basis = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=basis, timeout=10) as client:
        for _ in range(100):
            try:
                if client.get("/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            threading.Event().wait(0.1)
        else:  # pragma: no cover
            pytest.fail("Server ist nicht hochgekommen")

        yield client

    dienst.should_exit = True
    thread.join(timeout=10)


def test_viele_gleichzeitige_anfragen_blockieren_den_dienst_nicht(server):
    """Der Regressionstest zur festgefahrenen Sperre."""
    for i in range(5):
        server.post("/api/jobs", json={"prompt": f"Auftrag {i}"}, headers=AUTH)

    def eine_runde(_: int) -> list[int]:
        with httpx.Client(base_url=str(server.base_url), timeout=10) as c:
            return [
                c.get("/api/jobs", headers=AUTH).status_code,
                c.get("/healthz").status_code,
                c.post("/api/jobs", json={"prompt": "parallel"}, headers=AUTH).status_code,
            ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ergebnisse = list(pool.map(eine_runde, range(40)))

    codes = [code for runde in ergebnisse for code in runde]
    assert all(code in (200, 201) for code in codes), sorted(set(codes))


def test_dienst_antwortet_nach_der_last_immer_noch(server):
    """Ein hängengebliebenes Schloss zeigt sich erst danach."""

    def absetzen(i: int) -> int:
        with httpx.Client(base_url=str(server.base_url), timeout=10) as c:
            return c.post("/api/jobs", json={"prompt": f"Last {i}"}, headers=AUTH).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(absetzen, range(30)))

    # Der eigentliche Prüfpunkt: Der Dienst lebt noch.
    assert server.get("/healthz", timeout=5).status_code == 200
    assert len(server.get("/api/jobs", headers=AUTH, timeout=5).json()["jobs"]) == 30


def test_zwei_worker_ziehen_nie_denselben_job(server):
    """Der Claim muss auch über echte HTTP-Nebenläufigkeit sauber bleiben."""
    for name in ("worker-a", "worker-b"):
        server.post(
            "/api/workers/register",
            json={"name": name, "tags": [], "models": [], "vram_mb": 8192},
            headers=AUTH,
        )
    angelegt = {
        server.post("/api/jobs", json={"prompt": f"Job {i}"}, headers=AUTH).json()["id"]
        for i in range(10)
    }

    def claimen(name: str) -> list[str]:
        gezogen: list[str] = []
        with httpx.Client(base_url=str(server.base_url), timeout=10) as c:
            for _ in range(10):
                antwort = c.post(f"/api/workers/{name}/claim", headers=AUTH)
                if antwort.status_code == 200:
                    gezogen.append(antwort.json()["id"])
        return gezogen

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(claimen, ["worker-a", "worker-b"])

    zusammen = a + b
    assert len(zusammen) == len(set(zusammen)), "ein Job wurde doppelt vergeben"
    assert set(zusammen) == angelegt


def test_zeiten_werden_in_ortszeit_angezeigt(server):
    """In der Datenbank steht UTC, auf der Seite die Uhr an der Wand."""
    job = server.post("/api/jobs", json={"prompt": "Zeitprobe"}, headers=AUTH).json()

    angelegt_utc = datetime.fromisoformat(job["created_at"]).astimezone(UTC)
    erwartet = angelegt_utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M")

    seite = server.get(f"/jobs/{job['id']}", headers=AUTH).text
    assert erwartet in seite
    if angelegt_utc.strftime("%H:%M") != erwartet[-5:]:
        assert angelegt_utc.strftime("%d.%m.%Y %H:%M") not in seite
