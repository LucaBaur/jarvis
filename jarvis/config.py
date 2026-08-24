"""Konfiguration. Alles kommt aus der .env, nichts steht im Code."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.scheduler import WindowConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Werte, die aus .env.example stammen und kein echtes Geheimnis sind.
INSECURE_TOKENS = frozenset({"dev-token", "bitte-ersetzen", "", "changeme"})


def _parse_hhmm(value: str) -> time:
    hour, _, minute = value.strip().partition(":")
    return time(hour=int(hour), minute=int(minute or 0))


class Settings(BaseSettings):
    """Einstellungen des Orchestrators.

    Die Feldnamen entsprechen den Variablen in der .env mit Präfix ``JARVIS_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    token: str = "dev-token"
    host: str = "0.0.0.0"
    port: int = 8400
    db: Path = Path("data/jarvis.db")
    tz: str = "Europe/Berlin"
    night_start: str = "23:00"
    night_end: str = "07:00"
    lease_seconds: int = Field(default=300, ge=10)
    heartbeat_timeout: int = Field(default=90, ge=10)
    log_level: str = "info"

    @field_validator("db")
    @classmethod
    def _absolute_db_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def night_from(self) -> time:
        return _parse_hhmm(self.night_start)

    @property
    def night_to(self) -> time:
        return _parse_hhmm(self.night_end)

    @property
    def window_config(self) -> WindowConfig:
        return WindowConfig(night_start=self.night_from, night_end=self.night_to, tz=self.zone)

    @property
    def token_is_default(self) -> bool:
        """Steckt noch der Platzhalter drin?

        Der Start warnt dann laut, statt still einen Dienst ohne wirksames
        Token laufen zu lassen.
        """
        return self.token in INSECURE_TOKENS


@lru_cache
def get_settings() -> Settings:
    return Settings()
