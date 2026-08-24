"""Prüfung gegen den echten wyoming-whisper auf dem Homeserver.

Wird übersprungen, wenn der Dienst nicht erreichbar ist — also überall außer
dort, wo er tatsächlich läuft. Der Test steht hier, weil die Attrappe in
``test_stt.py`` nur das Protokoll prüft, das ich abgeschaut habe. Nur dieser
Test zeigt, ob ich es richtig abgeschaut habe.

Ausführen mit:  pytest tests/test_stt_echt.py --stt-host 127.0.0.1
(auf dem Homeserver, wo der Container auf localhost hört)
"""

from __future__ import annotations

import math
import socket
import struct

import pytest

from jarvis.audio import Aufnahme
from jarvis.stt import SttFehler, WyomingTranscriber


def erreichbar(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture
def echt(pytestconfig):
    host = pytestconfig.getoption("--stt-host")
    port = pytestconfig.getoption("--stt-port")
    if not erreichbar(host, port):
        pytest.skip(f"kein wyoming-whisper unter {host}:{port}")
    return WyomingTranscriber(host=host, port=port, language="de", timeout=120)


def test_stille_liefert_keinen_text(echt):
    """Zwei Sekunden Stille: Whisper antwortet, aber ohne Inhalt."""
    stille = Aufnahme(pcm=b"\x00\x00" * 32000, rate=16000, width=2, channels=1)
    with pytest.raises(SttFehler, match="nichts verstanden"):
        echt.transcribe(stille)


def test_hoehere_abtastrate_wird_angenommen(echt):
    """Der Container rechnet selbst um - darauf baut jarvis/audio.py auf."""
    frames = 44100
    werte = (int(8000 * math.sin(2 * math.pi * 220 * i / 44100)) for i in range(frames))
    pcm = b"".join(struct.pack("<h", w) for w in werte)
    ton = Aufnahme(pcm=pcm, rate=44100, width=2, channels=1)

    # Ein Sinuston ist keine Sprache. Erwartet wird deshalb entweder gar nichts
    # oder irgendein Wort - Hauptsache, das Format wird nicht abgelehnt.
    try:
        echt.transcribe(ton)
    except SttFehler as fehler:
        assert "nichts verstanden" in str(fehler)
