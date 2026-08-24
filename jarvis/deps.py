"""Gemeinsame Abhängigkeiten für API und Oberfläche.

Liegen in einem eigenen Modul, damit ``api.py`` und ``main.py`` sie beide
benutzen können, ohne sich gegenseitig zu importieren.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from jarvis import db
from jarvis.config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Eine frische Verbindung pro Anfrage, danach zu.

    Bewusst keine geteilte Verbindung mit Sperre: FastAPI führt synchrone
    Endpunkte im Threadpool aus, und bei einer Abhängigkeit mit ``yield``
    kann der Teil nach dem ``yield`` in einem anderen Thread laufen als der
    davor. Ein dort freigegebener ``threading.Lock`` bleibt hängen, und der
    Dienst antwortet irgendwann gar nicht mehr.

    SQLite im WAL-Modus verträgt mehrere Verbindungen problemlos; das
    Schema steht schon, deshalb ``init=False``.
    """
    conn = db.connect(request.app.state.settings.db, init=False)
    try:
        yield conn
    finally:
        conn.close()
