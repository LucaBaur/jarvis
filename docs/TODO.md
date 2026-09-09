# Was noch offen ist

Stand 24.08.2026, Version 0.2.1. Phase 1 und 2 laufen auf dem Homeserver.
Die groben Züge stehen in `ROADMAP.md`; hier steht, was konkret ansteht.

## Musst du selbst machen

Diese Punkte kann ich nicht erledigen — sie brauchen ein Passwort, ein
Gerät in deiner Hand oder eine Entscheidung.

- [ ] **Worker automatisch starten lassen.** Solange du ihn von Hand startest,
      wartet jeder Auftrag vom Handy, bis du am PC sitzt. Genau das ist heute
      passiert. Anleitung liegt fertig in `deploy/jarvis-worker.xml.txt`, eine
      Aufgabe in der Windows-Aufgabenplanung, fünf Minuten.
- [ ] **Als App installieren.** Am Handy `https://homeserver.tailnet.ts.net:10000`
      öffnen und installieren. Am PC vorher Tailscale einrichten oder den
      SSH-Tunnel nutzen, siehe README.
- [ ] **Sprachaufnahme mit echter Stimme ausprobieren.** Bisher getestet nur mit
      synthetischer Stimme aus Piper. Ob das Modell `base` deine Sprechweise
      versteht, zeigt sich erst im Gebrauch. Falls es nervt: `small` ist
      vermessen und dokumentiert, umgestellt wird ohne Codeänderung.
- [ ] **Entscheiden, ob ein Gigabit-Switch angeschafft wird.** Für Phase 4
      relevant. Kostet etwa zwölf Euro, macht Wake-on-LAN möglich. Ohne ihn
      geht es auch, aber umständlicher (siehe unten).

## Kleinigkeiten, die ich noch nachziehen sollte

- [ ] **Sichtprüfung des Mikrofon-Knopfs im Browser.** Steht seit Phase 2 aus,
      weil Chrome sich zwischendurch abgemeldet hat. Bisher ist nur geprüft,
      dass der Knopf ausgeliefert wird — nicht, dass er tut.
- [ ] **Markdown in Antworten darstellen.** Die Modelle antworten mit `**fett**`
      und Aufzählungen, das steht aktuell roh im Textfeld. Kosmetik, aber es
      fällt bei jeder längeren Antwort auf.
- [ ] **Alte Aufträge aufräumen.** Die Datenbank wächst unbegrenzt. Noch kein
      Problem, wird aber eines.
- [ ] **`sudo apt install python3.14-venv` auf dem Server.** Dann greift beim
      Deploy der normale Weg statt des get-pip-Umwegs. Rein kosmetisch.

## Phase 3 — Home Assistant

Der nächste größere Brocken.

- [ ] Eigenen Long-Lived Token in HA anlegen, getrennt von dem der
      Anytype-Sync-Jobs
- [ ] HA-Client: States lesen, Services aufrufen
- [ ] Jobstatus als HA-Sensor, nach dem Muster der bestehenden Cronjobs
      (`~/anytype-sync/status/` plus `ha_status.py`)
- [ ] Beispielkarte für Lovelace und `rest_command`-Schnipsel in
      `docs/homeassistant/`
- [ ] Tool-Calling: Das Modell darf HA selbst abfragen
- [ ] **Erlaubnisliste fürs Schalten.** Lesen frei, Schalten nur für
      ausdrücklich freigegebene Entitäten. Ein 8B-Modell, das eine Frage
      falsch versteht und nachts das Licht anmacht, ist kein hypothetisches
      Risiko.

Abgenommen, wenn „Ist im Wohnzimmer das Licht an?" korrekt beantwortet wird
und ein laufender Job auf einem HA-Dashboard sichtbar ist.

## Phase 4 — Nacht-Jobs und Coding

- [ ] Weckstrategie `self_timer`: Worker fragt das nächste Zeitfenster ab,
      setzt einen Weck-Timer, legt den PC schlafen
- [ ] Weckstrategie `wol`, falls der Switch kommt
- [ ] Coding-Job-Typ: Repo-Pfad plus Aufgabe, Ergebnis ist ein Branch oder ein
      Patch. **Nie direkt auf `main`.** Die meisten deiner Projekte haben kein
      Git, für die wird in ein Arbeitsverzeichnis kopiert und ein Diff erzeugt
- [ ] Projekt-Registry über alle Unterordner von `C:\Users\you\code`
- [ ] `qwen2.5-coder:7b` messen statt behaupten. Bei 6 GB VRAM ist nicht die
      Geschwindigkeit das Problem, sondern das Kontextfenster

## Phase 5 — Zweiter GPU-Worker

- [ ] 3090-Build anschließen, `worker.yaml` mit `vram_mb: 49152`
- [ ] Prüfen, ob wirklich keine Zeile im Orchestrator geändert werden muss.
      Genau das ist der Test dieser Phase — wenn doch, war die Architektur
      falsch, und das gehört dann dokumentiert

## Bewusst nicht gebaut

Keine offenen Punkte, sondern Entscheidungen:

- **Piper für gesprochene Antworten.** Funktioniert, ist geprüft, aber ob es im
  Alltag etwas bringt, zeigt sich erst mit Gebrauch.
- **MQTT.** Es läuft kein Broker, und REST reicht. Kommt in Phase 3, falls sich
  REST als zu umständlich erweist.
- **Job-Verkettung.** Die Transkription läuft in der Anfrage statt als eigener
  Job. Architektonisch unsauber, praktisch belanglos, solange Sprachnachrichten
  ein paar Sekunden lang sind.
