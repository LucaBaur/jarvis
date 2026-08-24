"""Tests für den Worker: Konfiguration, Ollama-Runner und die Poll-Schleife.

Der Worker ist die Seite, die ausfallen darf. Getestet wird deshalb vor allem,
dass er sich bei Fehlern richtig verhält: keinen Job verschluckt, Fehler
zurückmeldet statt sie zu verschweigen, und einen kurzzeitig nicht erreichbaren
Orchestrator übersteht.
"""

from __future__ import annotations

import httpx
import pytest

from worker.agent import Agent
from worker.client import OrchestratorClient, OrchestratorUnreachable
from worker.config import WorkerConfig
from worker.runners.base import RunnerError
from worker.runners.ollama_runner import OllamaRunner

CONFIG = WorkerConfig(
    name="gpu-pc",
    orchestrator="http://localhost:8400",
    token="test-token",
    tags=["gpu", "coding"],
    models=["llama3.1:8b"],
    vram_mb=6144,
    default_model="llama3.1:8b",
    ollama_url="http://127.0.0.1:11434",
    poll_interval=1,
)


class TestKonfiguration:
    def test_aus_yaml_lesen(self, tmp_path):
        pfad = tmp_path / "worker.yaml"
        pfad.write_text(
            "name: gpu-pc\n"
            "orchestrator: http://192.168.8.125:8400\n"
            "token: geheim\n"
            "tags: [gpu, coding]\n"
            "models: [llama3.1:8b]\n"
            "vram_mb: 6144\n"
            "default_model: llama3.1:8b\n",
            encoding="utf-8",
        )
        config = WorkerConfig.from_yaml(pfad)
        assert config.name == "gpu-pc"
        assert config.orchestrator == "http://192.168.8.125:8400"
        assert config.tags == ["gpu", "coding"]

    def test_fehlende_pflichtfelder_fallen_frueh_auf(self, tmp_path):
        pfad = tmp_path / "worker.yaml"
        pfad.write_text("name: gpu-pc\n", encoding="utf-8")
        with pytest.raises(ValueError, match="orchestrator"):
            WorkerConfig.from_yaml(pfad)

    def test_registrierung_beschreibt_die_faehigkeiten(self):
        daten = CONFIG.as_registration()
        assert daten["name"] == "gpu-pc"
        assert daten["vram_mb"] == 6144
        assert daten["models"] == ["llama3.1:8b"]


