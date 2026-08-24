"""HTTP-Client zum Orchestrator.

Alles, was schiefgehen kann, wird zu ``OrchestratorUnreachable``. Der Worker
soll dann warten und es später erneut versuchen, statt sich zu beenden — der
Server startet gelegentlich neu, und das ist kein Grund, die GPU stillzulegen.
"""

from __future__ import annotations

from typing import Any

import httpx

from jarvis.auth import TOKEN_HEADER


class OrchestratorUnreachable(RuntimeError):
    """Der Orchestrator hat nicht oder falsch geantwortet."""


class OrchestratorClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # --- Worker ---------------------------------------------------------

    def register(self, registration: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/workers/register", json=registration)

    def heartbeat(self, name: str) -> dict[str, Any]:
        return self._request("POST", f"/api/workers/{name}/heartbeat")

    def claim(self, name: str) -> dict[str, Any] | None:
        """Fragt nach Arbeit. ``None`` heißt: nichts zu tun, kein Fehler."""
        return self._request("POST", f"/api/workers/{name}/claim", erlaube_leer=True)

    # --- Jobs -----------------------------------------------------------

    def start(self, job_id: str, worker: str) -> dict[str, Any]:
        return self._request("POST", f"/api/jobs/{job_id}/start", json={"worker": worker})

    def log(self, job_id: str, worker: str, message: str, level: str = "info") -> None:
        self._request(
            "POST",
            f"/api/jobs/{job_id}/log",
            json={"worker": worker, "message": message, "level": level},
        )

    def complete(self, job_id: str, worker: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/jobs/{job_id}/complete", json={"worker": worker, "result": result}
        )

    def fail(self, job_id: str, worker: str, error: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/jobs/{job_id}/fail", json={"worker": worker, "error": error}
        )

    # --- Innenleben -----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        erlaube_leer: bool = False,
    ) -> dict[str, Any] | None:
        try:
            antwort = self._client.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                headers={TOKEN_HEADER: self._token},
            )
        except httpx.HTTPError as fehler:
            raise OrchestratorUnreachable(f"{self.base_url} nicht erreichbar: {fehler}") from fehler

        if antwort.status_code == httpx.codes.UNAUTHORIZED:
            raise OrchestratorUnreachable(
                "Token wurde abgelehnt. Stimmt JARVIS_TOKEN in der worker.yaml?"
            )
        if erlaube_leer and antwort.status_code == httpx.codes.NO_CONTENT:
            return None
        if antwort.is_error:
            raise OrchestratorUnreachable(
                f"{method} {path} kam mit {antwort.status_code} zurück: {antwort.text[:200]}"
            )
        if not antwort.content:
            return None
        return antwort.json()
