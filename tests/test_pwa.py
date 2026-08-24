"""Tests für die Installierbarkeit als App.

Ob ein Browser „Installieren" anbietet, hängt an einer Handvoll Bedingungen,
die alle still scheitern: fehlendes Symbol in der richtigen Größe, falscher
``display``-Wert, ein Service Worker ohne ``fetch``-Behandlung. Man merkt es
erst daran, dass der Menüpunkt nicht auftaucht — ohne jede Fehlermeldung.

Nicht prüfbar ist hier die wichtigste Bedingung: Die Seite muss über HTTPS oder
localhost geöffnet werden. Das hängt an der Adresse, nicht am Code.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.main import create_app

STATIC = Path(__file__).parent.parent / "jarvis" / "ui" / "static"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "pwa-token")
    monkeypatch.setenv("JARVIS_DB", str(tmp_path / "pwa.db"))
    with TestClient(create_app()) as testclient:
        yield testclient


@pytest.fixture
def manifest(client) -> dict:
    antwort = client.get("/static/manifest.webmanifest")
    assert antwort.status_code == 200
    return json.loads(antwort.content)


def png_masse(name: str) -> tuple[int, int]:
    """Breite und Höhe direkt aus dem PNG-Kopf."""
    kopf = (STATIC / name).read_bytes()[:24]
    assert kopf[:8] == b"\x89PNG\r\n\x1a\n", f"{name} ist kein PNG"
    return struct.unpack(">II", kopf[16:24])


class TestManifest:
    def test_pflichtangaben(self, manifest):
        assert manifest["name"]
        assert manifest["short_name"]
        assert manifest["start_url"] == "/"

    def test_eigenes_fenster_statt_browsertab(self, manifest):
        """Ohne standalone gibt es keinen Installieren-Knopf."""
        assert manifest["display"] == "standalone"

    def test_symbol_in_192_und_512(self, manifest):
        groessen = {s["sizes"] for s in manifest["icons"] if s["type"] == "image/png"}
        assert "192x192" in groessen
        assert "512x512" in groessen

    def test_maskierbares_symbol_fuer_android(self, manifest):
        """Sonst klebt Android das Symbol in einen weißen Kreis."""
        assert any("maskable" in s.get("purpose", "") for s in manifest["icons"])

    def test_farben_passen_zur_oberflaeche(self, manifest):
        assert manifest["theme_color"] == "#12151a"
        assert manifest["background_color"] == "#12151a"


class TestSymboldateien:
    @pytest.mark.parametrize(
        ("name", "erwartet"),
        [
            ("icon-192.png", (192, 192)),
            ("icon-512.png", (512, 512)),
            ("icon-maskable-512.png", (512, 512)),
            ("apple-touch-icon.png", (180, 180)),
        ],
    )
    def test_masse_stimmen(self, name, erwartet):
        assert png_masse(name) == erwartet

    def test_alle_im_manifest_genannten_dateien_gibt_es(self, manifest, client):
        for symbol in manifest["icons"]:
            assert client.get(symbol["src"]).status_code == 200, symbol["src"]


class TestSeite:
    def test_manifest_ist_verlinkt(self, client):
        seite = client.get("/login").text
        assert 'rel="manifest"' in seite

    def test_ios_angaben_sind_da(self, client):
        """iOS wertet das Manifest nicht aus und braucht eigene Angaben."""
        seite = client.get("/login").text
        assert 'rel="apple-touch-icon"' in seite
        assert 'name="apple-mobile-web-app-capable"' in seite

    def test_serviceworker_wird_registriert(self, client):
        assert "serviceWorker" in client.get("/login").text

    def test_serviceworker_behandelt_anfragen(self, client):
        """Ohne fetch-Behandlung gilt die Seite nicht als installierbar."""
        skript = client.get("/static/sw.js").text
        assert 'addEventListener("fetch"' in skript

    def test_serviceworker_speichert_keine_jobdaten(self, client):
        """Ein zwischengespeicherter Jobstatus wäre schlimmer als keiner."""
        skript = client.get("/static/sw.js").text
        assert "/static/" in skript
        assert "/api/" not in skript
