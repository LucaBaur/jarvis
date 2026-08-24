# Jarvis — Architektur

Stand: 2026-08-24. Dieses Dokument hält fest, wie Jarvis aufgebaut ist und
warum. Es beschreibt den Zielzustand; was davon schon gebaut ist, steht in
`ROADMAP.md`.

## Problem

Ich will Aufträge per Text oder Sprachnachricht absetzen und später eine
Antwort bekommen. Aufträge können Sekunden dauern oder eine ganze Nacht.
Die Hardware dafür ist ungleich verteilt:

- Der Homeserver läuft rund um die Uhr, hat aber zwei schwache Kerne, knapp
  5 GB freien RAM und keine GPU. Er kann kein LLM ausführen.
- Der Windows-PC hat eine GTX 1660 SUPER mit 6 GB VRAM und läuft nur, wenn ich
  davorsitze.
- Ein Server mit zwei RTX 3090 ist geplant, existiert aber nicht.

Ein einzelner Prozess, der Auftrag und Ausführung zusammen erledigt, scheitert
daran, dass der rechnende Rechner die meiste Zeit aus ist.

## Grundentscheidung: Pull statt Push

Der Orchestrator hält den Zustand und rechnet nie. Worker melden sich bei ihm
an und **holen sich** Arbeit ab. Der Orchestrator kennt keine Worker-Adresse
und ruft nie einen Worker auf.

Das löst mehrere Probleme auf einmal. Ein ausgeschalteter Worker blockiert
nichts, Jobs bleiben einfach in der Queue. Die IP des PCs ist egal, es braucht
keine feste Adresse und keine Portfreigabe auf dem PC. Und ein zusätzlicher
Worker ist nur ein weiterer Prozess, der pollt — der 3090-Build lässt sich
anschließen, ohne dass am Orchestrator eine Zeile geändert wird.

```
Handy / Browser
      |  HTTPS über Tailscale
      v
+----------------------------------+
| ORCHESTRATOR  (Homeserver, 8400) |  FastAPI + SQLite + Jinja/htmx
|  Job-Queue, Worker-Registry      |  läuft 24/7, rechnet nie
|  Scheduler (Fenster/Capability)  |
+----------------------------------+
      ^  Worker pollen: register -> heartbeat -> claim -> log -> complete
      +------------------+--------------------+
   worker "light"    worker "gpu-pc"      worker "gpu-3090"
   Homeserver, CPU   LUCASPC, 6 GB VRAM   geplant, 48 GB VRAM
   kleine Modelle    Coding, STT          große Modelle
```

## Bausteine

**Orchestrator** (`jarvis/`). FastAPI, SQLite als einzige Datenbank, Jinja2 mit
htmx für die Oberfläche. Kein Build-Schritt, kein npm, kein Frontend-Framework.
Läuft als systemd-User-Unit auf dem Homeserver, so wie `elo.service` und
`nebenschirm.service` dort auch.

**Worker** (`worker/`). Ein schlanker Python-Prozess mit einer Schleife:
registrieren, Heartbeat senden, Job anfragen, ausführen, Log und Ergebnis
zurückmelden. Was der Worker kann, steht in seiner `worker.yaml` — Tags, VRAM,
verfügbare Modelle. Die eigentliche Arbeit macht ein **Runner** hinter einem
schmalen Protokoll (`run(job, log) -> result`). In Phase 1 gibt es nur
`ollama_runner`; STT, TTS und der Coding-Agent kommen als weitere Runner dazu,
ohne dass sich die Schleife ändert.

**SQLite statt Postgres.** Ein Nutzer, wenige Schreibzugriffe pro Minute, dafür
kein zusätzlicher Dienst und kein RAM-Verbrauch auf einem Server, bei dem RAM
die knappe Ressource ist. WAL-Modus ist aktiv, damit Lesen und Schreiben sich
nicht gegenseitig blockieren.

## Job-Lebenszyklus

```
queued ---claim---> assigned ---start---> running ---> done
   ^                    |                    |
   |                    v                    v
   +---- Lease abgelaufen, attempts + 1 -----+
   |
   +---> failed     (wenn attempts >= max_attempts)
   +---> cancelled  (aus queued, assigned oder running)
```

Ein Job wird nicht zugewiesen, sondern **verpachtet**. Beim Claim bekommt er
ein `lease_expires_at`; der Worker verlängert es mit jedem Heartbeat. Meldet er
sich nicht mehr, weil der PC heruntergefahren wurde oder der Prozess abgestürzt
ist, setzt ein Aufräumer den Job zurück auf `queued` und erhöht `attempts`.
Ohne diesen Mechanismus würde ein Job beim ersten Stromausfall für immer in
`running` hängen.

Der Claim läuft in einer `BEGIN IMMEDIATE`-Transaktion. Damit kann kein zweiter
Worker denselben Job ziehen, auch wenn beide im selben Moment fragen.

Jeder Job hat neben `status` diese Felder, die den Scheduler steuern:
`priority` (kleiner ist wichtiger), `window` (`any`, `day` oder `night`),
`required_tags`, `min_vram_mb` und `model`.

## Scheduler

