"""Konfiguration eines Workers.

Steht in einer YAML-Datei neben dem Worker, nicht in der Umgebung: Jeder
Rechner hat andere Fähigkeiten, und die Datei ist zugleich die Dokumentation,
was dieser Rechner kann. ``worker.yaml`` ist in ``.gitignore``, weil das Token
darin steht; eingecheckt ist nur ``worker.example.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PFLICHTFELDER = ("name", "orchestrator", "token")


@dataclass(slots=True)
class WorkerConfig:
    name: str
    orchestrator: str
    token: str

    # Was dieser Rechner kann. Danach bekommt er Jobs zugeteilt.
    tags: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    vram_mb: int = 0

    # Wohin der Runner spricht.
    ollama_url: str = "http://127.0.0.1:11434"
    default_model: str = "llama3.1:8b"

    # Takt der Schleife, in Sekunden.
    poll_interval: int = 5
    request_timeout: int = 30
    #: Lange Jobs sind ausdrücklich erlaubt, deshalb großzügig: vier Stunden.
    job_timeout: int = 4 * 3600

    address: str | None = None
    wake_strategy: str = "none"

    @classmethod
    def from_yaml(cls, path: Path | str) -> WorkerConfig:
        daten: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

        fehlend = [feld for feld in PFLICHTFELDER if not daten.get(feld)]
        if fehlend:
            raise ValueError(
                f"In {path} fehlen Pflichtfelder: {', '.join(fehlend)}. "
                "Vorlage ist worker.example.yaml."
            )
        unbekannt = set(daten) - {f for f in cls.__dataclass_fields__}
        if unbekannt:
            raise ValueError(
                f"In {path} stehen unbekannte Schlüssel: {', '.join(sorted(unbekannt))}"
            )
        return cls(**daten)

    def as_registration(self) -> dict[str, Any]:
        """Was der Orchestrator über diesen Worker wissen muss."""
        return {
            "name": self.name,
            "tags": self.tags,
            "models": self.models,
            "vram_mb": self.vram_mb,
            "address": self.address,
            "wake_strategy": self.wake_strategy,
        }
