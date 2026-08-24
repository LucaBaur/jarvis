"""Durchstich: echter Worker gegen echten Orchestrator.

Nur Ollama ist gefälscht. Alles andere ist der Code, der später auch auf dem
Server und auf dem PC läuft — inklusive HTTP, Token und SQLite-Datei.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.main import create_app
from worker.agent import Agent
from worker.client import OrchestratorClient
from worker.config import WorkerConfig
from worker.runners.ollama_runner import OllamaRunner

TOKEN = "integrations-token"
AUTH = {"X-Jarvis-Token": TOKEN}


@pytest.fixture
def orchestrator(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", TOKEN)
    monkeypatch.setenv("JARVIS_DB", str(tmp_path / "integration.db"))
    monkeypatch.setenv("JARVIS_LEASE_SECONDS", "60")
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def ollama():
    """Ein Ollama, das immer dieselbe Antwort gibt."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        return httpx.Response(
            200,
            json={"message": {"content": "Heute ist Training um 20 Uhr."}, "model": "llama3.1:8b"},
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def baue_agent(orchestrator: TestClient, ollama: httpx.Client) -> Agent:
    config = WorkerConfig(
        name="gpu-pc",
        orchestrator="http://testserver",
        token=TOKEN,
        tags=["gpu"],
        models=["llama3.1:8b"],
        vram_mb=6144,
        default_model="llama3.1:8b",
        ollama_url="http://127.0.0.1:11434",
    )
    client = OrchestratorClient("http://testserver", TOKEN, client=orchestrator)
    runner = OllamaRunner("http://127.0.0.1:11434", "llama3.1:8b", client=ollama)
    return Agent(config, client, {"chat": runner})


def test_frage_absetzen_und_antwort_bekommen(orchestrator, ollama):
    agent = baue_agent(orchestrator, ollama)
    agent.register()

    angelegt = orchestrator.post(
        "/api/jobs", json={"prompt": "Was steht als Naechstes an?"}, headers=AUTH
    ).json()
    assert angelegt["status"] == "queued"

    assert agent.poll_once() is True

    fertig = orchestrator.get(f"/api/jobs/{angelegt['id']}", headers=AUTH).json()
    assert fertig["status"] == "done"
    assert fertig["result"]["text"] == "Heute ist Training um 20 Uhr."
    assert fertig["assigned_worker"] == "gpu-pc"

    logs = orchestrator.get(f"/api/jobs/{angelegt['id']}/logs", headers=AUTH).json()["lines"]
    assert len(logs) >= 2


def test_job_ueberlebt_einen_ausgefallenen_worker(orchestrator, ollama):
    """Der PC geht mitten im Job aus. Danach uebernimmt er ihn erneut."""
    agent = baue_agent(orchestrator, ollama)
    agent.register()

    job = orchestrator.post("/api/jobs", json={"prompt": "lange Aufgabe"}, headers=AUTH).json()
    orchestrator.post("/api/workers/gpu-pc/claim", headers=AUTH)
    orchestrator.post(f"/api/jobs/{job['id']}/start", json={"worker": "gpu-pc"}, headers=AUTH)

    # Ab hier meldet sich niemand mehr. Der Aufraeumer holt den Job zurueck.
    zurueck = orchestrator.post("/api/maintenance/reap", json={"force": True}, headers=AUTH).json()
    assert job["id"] in zurueck["requeued"]
    assert orchestrator.get(f"/api/jobs/{job['id']}", headers=AUTH).json()["status"] == "queued"

    assert agent.poll_once() is True
    assert orchestrator.get(f"/api/jobs/{job['id']}", headers=AUTH).json()["status"] == "done"


def test_worker_nimmt_nur_was_er_kann(orchestrator, ollama):
    agent = baue_agent(orchestrator, ollama)
    agent.register()

    zu_gross = orchestrator.post(
        "/api/jobs", json={"prompt": "riesig", "min_vram_mb": 40000}, headers=AUTH
    ).json()
    passend = orchestrator.post("/api/jobs", json={"prompt": "klein"}, headers=AUTH).json()

    assert agent.poll_once() is True
    assert agent.poll_once() is False

    assert orchestrator.get(f"/api/jobs/{passend['id']}", headers=AUTH).json()["status"] == "done"
    assert orchestrator.get(f"/api/jobs/{zu_gross['id']}", headers=AUTH).json()["status"] == "queued"


def test_ollama_aus_meldet_einen_lesbaren_fehler(orchestrator):
    def tot(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    agent = baue_agent(orchestrator, httpx.Client(transport=httpx.MockTransport(tot)))
    agent.register()

    job = orchestrator.post(
        "/api/jobs", json={"prompt": "Frage", "max_attempts": 1}, headers=AUTH
    ).json()
    agent.poll_once()

    ergebnis = orchestrator.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
    assert ergebnis["status"] == "failed"
    assert "erreichbar" in ergebnis["error"]

    logs = orchestrator.get(f"/api/jobs/{job['id']}/logs", headers=AUTH).json()["lines"]
    assert any("erreichbar" in z["message"] for z in logs)
