# Jarvis — Roadmap

Fünf Phasen. Jede hat ein Abnahmekriterium, das man von Hand prüfen kann.
Erst wenn das erfüllt ist, geht es weiter. Der aktuelle Stand steht in
`../CHANGELOG.md`.

## Phase 1 — Fundament

Ein Auftrag geht rein, ein lokales Modell beantwortet ihn, die Antwort ist im
Browser sichtbar. Ohne Sprache, ohne Home Assistant.

Umfang: Repo mit `.env.example` und README; Orchestrator mit Job-Queue,
Worker-Registry und Heartbeat; ein Worker auf dem PC, der Text-Prompts über
Ollama abarbeitet; Web-UI zum Absetzen, mit Jobliste und Detailseite samt Log;
Deploy-Skript und systemd-Unit; Tests für Queue, Scheduler und Registry.

Abgenommen, wenn: Ein Job, der abgesetzt wird, während der PC aus ist, bleibt
auf `queued` und läuft an, sobald der Worker startet. Ein Worker, der mitten im
Job abstürzt, gibt den Job nach Ablauf des Lease wieder frei. Und ein Job lässt
sich vom Handy über Tailscale absetzen und die Antwort dort lesen.

## Phase 2 — Sprache und Mobil

Sprachnachricht statt Tippen.

Umfang: Upload-Endpunkt für Audio; Transkription über den vorhandenen
`wyoming-whisper` auf dem Homeserver, hinter einer austauschbaren
Schnittstelle; der erkannte Text wird zum Job; die Weboberfläche wird als PWA
installierbar, mit Aufnahme-Knopf über die MediaRecorder-API. Piper für
gesprochene Antworten ist optional und kommt nur, wenn sich das im Alltag als
nützlich erweist.

Offener Punkt: Die Wyoming-Container hören nur auf `127.0.0.1`. Läuft der
Orchestrator auf demselben Server, reicht das; sonst muss das Port-Binding
geändert werden.

Abgenommen, wenn: Eine vom Handy aufgenommene deutsche Sprachnachricht landet
als korrekt transkribierter Job in der Queue und wird beantwortet.

## Phase 3 — Home Assistant

Jarvis kann den Zustand des Hauses lesen und Dinge schalten, und der Jobstatus
ist in Home Assistant sichtbar.

Umfang: HA-Client für die REST-API mit eigenem Long-Lived Token; Jobstatus als
Sensoren, nach demselben Muster wie die bestehenden Cronjobs
(`~/anytype-sync/status/` plus `ha_status.py`); Beispielkarte für Lovelace und
`rest_command`-Schnipsel in `docs/homeassistant/`; Tool-Calling, damit das
Modell selbst HA-States abfragen und Services aufrufen kann.

Beim Tool-Calling braucht es eine Erlaubnisliste: Lesen ist frei, Schalten nur
für ausdrücklich freigegebene Entitäten. Ein Modell, das aus Versehen das Licht
im Schlafzimmer um drei Uhr nachts anschaltet, ist kein hypothetisches Risiko.

MQTT wird hier nachgerüstet, falls sich REST als zu umständlich erweist.

Abgenommen, wenn: Die Frage „Ist im Wohnzimmer das Licht an?" wird korrekt
beantwortet, und der Status eines laufenden Jobs ist auf einem HA-Dashboard zu
sehen.

## Phase 4 — Nacht-Jobs und Coding

Aufträge, die stundenlang laufen dürfen, während niemand davorsitzt.

Umfang: Der Scheduler beachtet Zeitfenster (das Feld gibt es ab Phase 1, die
Weck-Logik kommt hier); Weckstrategie pro Worker, zunächst `self_timer` über
die Windows-Aufgabenplanung, `wol` sobald der PC per Kabel hängt; der Worker
legt den Rechner schlafen, wenn die Queue leer ist und niemand angemeldet ist;
ein Coding-Job-Typ, der einen Repo-Pfad und eine Aufgabe bekommt und als
Ergebnis einen Branch oder ein Patch-File liefert; eine Projekt-Registry, die
alle Unterordner von `C:\Users\lb307\Desktop\Coding` auflistet.

Harte Regel: **Nie direkt auf `main` committen.** Ergebnis ist immer ein Branch
oder ein Patch, den ich mir ansehe. Die meisten Projekte dort haben kein Git;
für die wird in einem Arbeitsverzeichnis kopiert und ein Diff erzeugt.

Zum Modell: `qwen2.5-coder:7b` in Q4_K_M ist der Kandidat für 6 GB VRAM, mit
deutlich reduziertem Kontextfenster. Ob das für echte Arbeit reicht, wird
gemessen und nicht vorher behauptet. Bis der 3090-Build steht, ist diese Phase
eher Machbarkeitsnachweis als Werkzeug.

Abgenommen, wenn: Ein abends abgesetzter Coding-Job läuft nachts durch und
liegt morgens als Diff vor, ohne dass jemand den PC angefasst hat.

## Phase 5 — Skalierung

Der 3090-Build kommt als zweiter GPU-Worker dazu.

Umfang: Worker installieren, `worker.yaml` mit `vram_mb: 49152` und Tags
ausfüllen, größere Modelle laden. Am Orchestrator sollte sich nichts ändern
müssen — genau das ist der Test dieser Phase. Wenn doch, war die Architektur
falsch, und das gehört dann hier dokumentiert.

Zusätzlich: Routing nach VRAM und Modell schärfen, wenn zwei GPU-Worker
gleichzeitig laufen und sich um Jobs streiten.

Abgenommen, wenn: Ein neuer Worker wird angeschlossen und bekommt Arbeit, ohne
dass eine Zeile im Orchestrator geändert wurde.
