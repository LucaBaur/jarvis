# Worker auf dem Windows-PC einrichten

Der Worker ist der Teil, der rechnet. Er holt sich Aufträge beim Orchestrator
ab und wird nie von außen angesprochen — deshalb braucht der PC keine feste
IP-Adresse und keine offenen Ports.

Der Rechner: `LUCASPC`, Core i5-10400F, 16 GB RAM, GTX 1660 SUPER mit
**6 GB VRAM**. Diese sechs Gigabyte sind die Grenze, an der sich alles
entscheidet.

## Ollama vorbereiten

Zwei Einstellungen stehen ab Werk falsch für diesen Zweck.

**Kontextfenster.** `OLLAMA_CONTEXT_LENGTH` steht auf 2048 Token. Das reicht
für kurze Fragen, aber nicht für einen Auftrag mit Vorgeschichte. In
PowerShell, dauerhaft für den Benutzer:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "8192", "User")
```

Mehr Kontext kostet VRAM. Bei 6 GB ist mit einem 7B-Modell in Q4 irgendwo um
8192 Schluss; darüber schiebt Ollama Schichten in den RAM und es wird zäh statt
schnell. Ob es passt, sieht man an `ollama ps` in der Spalte `PROCESSOR` — steht
dort `100% GPU`, ist alles gut, bei `CPU` läuft es teilweise auf dem Prozessor.

**Erreichbarkeit.** `OLLAMA_HOST` steht auf `127.0.0.1`. Solange Worker und
Ollama auf demselben Rechner laufen, ist das genau richtig und sollte so
bleiben. Nur falls der Worker einmal woanders läuft:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
```

Nach beiden Änderungen Ollama neu starten (Symbol in der Taskleiste beenden und
neu öffnen).

## Worker einrichten

```powershell
cd C:\Users\you\code\Jarvis
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
copy worker\worker.example.yaml worker\worker.yaml
```

In `worker\worker.yaml` gehören:

`token` — dasselbe wie `JARVIS_TOKEN` in der `.env` des Orchestrators, zu
finden in `~/jarvis/.env` auf dem Server.

`orchestrator` — `http://homeserver:8400`.

`vram_mb` — `6144` für die 1660 SUPER. Danach richtet sich, welche Jobs dieser
Worker überhaupt annimmt.

`tags` — was dieser Rechner kann, etwa `[gpu, coding]`. Ein Job mit
`required_tags` geht nur an einen Worker, der alle davon hat.

`models` muss man nicht pflegen: Beim Start fragt der Worker Ollama, welche
Modelle wirklich da sind, und meldet die.

`worker.yaml` steht in `.gitignore`, weil das Token darin steht.

Testlauf:

```powershell
.venv\Scripts\python.exe -m worker.agent --once
```

Der Worker meldet sich an, holt höchstens einen Auftrag, erledigt ihn und
beendet sich. Ohne `--once` läuft er dauerhaft.

## Dauerhaft laufen lassen

Anleitung für die Windows-Aufgabenplanung steht in
`deploy/jarvis-worker.xml.txt`. Kurz: eine Aufgabe „Jarvis Worker", die beim
Anmelden `pythonw.exe -m worker.agent` startet und sich bei Fehlern neu
startet. `pythonw` statt `python`, damit kein Konsolenfenster offen bleibt.

Für die Fehlersuche den Worker von Hand starten — dann steht das Log im
Fenster.

## Modelle für 6 GB VRAM

Eine grobe Rechnung: Ein Modell in Q4_K_M belegt ungefähr `Parameter × 0,6 GB`,
dazu kommt der Kontext. Bei 6 GB VRAM heißt das:

7B in Q4 passt komfortabel mit brauchbarem Kontext und ist der Arbeitsbereich
dieses Rechners. 8B in Q4 passt gerade so, mit kleinerem Kontext — `llama3.1:8b`
läuft und ist derzeit das Standardmodell. 13B in Q4 passt nicht mehr ganz;
Ollama lagert dann auf die CPU aus, und aus Sekunden werden Minuten. Alles
darüber ist auf dieser Karte sinnlos.

