"""App-Factory, Oberfläche und Start.

Die Weboberfläche ist absichtlich schlicht: serverseitig gerendertes HTML mit
htmx für die Aktualisierung. Kein Build-Schritt, keine Abhängigkeit, die in
zwei Jahren nicht mehr installierbar ist.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jarvis import __version__, api, db, queue, registry
from jarvis.auth import COOKIE_MAX_AGE, TOKEN_COOKIE, browser_is_logged_in, token_matches
from jarvis.config import Settings
from jarvis.deps import get_db
from jarvis.stt import Transcriber, WyomingTranscriber

UI_DIR = Path(__file__).parent / "ui"
#: Wie oft im Hintergrund nach abgelaufenen Leases gesehen wird.
REAP_INTERVAL_SECONDS = 30

log = logging.getLogger("jarvis")

# Muss im Modul stehen, nicht in einer Funktion: durch
# ``from __future__ import annotations`` sind Annotationen Zeichenketten,
# und FastAPI löst sie nur gegen den Modul-Namensraum auf.
Db = Annotated[sqlite3.Connection, Depends(get_db)]


def create_app(
    settings: Settings | None = None, *, transcriber: Transcriber | None = None
) -> FastAPI:
    """Baut die App.

    ``transcriber`` laesst sich fuer Tests hereinreichen. Ohne Angabe wird
    der Wyoming-Dienst aus der Konfiguration genommen, sofern die
    Spracherkennung eingeschaltet ist.
    """
    settings = settings or Settings()
    templates = Jinja2Templates(directory=UI_DIR / "templates")

    def lokal(wert: datetime | None, muster: str = "%d.%m.%Y %H:%M") -> str:
        """Zeitstempel in Ortszeit ausgeben.

        In der Datenbank steht alles in UTC, weil der Homeserver in UTC
        laeuft. Angezeigt wird trotzdem, was die Uhr an der Wand sagt.
        """
        if wert is None:
            return ""
        return wert.astimezone(settings.zone).strftime(muster)

    templates.env.filters["lokal"] = lokal

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.transcriber = transcriber or _baue_transcriber(settings)
        # Einmalig das Schema anlegen. Danach oeffnet jede Anfrage ihre
        # eigene Verbindung (siehe deps.get_db).
        with contextlib.closing(db.connect(settings.db)) as conn:
            db.init_schema(conn)

        if settings.token_is_default:
            log.warning(
                "JARVIS_TOKEN steht noch auf dem Platzhalter aus .env.example. "
                "Der Dienst ist damit praktisch ungeschützt."
            )
        log.info("Jarvis %s bereit, Datenbank %s", __version__, settings.db)
        if app.state.transcriber is None:
            log.info("Spracherkennung aus, Sprachnachrichten werden abgelehnt")

        aufraeumer = asyncio.create_task(_reap_loop(app))
        try:
            yield
        finally:
            aufraeumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await aufraeumer

    app = FastAPI(title="Jarvis", version=__version__, lifespan=lifespan)
    app.include_router(api.router)
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
    _add_ui_routes(app, templates)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Ohne Token, damit sich der Dienst überwachen lässt."""
        return {"status": "ok", "version": __version__}

    return app


def _baue_transcriber(settings: Settings) -> Transcriber | None:
    if not settings.stt_enabled:
        return None
    return WyomingTranscriber(
        host=settings.stt_host,
        port=settings.stt_port,
        language=settings.stt_language,
        timeout=settings.stt_timeout,
    )


async def _reap_loop(app: FastAPI) -> None:
    """Holt regelmäßig Jobs zurück, deren Worker verschwunden ist."""
    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            with contextlib.closing(db.connect(app.state.settings.db, init=False)) as conn:
                zurueck = queue.reap_expired_leases(conn)
            if zurueck:
                log.warning("Lease abgelaufen, zurück in die Queue: %s", ", ".join(zurueck))
        except Exception:  # der Aufräumer darf nie sterben
            log.exception("Aufräumer ist gestolpert, macht weiter")


def _add_ui_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    def _require_login(request: Request) -> RedirectResponse | None:
        return None if browser_is_logged_in(request) else RedirectResponse("/login", 303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "login.html", {"fehler": None, "version": __version__}
        )

    @app.post("/login", response_model=None)
    def login(
        request: Request, token: Annotated[str, Form()] = ""
    ) -> HTMLResponse | RedirectResponse:
        if not token_matches(token, request.app.state.settings.token):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"fehler": "Token stimmt nicht.", "version": __version__},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        antwort = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        antwort.set_cookie(
            TOKEN_COOKIE,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return antwort

    @app.post("/logout")
    def logout() -> RedirectResponse:
        antwort = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        antwort.delete_cookie(TOKEN_COOKIE)
        return antwort

    @app.get("/", response_class=HTMLResponse, response_model=None)
    def dashboard(request: Request, conn: Db) -> HTMLResponse | RedirectResponse:
        if (weiter := _require_login(request)) is not None:
            return weiter
        settings = request.app.state.settings
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "jobs": queue.list_jobs(conn, limit=50),
                "workers": registry.list_workers(
                    conn, heartbeat_timeout=settings.heartbeat_timeout
                ),
                "zaehler": queue.count_by_status(conn),
                "sprache": request.app.state.transcriber is not None,
                "version": __version__,
            },
        )

    @app.get("/partials/jobs", response_class=HTMLResponse, response_model=None)
    def jobs_partial(request: Request, conn: Db) -> HTMLResponse | RedirectResponse:
        """Nur die Tabelle. htmx holt sie im Takt und tauscht sie aus."""
        if (weiter := _require_login(request)) is not None:
            return weiter
        return templates.TemplateResponse(
            request, "_jobs.html", {"jobs": queue.list_jobs(conn, limit=50)}
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse, response_model=None)
    def job_detail(request: Request, job_id: str, conn: Db) -> HTMLResponse | RedirectResponse:
        if (weiter := _require_login(request)) is not None:
            return weiter
        job = queue.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
        return templates.TemplateResponse(
            request,
            "job.html",
            {"job": job, "zeilen": queue.get_logs(conn, job_id), "version": __version__},
        )

    @app.get("/partials/jobs/{job_id}", response_class=HTMLResponse, response_model=None)
    def job_partial(request: Request, job_id: str, conn: Db) -> HTMLResponse | RedirectResponse:
        if (weiter := _require_login(request)) is not None:
            return weiter
        job = queue.get_job(conn, job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
        return templates.TemplateResponse(
            request, "_job_body.html", {"job": job, "zeilen": queue.get_logs(conn, job_id)}
        )


def main() -> None:
    """Einstiegspunkt für systemd und für den Start von Hand."""
    import uvicorn

    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
