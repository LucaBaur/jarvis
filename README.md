# Jarvis

Selbst gehostetes LLM-Harness. Aufträge gehen per Weboberfläche oder REST-API
rein, warten in einer Queue und werden von dem Rechner abgearbeitet, der gerade
läuft und die passende Hardware hat. Alles lokal, keine bezahlten APIs, keine
Cloud-Modelle.

Der Kern ist eine Aufgabenteilung: Ein kleiner Dauerläufer verwaltet die
Aufträge, und beliebig viele Worker holen sich Arbeit ab. Der rechnende Rechner
darf ausgeschaltet sein — Aufträge warten dann einfach.

```
Handy / Browser
      |  HTTP, von unterwegs über Tailscale
      v
ORCHESTRATOR (Homeserver, Port 8400)   FastAPI + SQLite, läuft 24/7
      ^
      |  Worker fragen nach Arbeit (Pull)
   Worker "gpu-pc"          Worker "gpu-3090" (geplant)
   Windows, Ollama          48 GB VRAM
```

Warum es so gebaut ist, steht in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Was als Nächstes kommt, in [docs/ROADMAP.md](docs/ROADMAP.md).

**Stand:** Phase 1. Text-Aufträge laufen durch. Sprache, Home Assistant und
Nacht-Jobs sind vorbereitet, aber noch nicht gebaut.

## Schnellstart auf einem Rechner