class TestOllamaRunner:
    def runner(self, handler, **kwargs) -> OllamaRunner:
        transport = httpx.MockTransport(handler)
        return OllamaRunner(
            base_url="http://127.0.0.1:11434",
            default_model="llama3.1:8b",
            client=httpx.Client(transport=transport),
            **kwargs,
        )

    def test_antwort_wird_zurueckgegeben(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": "Hallo, wie geht es dir?"},
                    "model": "llama3.1:8b",
                    "eval_count": 12,
                },
            )

        zeilen: list[str] = []
        ergebnis = self.runner(handler).run({"payload": {"prompt": "Sag Hallo"}}, zeilen.append)

        assert ergebnis["text"] == "Hallo, wie geht es dir?"
        assert ergebnis["model"] == "llama3.1:8b"
        assert "duration_s" in ergebnis

    def test_systemprompt_wird_mitgeschickt(self):
        gesehen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            gesehen.update(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "ok"}, "model": "x"})

        self.runner(handler).run(
            {"payload": {"prompt": "Frage", "system": "Antworte knapp."}}, lambda _: None
        )
        rollen = [n["role"] for n in gesehen["messages"]]
        assert rollen == ["system", "user"]

    def test_modell_aus_dem_job_schlaegt_den_standard(self):
        gesehen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            gesehen.update(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": "ok"}, "model": "phi3:mini"})

        self.runner(handler).run(
            {"payload": {"prompt": "Frage"}, "model": "phi3:mini"}, lambda _: None
        )
        assert gesehen["model"] == "phi3:mini"

    def test_fehler_von_ollama_wird_zu_runnererror(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model 'gibtsnicht' not found"})

        with pytest.raises(RunnerError, match="not found"):
            self.runner(handler).run({"payload": {"prompt": "x"}}, lambda _: None)

    def test_ollama_nicht_erreichbar_wird_zu_runnererror(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(RunnerError, match="erreichbar"):
            self.runner(handler).run({"payload": {"prompt": "x"}}, lambda _: None)

    def test_leerer_prompt_wird_abgelehnt(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("Ollama darf gar nicht erst gefragt werden")

        with pytest.raises(RunnerError):
            self.runner(handler).run({"payload": {}}, lambda _: None)

    def test_verfuegbare_modelle_abfragen(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/tags"
            return httpx.Response(
                200, json={"models": [{"name": "llama3.1:8b"}, {"name": "phi3:mini"}]}
            )

        assert self.runner(handler).available_models() == ["llama3.1:8b", "phi3:mini"]

    def test_modellliste_bei_totem_ollama_ist_leer_statt_absturz(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        assert self.runner(handler).available_models() == []


class FakeClient:
    """Ein Orchestrator, der nur mitschreibt. Kein Netzwerk im Test."""

    def __init__(self, job: dict | None = None) -> None:
        self.job = job
        self.registered: list[dict] = []
        self.heartbeats = 0
        self.started: list[str] = []
        self.logs: list[tuple[str, str]] = []
        self.completed: list[tuple[str, dict]] = []
        self.failed: list[tuple[str, str]] = []

    def register(self, daten: dict) -> dict:
        self.registered.append(daten)
        return {"name": daten["name"], "state": "online"}

    def heartbeat(self, name: str) -> dict:
        self.heartbeats += 1
        return {"name": name, "state": "online"}

    def claim(self, name: str) -> dict | None:
        job, self.job = self.job, None
        return job

    def start(self, job_id: str, worker: str) -> dict | None:
        self.started.append(job_id)
        return {"id": job_id, "status": "running"}

    def log(self, job_id: str, worker: str, message: str, level: str = "info") -> None:
        self.logs.append((job_id, message))

    def complete(self, job_id: str, worker: str, result: dict) -> None:
        self.completed.append((job_id, result))

    def fail(self, job_id: str, worker: str, error: str) -> None:
        self.failed.append((job_id, error))


class FakeRunner:
    name = "chat"

    def __init__(self, antwort: dict | None = None, fehler: Exception | None = None) -> None:
        self.antwort = antwort or {"text": "Hallo"}
        self.fehler = fehler
        self.aufrufe = 0

    def run(self, job: dict, log) -> dict:
        self.aufrufe += 1
        log("Runner laeuft")
        if self.fehler:
            raise self.fehler
        return self.antwort


JOB = {"id": "job-1", "type": "chat", "payload": {"prompt": "Sag Hallo"}, "model": None}


class TestPollSchleife:
    def agent(self, client, runner=None) -> Agent:
        return Agent(CONFIG, client, {"chat": runner or FakeRunner()})

    def test_anmeldung_schickt_die_faehigkeiten(self):
        client = FakeClient()
        self.agent(client).register()
        assert client.registered[0]["name"] == "gpu-pc"
        assert client.registered[0]["vram_mb"] == 6144

    def test_leere_queue_bedeutet_nichts_zu_tun(self):
        client = FakeClient(job=None)
        assert self.agent(client).poll_once() is False
        assert client.heartbeats == 1

    def test_job_wird_abgearbeitet_und_gemeldet(self):
        client = FakeClient(job=JOB)
        runner = FakeRunner({"text": "Hallo zurueck"})

        assert self.agent(client, runner).poll_once() is True

        assert client.started == ["job-1"]
        assert client.completed == [("job-1", {"text": "Hallo zurueck"})]
        assert client.failed == []
        assert runner.aufrufe == 1

    def test_logzeilen_des_runners_gehen_an_den_orchestrator(self):
        client = FakeClient(job=JOB)
        self.agent(client).poll_once()
        assert ("job-1", "Runner laeuft") in client.logs

    def test_fehler_im_runner_wird_gemeldet_statt_verschluckt(self):
        client = FakeClient(job=JOB)
        runner = FakeRunner(fehler=RunnerError("Modell nicht gefunden"))

        assert self.agent(client, runner).poll_once() is True

        assert client.completed == []
        assert client.failed == [("job-1", "Modell nicht gefunden")]

    def test_unerwarteter_absturz_im_runner_wird_auch_gemeldet(self):
        """Ein ZeroDivisionError darf den Worker nicht mitreissen."""
        client = FakeClient(job=JOB)
        runner = FakeRunner(fehler=ZeroDivisionError("boom"))

        assert self.agent(client, runner).poll_once() is True
        assert len(client.failed) == 1
        assert "boom" in client.failed[0][1]

    def test_unbekannter_jobtyp_wird_als_fehler_gemeldet(self):
        client = FakeClient(job={"id": "job-2", "type": "video", "payload": {}})
        assert self.agent(client).poll_once() is True
        assert "video" in client.failed[0][1]

    def test_orchestrator_kurz_weg_beendet_den_worker_nicht(self):
        """Der Server startet neu, der Worker soll weiterlaufen."""

        class Wackelig(FakeClient):
            def heartbeat(self, name: str) -> dict:
                raise OrchestratorUnreachable("connection refused")

        client = Wackelig(job=JOB)
        assert self.agent(client).poll_once() is False
        assert client.failed == []


class TestOrchestratorClient:
    def client(self, handler) -> OrchestratorClient:
        return OrchestratorClient(
            "http://localhost:8400",
            "test-token",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    def test_token_wird_mitgeschickt(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Jarvis-Token"] == "test-token"
            return httpx.Response(200, json={"name": "gpu-pc", "state": "online"})

        self.client(handler).heartbeat("gpu-pc")

    def test_claim_ohne_arbeit_liefert_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        assert self.client(handler).claim("gpu-pc") is None

    def test_claim_mit_arbeit_liefert_den_job(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=JOB)

        assert self.client(handler).claim("gpu-pc")["id"] == "job-1"

    def test_netzwerkfehler_wird_zu_orchestratorunreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(OrchestratorUnreachable):
            self.client(handler).claim("gpu-pc")

    def test_falsches_token_ist_ein_klarer_fehler(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Token fehlt oder ist falsch"})

        with pytest.raises(OrchestratorUnreachable, match="Token"):
            self.client(handler).claim("gpu-pc")
