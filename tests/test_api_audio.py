"""Tests für den Audio-Endpunkt.

Eine Sprachnachricht wird hochgeladen, transkribiert und als ganz normaler Job
in die Queue gelegt. Ab da unterscheidet sie sich durch nichts mehr von einem
getippten Auftrag — das ist die Absicht.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.audio import Aufnahme
from jarvis.main import create_app
from jarvis.stt import SttFehler
from tests.test_audio import baue_wav

TOKEN = "audio-token"
AUTH = {"X-Jarvis-Token": TOKEN}


class TranscriberAttrappe:
    """Erkennt immer denselben Satz und merkt sich, was sie bekommen hat."""

    def __init__(self, text: str = "Was steht heute Abend an?") -> None:
        self.text = text
        self.fehler: Exception | None = None
        self.bekommen: list[Aufnahme] = []

    def transcribe(self, aufnahme: Aufnahme) -> str:
        self.bekommen.append(aufnahme)
        if self.fehler:
            raise self.fehler
        return self.text


@pytest.fixture
def erkenner() -> TranscriberAttrappe:
    return TranscriberAttrappe()


@pytest.fixture
def client(tmp_path, monkeypatch, erkenner):
    monkeypatch.setenv("JARVIS_TOKEN", TOKEN)
    monkeypatch.setenv("JARVIS_DB", str(tmp_path / "audio.db"))
    app = create_app(transcriber=erkenner)
    with TestClient(app) as testclient:
        yield testclient


def lade_hoch(client, daten: bytes | None = None, dateiname: str = "aufnahme.wav", **felder):
    return client.post(
        "/api/jobs/audio",
        files={"audio": (dateiname, daten if daten is not None else baue_wav(frames=16000),
                         "audio/wav")},
        data=felder,
        headers=AUTH,
    )


class TestSprachauftrag:
    def test_aus_sprache_wird_ein_job(self, client, erkenner):
        antwort = lade_hoch(client)
        assert antwort.status_code == 201, antwort.text

        job = antwort.json()
        assert job["status"] == "queued"
        assert job["payload"]["prompt"] == "Was steht heute Abend an?"

    def test_herkunft_wird_vermerkt(self, client):
        """Damit man später sieht, was gesprochen und was getippt war."""
        job = lade_hoch(client).json()
        assert job["payload"]["quelle"] == "sprache"

    def test_die_aufnahme_kommt_richtig_an(self, client, erkenner):
        lade_hoch(client, baue_wav(rate=48000, frames=48000))
        aufnahme = erkenner.bekommen[0]
        assert aufnahme.rate == 48000
        assert aufnahme.channels == 1
        assert aufnahme.dauer_s == pytest.approx(1.0)

    def test_optionen_gelten_auch_hier(self, client):
        job = lade_hoch(client, priority="10", window="night", model="phi3:mini").json()
        assert job["priority"] == 10
        assert job["window"] == "night"
        assert job["model"] == "phi3:mini"

    def test_der_job_laeuft_wie_jeder_andere(self, client):
        """Ab der Queue gibt es keinen Unterschied mehr."""
        job = lade_hoch(client).json()
        client.post(
            "/api/workers/register",
            json={"name": "gpu-pc", "tags": [], "models": [], "vram_mb": 6144},
            headers=AUTH,
        )
        claim = client.post("/api/workers/gpu-pc/claim", headers=AUTH)
        assert claim.json()["id"] == job["id"]

    def test_das_transkript_steht_im_joblog(self, client):
        """Nachvollziehbar, was die Erkennung verstanden hat."""
        job = lade_hoch(client).json()
        zeilen = client.get(f"/api/jobs/{job['id']}/logs", headers=AUTH).json()["lines"]
        assert any("Was steht heute Abend an?" in z["message"] for z in zeilen)


class TestFehler:
    def test_ohne_token_kein_upload(self, client):
        antwort = client.post(
            "/api/jobs/audio", files={"audio": ("a.wav", baue_wav(), "audio/wav")}
        )
        assert antwort.status_code == 401

    def test_kaputte_datei_wird_klar_abgelehnt(self, client):
        antwort = lade_hoch(client, b"OggS das ist kein WAV")
        assert antwort.status_code == 400
        assert "WAV" in antwort.json()["detail"]

    def test_erkennung_aus_meldet_lesbaren_fehler(self, client, erkenner):
        erkenner.fehler = SttFehler("Die Spracherkennung ist nicht erreichbar.")
        antwort = lade_hoch(client)
        assert antwort.status_code == 503
        assert "erreichbar" in antwort.json()["detail"]

    def test_stille_legt_keinen_leeren_job_an(self, client, erkenner):
        erkenner.fehler = SttFehler("Die Spracherkennung hat nichts verstanden.")
        antwort = lade_hoch(client)

        assert antwort.status_code == 503
        assert client.get("/api/jobs", headers=AUTH).json()["jobs"] == []

    def test_ohne_datei_ist_es_ein_formfehler(self, client):
        assert client.post("/api/jobs/audio", headers=AUTH).status_code == 422


class TestOhneSpracherkennung:
    """Ist kein Whisper eingerichtet, muss der Rest trotzdem laufen."""

    @pytest.fixture
    def client_ohne(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_TOKEN", TOKEN)
        monkeypatch.setenv("JARVIS_DB", str(tmp_path / "ohne.db"))
        monkeypatch.setenv("JARVIS_STT_ENABLED", "false")
        with TestClient(create_app()) as testclient:
            yield testclient

    def test_endpunkt_sagt_deutlich_ab(self, client_ohne):
        antwort = lade_hoch(client_ohne)
        assert antwort.status_code == 503
        assert "abgeschaltet" in antwort.json()["detail"]

    def test_getippte_auftraege_gehen_weiter(self, client_ohne):
        antwort = client_ohne.post("/api/jobs", json={"prompt": "Hallo"}, headers=AUTH)
        assert antwort.status_code == 201
