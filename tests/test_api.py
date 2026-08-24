"""Tests für die REST-Schnittstelle.

Deckt beide Seiten ab: die Endpunkte, die ich vom Handy aus benutze, und die,
die ein Worker anspricht.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.main import create_app

TOKEN = "test-token"
AUTH = {"X-Jarvis-Token": TOKEN}

GPU_PC = {
    "name": "gpu-pc",
    "tags": ["gpu", "coding"],
    "models": ["llama3.1:8b"],
    "vram_mb": 6144,
    "wake_strategy": "always_on",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", TOKEN)
    monkeypatch.setenv("JARVIS_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("JARVIS_LEASE_SECONDS", "60")
    monkeypatch.setenv("JARVIS_HEARTBEAT_TIMEOUT", "90")
    app = create_app()
    with TestClient(app) as testclient:
        yield testclient


def absetzen(client, **kwargs) -> dict:
    payload = {"prompt": "Sag Hallo"} | kwargs
    antwort = client.post("/api/jobs", json=payload, headers=AUTH)
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


class TestToken:
    def test_ohne_token_kein_zugriff(self, client):
        assert client.get("/api/jobs").status_code == 401

    def test_falscher_token_wird_abgewiesen(self, client):
        antwort = client.get("/api/jobs", headers={"X-Jarvis-Token": "falsch"})
        assert antwort.status_code == 401

    def test_healthz_braucht_keinen_token(self, client):
        """Damit sich der Dienst überwachen lässt, ohne das Geheimnis zu streuen."""
        antwort = client.get("/healthz")
        assert antwort.status_code == 200
        assert antwort.json()["status"] == "ok"

    def test_mit_token_geht_es(self, client):
        assert client.get("/api/jobs", headers=AUTH).status_code == 200


class TestJobsAbsetzen:
    def test_job_anlegen_liefert_queued(self, client):
        job = absetzen(client, prompt="Was steht als Naechstes an?")
        assert job["status"] == "queued"
        assert job["payload"]["prompt"] == "Was steht als Naechstes an?"
        assert job["assigned_worker"] is None

    def test_leerer_prompt_wird_abgelehnt(self, client):
        antwort = client.post("/api/jobs", json={"prompt": ""}, headers=AUTH)
        assert antwort.status_code == 422

    def test_unbekanntes_zeitfenster_wird_abgelehnt(self, client):
        antwort = client.post(
            "/api/jobs", json={"prompt": "x", "window": "uebermorgen"}, headers=AUTH
        )
        assert antwort.status_code == 422

    def test_optionale_felder_werden_uebernommen(self, client):
        job = absetzen(
            client,
            model="llama3.1:8b",
            priority=10,
            window="night",
            required_tags=["gpu"],
            min_vram_mb=4096,
        )
        assert job["model"] == "llama3.1:8b"
        assert job["priority"] == 10
        assert job["window"] == "night"
        assert job["required_tags"] == ["gpu"]

    def test_job_bleibt_ohne_worker_liegen(self, client):
        """Die Kernanforderung: Es laeuft auch, wenn die GPU aus ist."""
        job = absetzen(client)
        nachher = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
        assert nachher["status"] == "queued"

    def test_liste_und_statusfilter(self, client):
        absetzen(client, prompt="eins")
        zwei = absetzen(client, prompt="zwei")
        client.post(f"/api/jobs/{zwei['id']}/cancel", headers=AUTH)

        alle = client.get("/api/jobs", headers=AUTH).json()
        offen = client.get("/api/jobs", params={"status": "queued"}, headers=AUTH).json()

        assert len(alle["jobs"]) == 2
        assert [j["payload"]["prompt"] for j in offen["jobs"]] == ["eins"]

    def test_unbekannter_job_ist_404(self, client):
        assert client.get("/api/jobs/gibtesnicht", headers=AUTH).status_code == 404

    def test_abbrechen(self, client):
        job = absetzen(client)
        antwort = client.post(f"/api/jobs/{job['id']}/cancel", headers=AUTH)
        assert antwort.status_code == 200
        assert antwort.json()["status"] == "cancelled"

    def test_abgeschlossenen_job_abbrechen_ist_409(self, client):
        job = absetzen(client)
        client.post(f"/api/jobs/{job['id']}/cancel", headers=AUTH)
        assert client.post(f"/api/jobs/{job['id']}/cancel", headers=AUTH).status_code == 409


class TestWorkerProtokoll:
    def test_registrieren_und_heartbeat(self, client):
        antwort = client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        assert antwort.status_code == 200
        assert antwort.json()["state"] == "online"

        schlag = client.post("/api/workers/gpu-pc/heartbeat", headers=AUTH)
        assert schlag.status_code == 200

    def test_heartbeat_ohne_registrierung_ist_404(self, client):
        assert client.post("/api/workers/fremd/heartbeat", headers=AUTH).status_code == 404

    def test_claim_ohne_passenden_job_liefert_204(self, client):
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        assert client.post("/api/workers/gpu-pc/claim", headers=AUTH).status_code == 204

    def test_kompletter_durchlauf(self, client):
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        job = absetzen(client, prompt="Sag Hallo")

        claim = client.post("/api/workers/gpu-pc/claim", headers=AUTH)
        assert claim.status_code == 200
        assert claim.json()["id"] == job["id"]
        assert claim.json()["status"] == "assigned"

        start = client.post(
            f"/api/jobs/{job['id']}/start", json={"worker": "gpu-pc"}, headers=AUTH
        )
        assert start.json()["status"] == "running"

        client.post(
            f"/api/jobs/{job['id']}/log",
            json={"worker": "gpu-pc", "message": "Modell geladen", "level": "info"},
            headers=AUTH,
        )
        fertig = client.post(
            f"/api/jobs/{job['id']}/complete",
            json={"worker": "gpu-pc", "result": {"text": "Hallo"}},
            headers=AUTH,
        )
        assert fertig.status_code == 200
        assert fertig.json()["status"] == "done"
        assert fertig.json()["result"]["text"] == "Hallo"

        logs = client.get(f"/api/jobs/{job['id']}/logs", headers=AUTH).json()
        assert any("Modell geladen" in z["message"] for z in logs["lines"])

    def test_worker_bekommt_keinen_job_der_nicht_passt(self, client):
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        absetzen(client, min_vram_mb=40000)
        assert client.post("/api/workers/gpu-pc/claim", headers=AUTH).status_code == 204

    def test_fehler_landet_im_job(self, client):
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        job = absetzen(client, max_attempts=1)
        client.post("/api/workers/gpu-pc/claim", headers=AUTH)
        client.post(f"/api/jobs/{job['id']}/start", json={"worker": "gpu-pc"}, headers=AUTH)

        antwort = client.post(
            f"/api/jobs/{job['id']}/fail",
            json={"worker": "gpu-pc", "error": "Ollama nicht erreichbar"},
            headers=AUTH,
        )
        assert antwort.json()["status"] == "failed"
        logs = client.get(f"/api/jobs/{job['id']}/logs", headers=AUTH).json()
        assert any("Ollama nicht erreichbar" in z["message"] for z in logs["lines"])

    def test_fremder_worker_kann_nicht_abschliessen(self, client):
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        job = absetzen(client)
        client.post("/api/workers/gpu-pc/claim", headers=AUTH)
        client.post(f"/api/jobs/{job['id']}/start", json={"worker": "gpu-pc"}, headers=AUTH)

        antwort = client.post(
            f"/api/jobs/{job['id']}/complete",
            json={"worker": "eindringling", "result": {"text": "nope"}},
            headers=AUTH,
        )
        assert antwort.status_code == 409

    def test_logs_lassen_sich_ab_einer_zeile_nachladen(self, client):
        """Die Oberflaeche holt nur neue Zeilen, nicht jedes Mal alles."""
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        job = absetzen(client)
        client.post("/api/workers/gpu-pc/claim", headers=AUTH)
        for text in ("eins", "zwei", "drei"):
            client.post(
                f"/api/jobs/{job['id']}/log",
                json={"worker": "gpu-pc", "message": text},
                headers=AUTH,
            )

        alle = client.get(f"/api/jobs/{job['id']}/logs", headers=AUTH).json()["lines"]
        rest = client.get(
            f"/api/jobs/{job['id']}/logs", params={"after": alle[0]["seq"]}, headers=AUTH
        ).json()["lines"]
        assert len(rest) == len(alle) - 1

    def test_workerliste(self, client):
        client.post("/api/workers/register", json=GPU_PC, headers=AUTH)
        workers = client.get("/api/workers", headers=AUTH).json()["workers"]
        assert [w["name"] for w in workers] == ["gpu-pc"]


class TestOberflaeche:
    """Ein Browser kann bei einer Navigation keinen Header setzen.

    Deshalb nimmt die Anmeldung das Token einmalig entgegen und legt es als
    Cookie ab. Die API akzeptiert beides, Header und Cookie.
    """

    def anmelden(self, client) -> None:
        antwort = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
        assert antwort.status_code == 303

    def test_startseite_ohne_anmeldung_leitet_zur_anmeldung(self, client):
        antwort = client.get("/", follow_redirects=False)
        assert antwort.status_code == 303
        assert antwort.headers["location"] == "/login"

    def test_anmeldeseite_ist_offen(self, client):
        antwort = client.get("/login")
        assert antwort.status_code == 200
        assert "text/html" in antwort.headers["content-type"]

    def test_falsches_token_meldet_nicht_an(self, client):
        antwort = client.post("/login", data={"token": "falsch"}, follow_redirects=False)
        assert antwort.status_code == 401
        assert "jarvis_token" not in antwort.cookies

    def test_startseite_nach_anmeldung(self, client):
        self.anmelden(client)
        antwort = client.get("/")
        assert antwort.status_code == 200
        assert "text/html" in antwort.headers["content-type"]

    def test_cookie_gilt_auch_fuer_die_api(self, client):
        self.anmelden(client)
        assert client.get("/api/jobs").status_code == 200

    def test_jobdetail_zeigt_den_prompt(self, client):
        self.anmelden(client)
        job = absetzen(client, prompt="Testfrage vom Handy")
        antwort = client.get(f"/jobs/{job['id']}")
        assert antwort.status_code == 200
        assert "Testfrage vom Handy" in antwort.text

    def test_unbekanntes_jobdetail_ist_404(self, client):
        self.anmelden(client)
        assert client.get("/jobs/gibtesnicht").status_code == 404

    def test_manifest_und_serviceworker_sind_offen(self, client):
        """Sonst kann der Browser die PWA nicht installieren."""
        manifest = client.get("/static/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["name"]
        assert client.get("/static/sw.js").status_code == 200
