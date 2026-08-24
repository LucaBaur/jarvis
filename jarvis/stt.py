"""Spracherkennung über das Wyoming-Protokoll.

Auf dem Homeserver läuft bereits ``wyoming-whisper`` als Docker-Container für
die Assist-Pipeline von Home Assistant. Jarvis spricht denselben Dienst an,
statt eine zweite Transkription danebenzustellen: Er ist da, er kostet nichts,
und er hat kein Nutzungslimit.

Das Protokoll ist schlicht — eine Zeile JSON je Ereignis, danach optional
``data_length`` Bytes JSON und ``payload_length`` Bytes Audio. Deshalb steht es
hier direkt statt über die ``wyoming``-Bibliothek: eine Abhängigkeit weniger auf
einem Server, auf dem schon das Anlegen eines venv umständlich ist.

Der Ablauf einer Erkennung:

    transcribe -> audio-start -> audio-chunk* -> audio-stop -> transcript

``Transcriber`` ist bewusst ein Protokoll. Wird die CPU des Homeservers zu
langsam, tritt später ``faster-whisper`` auf der GPU des PCs als zweite
Umsetzung dahinter, ohne dass die API sich ändert.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Protocol

from jarvis.audio import Aufnahme

log = logging.getLogger("jarvis.stt")

#: Wie viel Audio in ein einzelnes Ereignis geht. 32 KB sind etwa eine Sekunde
#: bei 16 kHz und halten die Nachrichten handlich.
CHUNK_BYTES = 32_000


class SttFehler(RuntimeError):
    """Die Erkennung hat nicht geklappt. Die Meldung geht an den Nutzer."""


class Transcriber(Protocol):
    def transcribe(self, aufnahme: Aufnahme) -> str: ...


class WyomingTranscriber:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10300,
        *,
        language: str = "de",
        timeout: int = 300,
    ) -> None:
        self.host = host
        self.port = port
        self.language = language
        # Grosszuegig: Whisper braucht auf dem Homeserver ungefaehr so lange,
        # wie die Aufnahme dauert.
        self.timeout = timeout

    def transcribe(self, aufnahme: Aufnahme) -> str:
        ziel = f"{self.host}:{self.port}"
        try:
            verbindung = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
        except OSError as fehler:
            raise SttFehler(
                f"Die Spracherkennung unter {ziel} ist nicht erreichbar: {fehler}. "
                "Läuft der Container wyoming-whisper?"
            ) from fehler

        try:
            with verbindung:
                verbindung.settimeout(self.timeout)
                self._sende_aufnahme(verbindung, aufnahme)
                text = self._lies_transkript(verbindung)
        except SttFehler:
            raise
        except OSError as fehler:
            raise SttFehler(
                f"Die Verbindung zur Spracherkennung ({ziel}) wurde abgebrochen: {fehler}"
            ) from fehler

        if not text.strip():
            raise SttFehler(
                "Die Spracherkennung hat nichts verstanden. Zu leise, zu kurz "
                "oder nur Stille?"
            )
        return text.strip()

    # --- Protokoll ------------------------------------------------------

    def _sende_aufnahme(self, verbindung: socket.socket, aufnahme: Aufnahme) -> None:
        format_ = {
            "rate": aufnahme.rate,
            "width": aufnahme.width,
            "channels": aufnahme.channels,
        }
        _sende(verbindung, "transcribe", {"language": self.language})
        _sende(verbindung, "audio-start", dict(format_, timestamp=0))
        for anfang in range(0, len(aufnahme.pcm), CHUNK_BYTES):
            _sende(
                verbindung,
                "audio-chunk",
                dict(format_, timestamp=0),
                aufnahme.pcm[anfang : anfang + CHUNK_BYTES],
            )
        _sende(verbindung, "audio-stop", {"timestamp": 0})
        log.info(
            "%.1f s Audio an %s:%s geschickt", aufnahme.dauer_s, self.host, self.port
        )

    def _lies_transkript(self, verbindung: socket.socket) -> str:
        strom = verbindung.makefile("rb")
        while (ereignis := _lies(strom)) is not None:
            if ereignis["type"] == "transcript":
                return (ereignis.get("data") or {}).get("text", "")
            # audio-start, info und Ähnliches kommen vorher und sind egal.
        raise SttFehler(
            "Die Spracherkennung hat keine Antwort geschickt. Der Container "
            "wyoming-whisper ist vermutlich überlastet oder abgestürzt."
        )


def _sende(
    verbindung: socket.socket,
    typ: str,
    data: dict[str, Any] | None = None,
    payload: bytes | None = None,
) -> None:
    ereignis: dict[str, Any] = {"type": typ}
    if data:
        ereignis["data"] = data
    if payload:
        ereignis["payload_length"] = len(payload)
    verbindung.sendall((json.dumps(ereignis) + "\n").encode())
    if payload:
        verbindung.sendall(payload)


def _lies(strom: Any) -> dict[str, Any] | None:
    zeile = strom.readline()
    if not zeile:
        return None
    ereignis = json.loads(zeile)
    if laenge := ereignis.get("data_length"):
        ereignis["data"] = json.loads(strom.read(laenge))
    if laenge := ereignis.get("payload_length"):
        strom.read(laenge)
    return ereignis
