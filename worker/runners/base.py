"""Was ein Runner ist.

Ein Runner bekommt einen Job als Wörterbuch und eine Funktion zum Loggen, und
gibt ein Ergebnis zurück. Mehr nicht. STT, TTS und der Coding-Agent kommen
später als weitere Runner dazu, ohne dass sich die Poll-Schleife ändert.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

#: Eine Zeile, die im Job-Log landet.
LogFn = Callable[[str], None]


class RunnerError(RuntimeError):
    """Der Job ist gescheitert, mit einer für Menschen lesbaren Begründung.

    Die Meldung landet unverändert im Job-Log und in der Oberfläche. Sie sollte
    deshalb sagen, was zu tun ist, und nicht nur, was kaputt war.
    """


@runtime_checkable
class Runner(Protocol):
    name: str

    def run(self, job: dict[str, Any], log: LogFn) -> dict[str, Any]: ...
