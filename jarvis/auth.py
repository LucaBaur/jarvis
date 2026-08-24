"""Token-Prüfung.

Ein einziges gemeinsames Geheimnis, kein Nutzerkonto, keine Sitzungen. Jarvis
läuft im LAN und im Tailnet; das Token soll verhindern, dass ein versehentlich
offener Port sofort benutzbar ist.

Es wird an zwei Stellen akzeptiert: im Header ``X-Jarvis-Token`` für Worker und
Skripte, und im Cookie ``jarvis_token`` für den Browser, der bei einer normalen
Navigation keinen Header setzen kann.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

TOKEN_HEADER = "X-Jarvis-Token"
TOKEN_COOKIE = "jarvis_token"
#: Ein Jahr. Es gibt einen Nutzer und kein Sitzungskonzept.
COOKIE_MAX_AGE = 365 * 24 * 3600


def token_matches(candidate: str | None, expected: str) -> bool:
    """Vergleich in konstanter Zeit, damit die Laufzeit nichts verrät."""
    if not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


def presented_token(
    x_jarvis_token: Annotated[str | None, Header()] = None,
    jarvis_token: Annotated[str | None, Cookie()] = None,
) -> str | None:
    return x_jarvis_token or jarvis_token


def require_token(
    request: Request,
    candidate: Annotated[str | None, Depends(presented_token)],
) -> None:
    """Abhängigkeit für alle geschützten API-Endpunkte."""
    expected = request.app.state.settings.token
    if not token_matches(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token fehlt oder ist falsch",
        )


def browser_is_logged_in(request: Request) -> bool:
    """Für die HTML-Seiten: angemeldet oder nicht, ohne Ausnahme zu werfen."""
    candidate = request.cookies.get(TOKEN_COOKIE) or request.headers.get(TOKEN_HEADER)
    return token_matches(candidate, request.app.state.settings.token)
