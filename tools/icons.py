"""Erzeugt die App-Symbole als PNG.

Ohne Pillow, nur mit der Standardbibliothek: Ein PNG ist ein Header, ein
zlib-komprimierter Block Bildzeilen und ein Abschluss. Für ein paar Kreise
lohnt keine Abhängigkeit, die man auf dem Server auch noch installieren müsste.

Aufrufen, wenn sich das Symbol ändert:

    python tools/icons.py

Die erzeugten Dateien liegen in jarvis/ui/static/ und sind eingecheckt, damit
niemand dieses Skript laufen lassen muss, um Jarvis zu starten.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ZIEL = Path(__file__).parent.parent / "jarvis" / "ui" / "static"

GRUND = (0x12, 0x15, 0x1A)
AKZENT = (0x6A, 0xA9, 0xFF)


def schreibe_png(pfad: Path, breite: int, hoehe: int, pixel: bytes) -> None:
    """Pixel sind RGBA, zeilenweise, ohne Filterbytes."""

    def chunk(art: bytes, daten: bytes) -> bytes:
        return (
            struct.pack(">I", len(daten))
            + art
            + daten
            + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)
        )

    # Vor jede Zeile gehoert ein Filterbyte (0 = kein Filter).
    zeilenlaenge = breite * 4
    roh = b"".join(
        b"\x00" + pixel[y * zeilenlaenge : (y + 1) * zeilenlaenge] for y in range(hoehe)
    )

    pfad.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(roh, 9))
        + chunk(b"IEND", b"")
    )


def zeichne(groesse: int, *, rand_anteil: float, ecken_anteil: float) -> bytes:
    """Das Motiv: ein Ring mit Punkt, wie das Favicon.

    ``rand_anteil`` haelt das Motiv von den Kanten weg. Fuer maskierbare
    Symbole braucht Android einen Sicherheitsabstand, weil es rund oder als
    Tropfen beschnitten wird.
    """
    mitte = (groesse - 1) / 2
    nutzbar = groesse * (1 - 2 * rand_anteil)
    ring_radius = nutzbar * 0.30
    ring_dicke = nutzbar * 0.065
    punkt_radius = nutzbar * 0.085
    ecke = groesse * ecken_anteil

    pixel = bytearray()
    for y in range(groesse):
        for x in range(groesse):
            # Abgerundete Ecken: ausserhalb wird durchsichtig.
            if ecke and _ausserhalb_der_ecke(x, y, groesse, ecke):
                pixel += b"\x00\x00\x00\x00"
                continue

            abstand = ((x - mitte) ** 2 + (y - mitte) ** 2) ** 0.5
            auf_dem_ring = abs(abstand - ring_radius) <= ring_dicke / 2
            im_punkt = abstand <= punkt_radius
            farbe = AKZENT if (auf_dem_ring or im_punkt) else GRUND
            pixel += bytes(farbe) + b"\xff"

    return bytes(pixel)


def _ausserhalb_der_ecke(x: int, y: int, groesse: int, ecke: float) -> bool:
    for mx, my in ((ecke, ecke), (groesse - ecke, ecke),
                   (ecke, groesse - ecke), (groesse - ecke, groesse - ecke)):
        innen_x = x < ecke if mx == ecke else x > groesse - ecke
        innen_y = y < ecke if my == ecke else y > groesse - ecke
        if innen_x and innen_y:
            return ((x - mx) ** 2 + (y - my) ** 2) ** 0.5 > ecke
    return False


def main() -> None:
    dateien = [
        # Normale Symbole: abgerundete Ecken, Motiv darf gross sein.
        ("icon-192.png", 192, 0.06, 0.20),
        ("icon-512.png", 512, 0.06, 0.20),
        # iOS schneidet selbst zu und mag keine Transparenz.
        ("apple-touch-icon.png", 180, 0.08, 0.0),
        # Android maskiert: Motiv klein halten, Flaeche randlos fuellen.
        ("icon-maskable-512.png", 512, 0.20, 0.0),
    ]
    for name, groesse, rand, ecken in dateien:
        pfad = ZIEL / name
        schreibe_png(pfad, groesse, groesse, zeichne(groesse, rand_anteil=rand,
                                                     ecken_anteil=ecken))
        print(f"{name}: {groesse}x{groesse}, {pfad.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
