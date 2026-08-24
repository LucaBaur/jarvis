"""Gemeinsame Abhängigkeiten für API und Oberfläche.

Liegen in einem eigenen Modul, damit ``api.py`` und ``main.py`` sie beide
benutzen können, ohne sich gegenseitig zu importieren.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from jarvis.config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Reicht die eine Verbindung durch, jeweils unter Sperre.

    FastAPI führt synchrone Endpunkte in einem Threadpool aus. Statt einer
    Verbindung pro Anfrage gibt es eine einzige, deren Zugriffe serialisiert
    werden. Bei ein paar Anfragen pro Minute kostet das nichts und macht die
    Nebenläufigkeit trivial nachvollziehbar.
    """
    with request.app.state.db_lock:
        yield request.app.state.conn