Für einen ersten Blick reichen Orchestrator und Worker auf derselben Maschine.
Voraussetzung: Python 3.11 oder neuer, und ein laufendes
[Ollama](https://ollama.com) mit mindestens einem Modell (`ollama pull llama3.1:8b`).

```bash
git clone <pfad-zum-repo> Jarvis && cd Jarvis

python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # Linux: .venv/bin/python

cp .env.example .env
# JARVIS_TOKEN setzen:
.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Orchestrator starten:

```bash
.venv/Scripts/python -m jarvis.main
```

Im Browser `http://localhost:8400` öffnen und das Token aus der `.env`
eingeben. Ein Auftrag lässt sich sofort absetzen — er bleibt auf `queued`
stehen, weil noch kein Worker läuft. Das ist richtig so.

Worker in einem zweiten Fenster:

```bash
cp worker/worker.example.yaml worker/worker.yaml
# darin token eintragen (dasselbe wie JARVIS_TOKEN) und orchestrator auf
# http://127.0.0.1:8400 setzen
.venv/Scripts/python -m worker.agent
```

Der wartende Auftrag läuft jetzt an.

## Dauerbetrieb

Orchestrator auf dem Homeserver, Worker auf dem PC mit der Grafikkarte:

- [docs/SETUP-SERVER.md](docs/SETUP-SERVER.md) — venv, systemd-Unit, Deploy,
  Zugriff von unterwegs
- [docs/SETUP-WORKER.md](docs/SETUP-WORKER.md) — Ollama richtig konfigurieren,
  Worker als Windows-Aufgabe, Modellwahl für 6 GB VRAM

Deployen aus dem Projektordner heraus:

```bash
bash deploy.sh
```

Das Skript prüft erst Lint und Tests und lädt nur bei grün hoch. Es überträgt
niemals `.env`, `worker.yaml` oder die Datenbank.

## Aufbau

| Ordner | Inhalt |
|---|---|
| `jarvis/` | Orchestrator: Queue, Registry, Scheduler, API, Oberfläche |
| `worker/` | Worker: Poll-Schleife und Runner (aktuell nur Ollama) |
| `docs/` | Architektur, Roadmap, Setup |
| `deploy/` | systemd-Unit und Anleitung für die Windows-Aufgabenplanung |
| `tests/` | 113 Tests |

Die wichtigsten Dateien, wenn man sich einarbeitet: `jarvis/queue.py` hält den
Zustandsautomaten der Jobs, `jarvis/scheduler.py` entscheidet, welcher Job zu
welchem Worker passt, `worker/agent.py` ist die Schleife auf der Worker-Seite.

## Wie ein Auftrag durchläuft

```
queued ---claim---> assigned ---start---> running ---> done
   ^                    |                    |
   +---- Lease abgelaufen, attempts + 1 -----+
   |
   +---> failed (keine Versuche mehr)   +---> cancelled
```

Ein Job wird nicht zugewiesen, sondern **verpachtet**. Der Worker verlängert
den Lease mit jedem Heartbeat. Wird der PC mitten im Job ausgeschaltet, läuft
der Lease ab und der Job geht zurück in die Queue, statt für immer auf
`running` zu stehen.

Jeder Job hat eine Priorität, ein erlaubtes Zeitfenster (`any`, `day`,
`night`), geforderte Tags, einen Mindest-VRAM-Bedarf und optional ein Modell.
Ein Worker bekommt nur Jobs, die er auch erfüllen kann.

## API

Alles hinter dem Token, entweder im Header `X-Jarvis-Token` oder im Cookie
`jarvis_token` (das setzt die Anmeldeseite). `GET /healthz` ist frei.

```bash
TOKEN=$(grep '^JARVIS_TOKEN=' .env | cut -d= -f2)

# Auftrag absetzen
curl -X POST http://192.168.8.125:8400/api/jobs \
  -H "X-Jarvis-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"Was steht als Naechstes an?"}'

# Nachsehen
curl "http://192.168.8.125:8400/api/jobs?status=queued" -H "X-Jarvis-Token: $TOKEN"
curl "http://192.168.8.125:8400/api/jobs/<id>"          -H "X-Jarvis-Token: $TOKEN"
curl "http://192.168.8.125:8400/api/jobs/<id>/logs"     -H "X-Jarvis-Token: $TOKEN"
curl "http://192.168.8.125:8400/api/workers"            -H "X-Jarvis-Token: $TOKEN"
```

Die vollständige Beschreibung erzeugt FastAPI selbst: `http://.../docs`.

Felder beim Absetzen, alle optional außer `prompt`: `model`, `system`,
`priority` (0–999, kleiner ist wichtiger), `window`, `required_tags`,
`min_vram_mb`, `max_attempts`.

## Entwickeln

```bash
.venv/Scripts/python -m pytest        # 113 Tests
.venv/Scripts/python -m ruff check .
```

`tests/test_nebenlaeufig.py` fährt einen echten Uvicorn hoch und feuert
parallele Anfragen dagegen. Der Test existiert, weil eine frühere Fassung sich
unter Last festgefahren hat und der `TestClient` das nicht zeigen konnte — er
arbeitet Anfragen nacheinander im selben Thread ab.

## Sicherheit

Ein einziges Geheimnis, `JARVIS_TOKEN`, für alles. Kein Nutzerkonto, keine
Rollen — es gibt einen Nutzer, und der Dienst läuft im LAN und im Tailnet. Das
Token soll verhindern, dass ein versehentlich offener Port sofort benutzbar
ist, mehr nicht.

Kein Port-Forwarding ins Internet. Von unterwegs geht der Zugriff über
Tailscale.

Geheimnisse stehen ausschließlich in `.env` (Orchestrator) und
`worker/worker.yaml` (Worker). Beide sind in `.gitignore`; eingecheckt sind
nur die Vorlagen mit Platzhaltern.

## Wenn etwas nicht geht

**Aufträge bleiben auf `queued`.** Läuft ein Worker? `GET /api/workers` zeigt
`state`. Steht dort `offline`, hat sich der Worker länger als
`JARVIS_HEARTBEAT_TIMEOUT` Sekunden nicht gemeldet. Passt der Job überhaupt zum
Worker — genug VRAM, richtige Tags, Modell vorhanden?

**Job scheitert mit „Ollama nicht erreichbar".** Auf dem Worker-Rechner
`ollama list` ausführen. Läuft Ollama auf einem anderen Rechner als der Worker,
muss `OLLAMA_HOST` dort auf `0.0.0.0` stehen, sonst hört es nur auf localhost.

**Job hängt auf `running`.** Der Worker ist weg. Nach Ablauf des Lease
(`JARVIS_LEASE_SECONDS`, Standard 300) holt der Aufräumer den Job zurück.
Sofort erzwingen:
`curl -X POST .../api/maintenance/reap -d '{"force":true}' -H "X-Jarvis-Token: $TOKEN" -H "Content-Type: application/json"`

**Die Oberfläche fragt immer wieder nach dem Token.** Das Cookie ist
`httponly` und an den Host gebunden. Wer über `192.168.8.125:8400` anmeldet und
später über den Tailscale-Namen kommt, muss sich dort erneut anmelden.

**Der Dienst antwortet nicht.**
`systemctl --user status jarvis` und `tail -f ~/logs/jarvis.log` auf dem
Server. Bei nicht-interaktivem SSH vorher
`export XDG_RUNTIME_DIR=/run/user/$(id -u)`.
