"""Tests für die Spracherkennung über das Wyoming-Protokoll.

Getestet wird gegen einen nachgebauten Wyoming-Dienst im selben Prozess. Das
Protokoll ist am echten ``wyoming-whisper`` auf dem Homeserver abgeschaut und
dort auch geprüft: eine Zeile JSON pro Ereignis, danach optional ``data_length``
Bytes JSON und ``payload_length`` Bytes Audio.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading

import pytest

from jarvis.audio import Aufnahme
from jarvis.stt import SttFehler, WyomingTranscriber

PROBE = Aufnahme(pcm=b"\x00\x01" * 8000, rate=16000, width=2, channels=1)


class WyomingAttrappe(socketserver.ThreadingTCPServer):
    """Ein Wyoming-Dienst, der mitschreibt und antwortet, was man ihm sagt."""

    allow_reuse_address = True
    daemon_threads = True

    antwort: str | list | None = "Was steht heute an?"
    empfangen: list[dict]
    audio: bytes

    def __init__(self) -> None:
        self.empfangen = []
        self.audio = b""
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: WyomingAttrappe = self.server  # type: ignore[assignment]
        while zeile := self.rfile.readline():
            ereignis = json.loads(zeile)
            if laenge := ereignis.get("data_length"):
                ereignis["data"] = json.loads(self.rfile.read(laenge))
            if laenge := ereignis.get("payload_length"):
                server.audio += self.rfile.read(laenge)
            server.empfangen.append(ereignis)

            if ereignis["type"] == "audio-stop":
                if server.antwort is None:
                    return  # Verbindung zu, ohne je zu antworten
                if isinstance(server.antwort, list):
                    for ev in server.antwort:
                        self.wfile.write((json.dumps(ev) + "\n").encode())
                else:
                    self.wfile.write(
                        (
                            json.dumps(
                                {"type": "transcript", "data": {"text": server.antwort}}
                            )
                            + "\n"
                        ).encode()
                    )
                self.wfile.flush()
                return


@pytest.fixture
def wyoming():
    dienst = WyomingAttrappe()
    threading.Thread(target=dienst.serve_forever, daemon=True).start()
    yield dienst
    dienst.shutdown()
    dienst.server_close()


def transcriber(dienst: WyomingAttrappe, **kwargs) -> WyomingTranscriber:
    return WyomingTranscriber(host="127.0.0.1", port=dienst.port, **kwargs)


class TestErkennung:
    def test_text_kommt_zurueck(self, wyoming):
        assert transcriber(wyoming).transcribe(PROBE) == "Was steht heute an?"

    def test_text_wird_getrimmt(self, wyoming):
        wyoming.antwort = "  Hallo Welt  \n"
        assert transcriber(wyoming).transcribe(PROBE) == "Hallo Welt"

    def test_protokollreihenfolge_stimmt(self, wyoming):
        transcriber(wyoming).transcribe(PROBE)
        typen = [e["type"] for e in wyoming.empfangen]

        assert typen[0] == "transcribe"
        assert typen[1] == "audio-start"
        assert typen[-1] == "audio-stop"
        assert "audio-chunk" in typen

    def test_audio_kommt_vollstaendig_an(self, wyoming):
        transcriber(wyoming).transcribe(PROBE)
        assert wyoming.audio == PROBE.pcm

    def test_format_wird_mitgeschickt(self, wyoming):
        """Whisper rechnet selbst um, muss die Rate aber kennen."""
        transcriber(wyoming).transcribe(
            Aufnahme(pcm=b"\x00\x00" * 100, rate=48000, width=2, channels=1)
        )
        start = next(e for e in wyoming.empfangen if e["type"] == "audio-start")
        assert start["data"]["rate"] == 48000
        assert start["data"]["width"] == 2
        assert start["data"]["channels"] == 1

    def test_sprache_wird_gesetzt(self, wyoming):
        transcriber(wyoming, language="de").transcribe(PROBE)
        assert wyoming.empfangen[0]["data"]["language"] == "de"

    def test_grosse_aufnahme_wird_gestueckelt(self, wyoming):
        """Ein Ereignis mit einem Megabyte Nutzlast ist unhöflich."""
        gross = Aufnahme(pcm=b"\x00\x00" * 200_000, rate=16000, width=2, channels=1)
        transcriber(wyoming).transcribe(gross)

        stuecke = [e for e in wyoming.empfangen if e["type"] == "audio-chunk"]
        assert len(stuecke) > 1
        assert wyoming.audio == gross.pcm


class TestFehler:
    def test_dienst_nicht_erreichbar(self):
        with socket.socket() as s:  # ein Port, auf dem sicher nichts hört
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        erkenner = WyomingTranscriber(host="127.0.0.1", port=port, timeout=2)
        with pytest.raises(SttFehler, match="erreichbar"):
            erkenner.transcribe(PROBE)

    def test_dienst_antwortet_nicht(self, wyoming):
        wyoming.antwort = None
        with pytest.raises(SttFehler, match=r"Antwort|abgebrochen"):
            transcriber(wyoming, timeout=3).transcribe(PROBE)

    def test_leerer_text_ist_ein_fehler(self, wyoming):
        """Stille soll keinen leeren Auftrag anlegen."""
        wyoming.antwort = "   "
        with pytest.raises(SttFehler, match=r"nichts verstanden|kein Text"):
            transcriber(wyoming).transcribe(PROBE)

    def test_andere_ereignisse_werden_uebersprungen(self, wyoming):
        """Whisper schickt vor dem Transkript auch anderes."""
        wyoming.antwort = [
            {"type": "audio-start", "data": {"rate": 16000}},
            {"type": "info", "data": {"asr": []}},
            {"type": "transcript", "data": {"text": "endlich"}},
        ]
        assert transcriber(wyoming).transcribe(PROBE) == "endlich"
