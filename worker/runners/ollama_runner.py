"""Text-Prompts über Ollama.

Ollama ist hier bewusst hinter einer schmalen Schnittstelle versteckt. Kommt
später llama.cpp oder vLLM dazu, ist das ein weiterer Runner mit demselben
``run``, und am Rest ändert sich nichts.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from worker.runners.base import LogFn, RunnerError


class OllamaRunner:
    name = "chat"

    def __init__(
        self,
        base_url: str,
        default_model: str,
        *,
        client: httpx.Client | None = None,
        timeout: int = 4 * 3600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        # Lange Laufzeiten sind ausdrücklich erlaubt, deshalb der große Timeout.
        self._client = client or httpx.Client(timeout=timeout)

    def run(self, job: dict[str, Any], log: LogFn) -> dict[str, Any]:
        payload = job.get("payload") or {}
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            raise RunnerError("Der Job hat keinen Text, den das Modell beantworten könnte.")

        model = job.get("model") or self.default_model
        nachrichten: list[dict[str, str]] = []
        if payload.get("system"):
            nachrichten.append({"role": "system", "content": payload["system"]})
        nachrichten.append({"role": "user", "content": prompt})

        log(f"Frage {model} über {self.base_url}")
        begonnen = time.monotonic()

        try:
            antwort = self._client.post(
                f"{self.base_url}/api/chat",
                json={"model": model, "messages": nachrichten, "stream": False},
            )
        except httpx.HTTPError as fehler:
            raise RunnerError(
                f"Ollama unter {self.base_url} ist nicht erreichbar: {fehler}. "
                "Läuft der Dienst auf diesem Rechner?"
            ) from fehler

        if antwort.is_error:
            raise RunnerError(f"Ollama hat abgelehnt ({antwort.status_code}): {_grund(antwort)}")

        daten = antwort.json()
        text = (daten.get("message") or {}).get("content", "").strip()
        if not text:
            raise RunnerError("Ollama hat eine leere Antwort geschickt.")

        dauer = round(time.monotonic() - begonnen, 1)
        log(f"Antwort nach {dauer} s, {len(text)} Zeichen")
        return {
            "text": text,
            "model": daten.get("model", model),
            "duration_s": dauer,
            "eval_count": daten.get("eval_count"),
        }

    def available_models(self) -> list[str]:
        """Welche Modelle liegen auf diesem Rechner?

        Wird beim Start abgefragt, damit die Registrierung stimmt. Ein totes
        Ollama darf den Worker nicht am Start hindern — dann meldet er sich
        eben ohne Modelle und bekommt keine Jobs, die eines verlangen.
        """
        try:
            antwort = self._client.get(f"{self.base_url}/api/tags")
            antwort.raise_for_status()
        except httpx.HTTPError:
            return []
        return [m["name"] for m in antwort.json().get("models", []) if m.get("name")]


def _grund(antwort: httpx.Response) -> str:
    try:
        return antwort.json().get("error", antwort.text)
    except ValueError:
        return antwort.text[:200]
