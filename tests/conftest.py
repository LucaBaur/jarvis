from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from jarvis.db import connect
from jarvis.models import WakeStrategy, Worker, WorkerState
from jarvis.scheduler import WindowConfig

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def windows() -> WindowConfig:
    """Nacht von 23:00 bis 07:00 Berliner Zeit, wie in der .env.example."""
    return WindowConfig(night_start=time(23, 0), night_end=time(7, 0), tz=BERLIN)


def make_worker(
    name: str = "gpu-pc",
    *,
    tags: list[str] | None = None,
    models: list[str] | None = None,
    vram_mb: int = 6144,
) -> Worker:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    return Worker(
        name=name,
        tags=tags if tags is not None else ["gpu", "coding"],
        models=models if models is not None else ["llama3.1:8b", "phi3:mini"],
        vram_mb=vram_mb,
        address=None,
        wake_strategy=WakeStrategy.ALWAYS_ON,
        last_heartbeat=now,
        registered_at=now,
        state=WorkerState.ONLINE,
    )


@pytest.fixture
def worker() -> Worker:
    return make_worker()
