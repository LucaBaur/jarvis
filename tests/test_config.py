"""Tests für die Konfiguration.

Wichtigster Punkt: Es darf kein Geheimnis im Code stehen, und ein vergessenes
Token darf nicht unbemerkt zu einem offenen Dienst führen.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from jarvis.config import Settings


def settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_werte_kommen_aus_der_umgebung(monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "aus-der-env")
    monkeypatch.setenv("JARVIS_PORT", "9999")
    geladen = Settings(_env_file=None)
    assert geladen.token == "aus-der-env"
    assert geladen.port == 9999


def test_nachtfenster_wird_geparst():
    geladen = settings(night_start="23:00", night_end="07:00")
    assert geladen.night_from == time(23, 0)
    assert geladen.night_to == time(7, 0)


def test_relativer_datenbankpfad_wird_absolut():
    assert Path(settings(db=Path("data/jarvis.db")).db).is_absolute()


def test_absoluter_datenbankpfad_bleibt(tmp_path):
    ziel = tmp_path / "woanders.db"
    assert settings(db=ziel).db == ziel


def test_zeitzone_wird_aufgeloest():
    assert settings(tz="Europe/Berlin").zone.key == "Europe/Berlin"


def test_unsinnige_lease_dauer_wird_abgelehnt():
    with pytest.raises(ValueError, match="lease_seconds"):
        settings(lease_seconds=1)


def test_standardtoken_wird_als_unsicher_erkannt():
    """Damit der Start warnen kann, statt still ein offenes Tor zu lassen."""
    assert settings(token="dev-token").token_is_default is True
    assert settings(token="etwas-langes-und-zufaelliges").token_is_default is False
