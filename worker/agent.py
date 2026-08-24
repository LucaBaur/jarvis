"""Die Poll-Schleife.

Der Worker fragt, ob es Arbeit gibt, erledigt sie und meldet das Ergebnis. Er
wird nie von außen angesprochen. Damit ist ein ausgeschalteter Rechner kein
Sonderfall, sondern einfach ein Worker, der gerade nicht fragt.

Zwei Regeln bestimmen den Aufbau:

* Kein Fehler darf die Schleife beenden. Ein abgestürzter Job wird gemeldet,
  ein weggefallener Orchestrator abgewartet.
* Kein Fehler darf im Nirvana landen. Alles, was schiefgeht, wird als Fehler
  am Job vermerkt, damit es in der Oberfläche steht.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from worker.client import OrchestratorUnreachable
from worker.config import WorkerConfig
from worker.runners.base import Runner, RunnerError

log = logging.getLogger("jarvis.worker")


class Agent:
    def __init__(
        self,
        config: WorkerConfig,
        client: Any,
        runners: dict[str, Runner],
    ) -> None:
        self.config = config
        self.client = client
        self.runners = runners

    def register(self) -> None:
        self.client.register(self.config.as_registration())
        log.info("Als %s angemeldet bei %s", self.config.name, self.config.orchestrator)

    def poll_once(self) -> bool:
        """Ein Durchgang. ``True``, wenn ein Job bearbeitet wurde.

        Der Heartbeat kommt zuerst: Er verlängert nebenbei den Lease auf
        laufende Jobs und meldet den Worker als online.
        """
        try:
            self.client.heartbeat(self.config.name)
            job = self.client.claim(self.config.name)
        except OrchestratorUnreachable as fehler:
            log.warning("Orchestrator nicht erreichbar: %s", fehler)
            return False

        if job is None:
            return False

        self._arbeite(job)
        return True

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.register()
        while not stop.is_set():
            try:
                gearbeitet = self.poll_once()
            except Exception:  # die Schleife darf nie sterben
                log.exception("Unerwarteter Fehler in der Schleife, mache weiter")
                gearbeitet = False
            # Nach einem Job sofort weiter, sonst im Takt warten.
            stop.wait(0 if gearbeitet else self.config.poll_interval)

    # --- Innenleben -----------------------------------------------------

    def _arbeite(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        typ = job.get("type", "chat")
        runner = self.runners.get(typ)

        if runner is None:
            self._melde_fehler(
                job_id,
                f"Für den Jobtyp {typ} hat dieser Worker keinen Runner. "
                f"Vorhanden: {', '.join(sorted(self.runners)) or 'keiner'}",
            )
            return

        try:
            self.client.start(job_id, self.config.name)
        except OrchestratorUnreachable as fehler:
            # Der Job bleibt beim Orchestrator, sein Lease läuft ab und er
            # wird neu vergeben. Hier ist nichts zu retten.
            log.warning("Job %s konnte nicht gestartet werden: %s", job_id, fehler)
            return

        def logge(zeile: str) -> None:
            try:
                self.client.log(job_id, self.config.name, zeile)
            except OrchestratorUnreachable:
                log.warning("Logzeile ging verloren: %s", zeile)

        try:
            ergebnis = runner.run(job, logge)
        except RunnerError as fehler:
            self._melde_fehler(job_id, str(fehler))
            return
        except Exception as fehler:  # alles Unerwartete gehört in den Job
            log.exception("Runner %s ist abgestürzt", typ)
            self._melde_fehler(job_id, f"{type(fehler).__name__}: {fehler}")
            return

        try:
            self.client.complete(job_id, self.config.name, ergebnis)
        except OrchestratorUnreachable as fehler:
            log.error("Job %s ist fertig, das Ergebnis kam aber nicht an: %s", job_id, fehler)

    def _melde_fehler(self, job_id: str, meldung: str) -> None:
        try:
            self.client.fail(job_id, self.config.name, meldung)
        except OrchestratorUnreachable as fehler:
            log.error("Fehler zu Job %s konnte nicht gemeldet werden: %s", job_id, fehler)


def main() -> None:
    """Einstiegspunkt: worker.yaml lesen, anmelden, Schleife starten."""
    import argparse
    from pathlib import Path

    import httpx

    from worker.client import OrchestratorClient
    from worker.runners.ollama_runner import OllamaRunner

    parser = argparse.ArgumentParser(description="Jarvis-Worker")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "worker.yaml",
        help="Pfad zur worker.yaml",
    )
    parser.add_argument("--once", action="store_true", help="nur einen Durchgang, für Tests")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s %(message)s"
    )
    config = WorkerConfig.from_yaml(args.config)

    runner = OllamaRunner(
        config.ollama_url,
        config.default_model,
        client=httpx.Client(timeout=config.job_timeout),
    )

    # Die tatsächlich vorhandenen Modelle schlagen die Liste aus der Datei.
    vorhanden = runner.available_models()
    if vorhanden:
        config.models = vorhanden
        log.info("Ollama meldet %d Modelle: %s", len(vorhanden), ", ".join(vorhanden))
    else:
        log.warning(
            "Ollama unter %s antwortet nicht. Der Worker meldet sich trotzdem an, "
            "bekommt aber keine Jobs, die ein bestimmtes Modell verlangen.",
            config.ollama_url,
        )

    client = OrchestratorClient(
        config.orchestrator, config.token, timeout=config.request_timeout
    )
    agent = Agent(config, client, {"chat": runner})

    if args.once:
        agent.register()
        agent.poll_once()
        return

    try:
        agent.run_forever()
    except KeyboardInterrupt:
        log.info("Beendet.")


if __name__ == "__main__":
    main()