Für Coding-Jobs in Phase 4 ist `qwen2.5-coder:7b` in Q4_K_M der Kandidat:

```powershell
ollama pull qwen2.5-coder:7b
```

Ob das für echte Arbeit an einem Projekt reicht, muss gemessen werden, nicht
behauptet. Der begrenzende Faktor ist nicht die Geschwindigkeit — lange
Laufzeiten sind ausdrücklich erlaubt — sondern das Kontextfenster: Wer nur
8192 Token hat, kann dem Modell keine großen Dateien zeigen.

Was Geschwindigkeit angeht: Ein kalt geladenes 8B-Modell braucht auf dieser
Karte 20 bis 30 Sekunden bis zur ersten Antwort. Danach hält Ollama es fünf
Minuten im Speicher (`OLLAMA_KEEP_ALIVE`), und dieselbe Frage ist in unter einer
Sekunde beantwortet.

Wenn der 3090-Build kommt: Der bekommt seine eigene `worker.yaml` mit
`vram_mb: 49152` und passenden Tags, und dockt damit an. Am Orchestrator ist
nichts zu ändern.

## Netzwerk und Wecken

Der PC hängt am WLAN. Der Ethernet-Anschluss ist frei (MAC `88-AE-DD-10-3A-D2`),
aber der einzige LAN-Port des Routers ist von der Hue-Bridge belegt.

Das ist für Phase 1 egal, wird aber in Phase 4 wichtig: **Wake-on-LAN
funktioniert über WLAN nicht.** Der verbaute Realtek 8822CE unterstützt kein
zuverlässiges Aufwachen aus dem ausgeschalteten Zustand.

Drei Wege, in dieser Reihenfolge sinnvoll:

`self_timer` braucht keine neue Hardware. Der Worker fragt vor dem Einschlafen,
wann das nächste Zeitfenster beginnt, setzt per Aufgabenplanung einen
Weck-Timer und legt den PC schlafen. Funktioniert über WLAN, weil gar kein
Netzwerk nötig ist.

`wol` wird möglich, sobald ein einfacher Gigabit-Switch am Router hängt — Switch
in den LAN-Port, Hue-Bridge und PC in den Switch. Kostet etwa zwölf Euro.
Zusätzlich muss im BIOS „Power On by PCI-E/PCI" eingeschaltet sein und in
Windows unter *Geräte-Manager → Netzwerkadapter → Realtek PCIe GbE →
Energieverwaltung* das Aufwecken erlaubt werden. Das ist der robustere Weg,
weil der Server dann auch spontan wecken kann.

`always_on` heißt: Der PC läuft durch. Funktioniert sofort, kostet Strom.

Weil Worker pollen, ist dem Orchestrator die Wahl gleichgültig. Er legt
Aufträge in die Queue; ob und wie ein Rechner aufwacht, ist dessen Sache.

## Wenn etwas nicht geht

**„Ollama ist nicht erreichbar".** `ollama list` im selben Fenster ausführen.
Antwortet das nicht, läuft Ollama nicht.

**„Token wurde abgelehnt".** Das `token` in `worker.yaml` stimmt nicht mit
`JARVIS_TOKEN` in `~/jarvis/.env` überein.

**Der Worker meldet sich an, bekommt aber nichts.** Der Job passt nicht zu ihm.
`GET /api/jobs/<id>` zeigt `min_vram_mb`, `required_tags` und `model` — eines
davon erfüllt der Worker nicht.

**Der Worker meldet keine Modelle.** Ollama lief beim Start des Workers nicht.
Er läuft trotzdem weiter, bekommt aber keine Jobs, die ein bestimmtes Modell
verlangen. Ollama starten, Worker neu starten.