Fragt ein Worker nach Arbeit, filtert der Orchestrator die Queue auf Jobs, die
zu ihm passen: Status `queued`, Zeitfenster passt zur aktuellen Ortszeit, alle
`required_tags` des Jobs sind Tags des Workers, `min_vram_mb` ist erfüllt, und
das gewünschte Modell ist beim Worker vorhanden. Sortiert wird nach Priorität,
bei Gleichstand nach Alter.

Genau hier dockt der 3090-Build später an. Er registriert sich mit
`vram_mb: 49152` und Tags wie `coding` und `large`, und bekommt dadurch von
selbst die Jobs, die der 1660er nicht stemmt. Es gibt keine Liste von Workern
im Code, die man pflegen müsste.

**Zeitzonen.** Der Homeserver läuft in UTC, weil Home Assistant sich darauf
verlässt, und das bleibt so. Alle Zeitstempel in der Datenbank sind UTC. Für
die Fensterprüfung wird nach `Europe/Berlin` umgerechnet. Naive Zeitstempel
kommen nirgends vor.

## Sicherheit

Ein einziges Geheimnis, `JARVIS_TOKEN`, wird bei jedem API-Aufruf im Header
`X-Jarvis-Token` erwartet und in konstanter Zeit verglichen. Das ist kein
Auth-System und soll keines sein: Jarvis läuft im LAN und im Tailnet, es gibt
einen Nutzer, und das Token schützt davor, dass ein versehentlich offener Port
sofort ausnutzbar ist.

Kein Port-Forwarding ins Internet. Von unterwegs geht der Zugriff über
Tailscale, wo der Homeserver bereits eingebunden ist.

Geheimnisse stehen ausschließlich in `.env` und `worker/worker.yaml`, beide
sind in `.gitignore`. Im Repo liegen nur `.env.example` und
`worker/worker.example.yaml` mit Platzhaltern.

## Sprache (Phase 2)

Auf dem Homeserver laufen bereits `wyoming-whisper` (Port 10300) und
`wyoming-piper` (10200) als Docker-Container für die Assist-Pipeline von Home
Assistant. Beide sind lokal, kostenlos und ohne Nutzungsgrenze. Jarvis spricht
sie über das Wyoming-Protokoll an, statt eine zweite Transkription aufzusetzen.

Die Schnittstelle im Code ist trotzdem abstrakt (`transcribe(audio) -> text`).
Wenn die Server-CPU zu langsam wird, tritt `faster-whisper` auf der GPU des PCs
als zweite Implementierung dahinter, als Runner, der wie jeder andere Job
läuft. Die Container hören derzeit nur auf `127.0.0.1`; für Zugriff vom PC aus
müsste das Port-Binding geändert werden.

## Home Assistant (Phase 3)

Home Assistant ist Oberfläche und Auslöser, nie Rechenknecht. Der Zugriff läuft
über die REST-API mit einem eigenen Long-Lived Token, getrennt von dem, den die
Anytype-Sync-Jobs benutzen, damit man einen davon widerrufen kann, ohne den
anderen zu treffen.

Jobstatus wird nach demselben Muster gemeldet, das die bestehenden Cronjobs
verwenden (`~/anytype-sync/status/<job>.json` plus `ha_status.py`), damit es
nicht zwei konkurrierende Konventionen gibt.

MQTT ist vorgesehen, aber vorerst nicht gebaut: Es läuft kein Broker, und REST
reicht. Der HA-Adapter bekommt dafür ein eigenes Modul, damit MQTT später
danebengelegt werden kann.

## Wecken des PCs (Phase 4)

Der PC hängt am WLAN. Der Ethernet-Anschluss ist frei, aber der einzige
LAN-Port des Routers ist von der Hue-Bridge belegt. Wake-on-LAN über WLAN
funktioniert mit dem verbauten Realtek 8822CE aus dem ausgeschalteten Zustand
praktisch nicht. Deshalb ist die Weckstrategie ein Feld am Worker und keine
feste Annahme:

`self_timer` ist der Standardweg ohne neue Hardware. Der Worker fragt vor dem
Einschlafen beim Orchestrator, wann das nächste Zeitfenster beginnt, setzt per
Windows-Aufgabenplanung einen Weck-Timer und legt den PC schlafen. Dafür ist
kein Netzwerk nötig.

`wol` wird möglich, sobald ein einfacher Gigabit-Switch am Router hängt, sodass
Hue-Bridge und PC beide per Kabel angebunden sind, und im BIOS „Power On by
PCI-E" aktiv ist. Das ist der robustere Weg, weil der Orchestrator dann auch
spontan wecken kann.

`always_on` heißt: Der Rechner läuft durch, es wird nichts geweckt.

Weil Worker pollen, ist dem Orchestrator die Strategie gleichgültig. Er legt
Jobs in die Queue; ob und wie ein Worker aufwacht, ist Sache des Workers.

## Was Jarvis bewusst nicht ist

Keine bezahlten APIs und keine Cloud-Modelle. Keine Optimierung auf Latenz,
sechs Stunden für einen Job sind in Ordnung. Kein Multi-User und kein
Rollenmodell. Kein Kubernetes, kein Message-Broker, keine verteilte Datenbank
für eine Queue, die ein paar Dutzend Einträge am Tag hat.
