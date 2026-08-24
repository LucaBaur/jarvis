# Orchestrator auf dem Homeserver einrichten

Einmalig durchzuführen. Danach reicht `bash deploy.sh` vom Windows-PC aus.

Der Server ist ein sparsamer Dauerläufer: zwei Kerne, knapp 5 GB freier RAM,
keine GPU. Der Orchestrator passt dorthin, weil er nichts rechnet — er hält die
Queue und liefert die Oberfläche aus. Rechnen tun die Worker.

## Voraussetzungen prüfen

```bash
ssh luca@192.168.8.125
python3 --version          # 3.11 oder neuer, aktuell 3.14.4
python3 -c "import venv"   # muss ohne Fehler durchlaufen
loginctl show-user luca --property=Linger   # muss Linger=yes sagen
```

Steht dort `Linger=no`, würden User-Dienste beim Abmelden beendet:

```bash
sudo loginctl enable-linger luca
```

Port 8400 muss frei sein. Belegt sind 22, 8080, 8099, 8123 (Home Assistant),
8200, 8300 und 9000:

```bash
ss -ltn | grep 8400 || echo "8400 ist frei"
```

## Erster Deploy

Vom Windows-PC aus, im Projektordner:

```bash
bash deploy.sh
```

Das Skript prüft Lint und Tests, überträgt den Code nach `~/jarvis`, legt dort
ein venv an, installiert die Abhängigkeiten und erzeugt beim ersten Mal eine
`.env` mit einem frischen Token. **Das Token wird einmal ausgegeben — notieren,
es kommt gleich in die `worker.yaml`.** Später steht es in `~/jarvis/.env`.

Übertragen werden nie `.env`, `worker.yaml`, `data/` oder `__pycache__`.

## Dienst einrichten

```bash
ssh luca@192.168.8.125
mkdir -p ~/logs ~/.config/systemd/user
cp ~/jarvis/deploy/jarvis.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jarvis
systemctl --user status jarvis
```

Bei nicht-interaktivem SSH (also wenn der Befehl direkt hinter `ssh` steht)
vorher `export XDG_RUNTIME_DIR=/run/user/$(id -u)`, sonst findet `systemctl`
den User-Bus nicht.

Prüfen:

```bash
curl -s http://127.0.0.1:8400/healthz
```

Die Unit begrenzt den Speicher auf 512 MB und gewichtet die CPU herunter. Das
ist Absicht: Home Assistant läuft auf demselben knappen RAM und soll nicht
darunter leiden.

## Logs

```bash
tail -f ~/logs/jarvis.log
journalctl --user -u jarvis -f    # zusätzlich, für Start- und Absturzmeldungen
```

Fehler eines einzelnen Jobs stehen nicht hier, sondern am Job selbst — in der
Oberfläche unter dem Auftrag oder über `GET /api/jobs/<id>/logs`.

## Konfiguration ändern

Alles steht in `~/jarvis/.env`, erklärt in `.env.example`. Nach einer Änderung:

```bash
systemctl --user restart jarvis
```

Die Werte, die man wirklich anfasst:

`JARVIS_LEASE_SECONDS` (Standard 300) bestimmt, wie lange ein Job einem
stummen Worker erhalten bleibt, bevor er zurück in die Queue geht. Der
Heartbeat verlängert ihn laufend, deshalb muss er nicht zur Joblaufzeit passen.

`JARVIS_NIGHT_START` und `JARVIS_NIGHT_END` legen das Nachtfenster in
**Ortszeit** fest. Der Server läuft in UTC und bleibt dabei, weil Home
Assistant sich darauf verlässt; Jarvis rechnet selbst um.

`JARVIS_HEARTBEAT_TIMEOUT` (Standard 90) entscheidet, ab wann ein Worker in
der Oberfläche als offline gilt.

## Zugriff von unterwegs

Tailscale läuft auf dem Server bereits (`homeserver`, `100.84.41.20`). Ist das
Handy im selben Tailnet, genügt:

```
http://homeserver:8400
```

Kein Port-Forwarding im Router, keine Freigabe ins Internet. Der Dienst hört
auf `0.0.0.0`, ist also im Heimnetz und im Tailnet erreichbar — beides
gewollt, beides durch das Token geschützt.

Wer die Oberfläche als App auf dem Handy will: Seite öffnen, anmelden, im
Browsermenü „Zum Startbildschirm hinzufügen". Das Anmelde-Cookie hängt am
Hostnamen, also am besten von Anfang an immer denselben verwenden.

## Aktualisieren

```bash
bash deploy.sh
```

Nichts weiter. Das Skript startet den Dienst am Ende neu und meldet, ob er
läuft. Die Datenbank bleibt liegen, das Schema wird beim Start bei Bedarf
ergänzt.

## Wieder loswerden

```bash
systemctl --user disable --now jarvis
rm ~/.config/systemd/user/jarvis.service
systemctl --user daemon-reload
rm -rf ~/jarvis          # löscht auch die Datenbank mit allen Aufträgen
```
