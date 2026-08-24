"""Hochgeladene Audiodateien einlesen.

Nur WAV. Der Grund ist bodenständig: Auf dem Homeserver ist kein ffmpeg
installiert, und es nachzuinstallieren braucht ein Passwort. Ohne ffmpeg lässt
sich das webm/opus, das ein Browser von sich aus aufnimmt, nicht dekodieren.

Deshalb nimmt die Oberfläche über die Web-Audio-API selbst rohes PCM auf und
schickt WAV. Die Abtastrate ist dabei egal: ``wyoming-faster-whisper`` rechnet
selbst um, auch von 22050 oder 48000 Hz. Das ist am laufenden Container geprüft
und spart hier eine fehleranfällige Umrechnung.

Was bleibt: Stereo zu Mono mischen, und alles ablehnen, womit Whisper nichts
anfangen kann — mit einer Fehlermeldung, die sagt, was zu tun ist.
"""

from __future__ import annotations

import struct
import wave
from dataclasses import dataclass
from io import BytesIO

#: Länger als das nimmt der Server nicht an. Whisper braucht auf dem
#: Homeserver ungefähr Echtzeit; zehn Minuten Audio wären zehn Minuten
#: blockierte Anfrage.
MAX_SEKUNDEN = 600


class AudioFehler(ValueError):
    """Die Datei lässt sich nicht verwenden. Die Meldung geht an den Nutzer."""


@dataclass(frozen=True, slots=True)
class Aufnahme:
    """Rohes PCM samt Format, so wie Wyoming es erwartet."""

    pcm: bytes
    rate: int
    width: int
    channels: int

    @property
    def dauer_s(self) -> float:
        return len(self.pcm) / (self.rate * self.width * self.channels)


def lies_wav(daten: bytes, *, max_sekunden: int = MAX_SEKUNDEN) -> Aufnahme:
    """Liest eine WAV-Datei und liefert Mono-PCM mit 16 bit."""
    if not daten:
        raise AudioFehler("Die Datei ist leer.")

    try:
        with wave.open(BytesIO(daten), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as fehler:
        raise AudioFehler(
            "Das ist keine lesbare WAV-Datei. Jarvis nimmt nur WAV entgegen, "
            "weil auf dem Server kein ffmpeg läuft — die Aufnahme in der "
            f"Oberfläche liefert bereits das richtige Format. ({fehler})"
        ) from fehler

    if width != 2:
        raise AudioFehler(
            f"Die Datei hat {width * 8} bit pro Abtastwert, gebraucht werden 16 bit."
        )
    if not pcm:
        raise AudioFehler("Die Datei enthält keinen Ton.")

    aufnahme = Aufnahme(pcm=pcm, rate=rate, width=width, channels=channels)
    if aufnahme.dauer_s > max_sekunden:
        raise AudioFehler(
            f"Die Aufnahme ist mit {aufnahme.dauer_s:.0f} Sekunden zu lang "
            f"(höchstens {max_sekunden})."
        )

    return _zu_mono(aufnahme) if channels > 1 else aufnahme


def _zu_mono(aufnahme: Aufnahme) -> Aufnahme:
    """Mischt mehrere Kanäle zum Mittelwert zusammen.

    In reinem Python, weil ``audioop`` seit Python 3.13 nicht mehr in der
    Standardbibliothek ist und der Server auf 3.14 läuft. Für ein paar
    Sekunden Sprache ist das schnell genug.
    """
    anzahl = aufnahme.channels
    werte = struct.unpack(f"<{len(aufnahme.pcm) // 2}h", aufnahme.pcm)
    gemischt = bytearray()
    for i in range(0, len(werte) - anzahl + 1, anzahl):
        # Ganzzahlige Mittelung: kann nicht überlaufen, weil jeder Summand
        # bereits in den 16-Bit-Bereich passt.
        gemischt += struct.pack("<h", sum(werte[i : i + anzahl]) // anzahl)

    return Aufnahme(
        pcm=bytes(gemischt), rate=aufnahme.rate, width=aufnahme.width, channels=1
    )
