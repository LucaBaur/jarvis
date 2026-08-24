"""Tests für das Einlesen hochgeladener Audiodateien.

Auf dem Homeserver ist kein ffmpeg installiert, und nachinstallieren geht dort
nicht ohne Passwort. Deshalb nimmt Jarvis nur WAV entgegen und verlässt sich
darauf, dass wyoming-faster-whisper selbst umrechnet — der Container nimmt auch
22050 Hz an, das ist am laufenden Dienst geprüft.

Was hier trotzdem passieren muss: Stereo zu Mono mischen, Formate ablehnen, die
Whisper nicht versteht, und bei allem eine Fehlermeldung liefern, mit der man
etwas anfangen kann.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from jarvis.audio import AudioFehler, lies_wav


def baue_wav(
    *,
    rate: int = 16000,
    channels: int = 1,
    width: int = 2,
    frames: int = 1600,
    muster: bytes | None = None,
) -> bytes:
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(muster if muster is not None else b"\x00" * (frames * channels * width))
    return puffer.getvalue()


class TestEinlesen:
    def test_mono_16k_geht_unveraendert_durch(self):
        aufnahme = lies_wav(baue_wav(frames=1600))
        assert aufnahme.rate == 16000
        assert aufnahme.channels == 1
        assert aufnahme.width == 2
        assert len(aufnahme.pcm) == 3200
        assert aufnahme.dauer_s == pytest.approx(0.1)

    def test_andere_abtastrate_bleibt_erhalten(self):
        """Whisper rechnet selbst um, also nicht daran herumdoktern."""
        aufnahme = lies_wav(baue_wav(rate=48000, frames=4800))
        assert aufnahme.rate == 48000
        assert aufnahme.dauer_s == pytest.approx(0.1)

    def test_stereo_wird_zu_mono_gemischt(self):
        # Zwei Kanäle: links 1000, rechts 3000. Erwartet wird der Mittelwert.
        rahmen = struct.pack("<hh", 1000, 3000) * 100
        aufnahme = lies_wav(baue_wav(channels=2, muster=rahmen))

        assert aufnahme.channels == 1
        assert len(aufnahme.pcm) == 200
        assert struct.unpack("<h", aufnahme.pcm[:2])[0] == 2000

    def test_mischen_laeuft_nicht_ueber(self):
        """Zwei laute Kanäle dürfen nicht in den negativen Bereich kippen."""
        rahmen = struct.pack("<hh", 32000, 32000) * 10
        aufnahme = lies_wav(baue_wav(channels=2, muster=rahmen))
        werte = struct.unpack(f"<{len(aufnahme.pcm) // 2}h", aufnahme.pcm)
        assert all(w == 32000 for w in werte)


class TestAbgelehnt:
    def test_kein_wav(self):
        with pytest.raises(AudioFehler, match="WAV"):
            lies_wav(b"OggS\x00\x02irgendwas")

    def test_leere_datei(self):
        with pytest.raises(AudioFehler):
            lies_wav(b"")

    def test_webm_nennt_den_grund(self):
        """Genau das schickt ein Browser ohne Zutun - die Meldung muss helfen."""
        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 40
        with pytest.raises(AudioFehler) as fehler:
            lies_wav(webm)
        assert "WAV" in str(fehler.value)

    def test_acht_bit_wird_abgelehnt(self):
        with pytest.raises(AudioFehler, match=r"16 bit|16-Bit"):
            lies_wav(baue_wav(width=1))

    def test_tonlose_datei(self):
        with pytest.raises(AudioFehler, match=r"leer|keinen Ton"):
            lies_wav(baue_wav(frames=0))

    def test_zu_lang(self):
        """Eine Stunde Audio soll den Server nicht minutenlang beschäftigen."""
        with pytest.raises(AudioFehler, match="lang"):
            lies_wav(baue_wav(frames=16000 * 700), max_sekunden=600)
