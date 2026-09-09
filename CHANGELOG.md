# Changelog

Was sich geändert hat, in verständlicher Sprache. Neueste Version oben.

## 0.2.1 — 24.08.2026

Jarvis lässt sich als App installieren, auf dem Handy und am Rechner. Danach
liegt es mit eigenem Symbol auf dem Startbildschirm und öffnet sich in einem
eigenen Fenster ohne Adresszeile.

Dafür fehlten die Symbole in den Größen, die Chrome verlangt (192 und 512
Pixel als PNG), ein maskierbares für Android und die eigenen Angaben, die iOS
braucht — Safari wertet das Manifest nicht aus. Erzeugt werden die Symbole von
`tools/icons.py` ohne zusätzliche Abhängigkeit; die fertigen Dateien liegen im
Repo.

**Installieren geht nur über HTTPS oder localhost.** Am Handy also die
Tailscale-Adresse verwenden. Wie es am PC geht, steht im README.

15 Tests prüfen das Manifest, die Bildmaße und die Angaben in der Seite. Das
sind Dinge, die still scheitern: Fehlt ein Symbol, taucht der Menüpunkt
„Installieren" einfach nicht auf, ohne jede Meldung.

## 0.2.0 — 24.08.2026

Sprachnachrichten. Auf dem Dashboard gibt es einen Knopf „Sprechen": aufnehmen,
stoppen, und der gesprochene Satz landet als Auftrag in der Queue. Ab da
unterscheidet er sich durch nichts mehr von einem getippten.

Transkribiert wird vom Container `wyoming-whisper`, der auf dem Homeserver
ohnehin für Home Assistant läuft. Kein zweiter Dienst, keine bezahlte API.

**Aufgenommen wird über die Web-Audio-API, nicht über den MediaRecorder.** Der
liefert webm/opus, und auf dem Homeserver ist kein ffmpeg installiert, um das
zu dekodieren — nachinstallieren geht dort nicht ohne Passwort. Der Browser
baut deshalb selbst eine WAV-Datei, die Whisper direkt versteht. Die
Abtastrate ist dabei egal, der Container rechnet um.

**Jarvis ist jetzt über HTTPS erreichbar**, unter
`https://homeserver.tailnet.ts.net:10000`, nur innerhalb des Tailnets. Das
ist keine Bequemlichkeit: Browser geben das Mikrofon ausschließlich auf HTTPS
oder localhost frei, über `http://homeserver:8400` bliebe der Aufnahmeknopf
wirkungslos.

Ist kein Whisper erreichbar oder steht `JARVIS_STT_ENABLED=false`, verschwindet
der Knopf und alles andere läuft weiter.

### Zur Erkennungsqualität

Gemessen auf dem Homeserver, vier deutsche Sätze über Piper eingesprochen:

| Modell | Rechenzeit für 10 s Audio | vollständig richtig |
|---|---|---|
| `base` (läuft) | 11 s | 1 von 4 |
| `small` | 36 s | 3 von 4 |

`base` verwechselt Kleinigkeiten („Nene mit" statt „Nenne mir") und stolpert
über Eigennamen („neben Schirm" statt „Nebenschirm"). Für kurze Kommandos
reicht das; wenn es im Alltag stört, ist `small` der nächste Schritt — er
kostet aber Faktor 3 an Zeit und rund 1,3 GB RAM. Umgestellt wird über
`JARVIS_STT_PORT`, ohne Codeänderung.

Piper für gesprochene Antworten ist vorbereitet, aber nicht gebaut. Ob das im
Alltag etwas bringt, zeigt sich erst mit ein paar Wochen Gebrauch.

## 0.1.0 — 24.08.2026

Erste lauffähige Fassung. Ein Auftrag geht per Weboberfläche oder REST-API rein
und wird von einem lokalen Modell über Ollama beantwortet.

**Orchestrator.** Job-Queue auf SQLite, die Neustarts übersteht.
Worker-Registry mit Heartbeat. Ein Scheduler, der Aufträge nach Priorität,
Zeitfenster, Tags, VRAM und Modell verteilt. REST-API für beide Seiten, dazu
eine automatisch erzeugte Beschreibung unter `/docs`.

**Aufträge werden verpachtet, nicht zugewiesen.** Wird der rechnende Rechner
mitten im Job ausgeschaltet, läuft der Lease ab und der Auftrag geht zurück in
die Queue, statt für immer auf `running` zu stehen. Der Heartbeat verlängert
ihn laufend, deshalb dürfen Jobs beliebig lange rechnen.

**Weboberfläche**, für das Handy gebaut: Auftrag absetzen, Liste, Detailseite
mit Antwort und Log. Aktualisiert sich von selbst und hört damit auf, sobald
der Auftrag fertig ist. Als PWA installierbar. Anmeldung über ein Token, das
als Cookie gespeichert wird.

**Worker** für Windows mit Ollama. Meldet seine Fähigkeiten selbst an und
fragt beim Start ab, welche Modelle wirklich vorhanden sind. Fehler landen im
Log des Auftrags, nicht im Nirvana.

**Ohne Worker läuft es trotzdem.** Ist die Grafikkarte aus, warten Aufträge in
der Queue, bis sich ein passender Worker meldet.

Was noch nicht da ist: Sprache, Home Assistant, Nacht-Jobs und Coding-Aufträge.
Siehe `docs/ROADMAP.md`.

### Zwei Fehler, die erst der Blick in den Browser gezeigt hat

Der Dienst blieb nach einigen Minuten unter Last stehen. Ursache war eine
geteilte Datenbankverbindung mit einer Sperre, die in einem anderen Thread
freigegeben wurde als sie genommen worden war — sie blieb dann dauerhaft zu.
Jetzt bekommt jede Anfrage ihre eigene Verbindung. Der neue Test
`tests/test_nebenlaeufig.py` fährt dafür einen echten Server hoch und feuert
parallele Anfragen dagegen; der übliche Testclient konnte das nicht zeigen,
weil er alles nacheinander im selben Thread abarbeitet.

Die Oberfläche zeigte alle Zeiten in UTC. Ein Auftrag von 17:01 stand als
15:01 da. Angezeigt wird jetzt Ortszeit, gespeichert weiterhin UTC.
