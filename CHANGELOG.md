# Changelog

Was sich geändert hat, in verständlicher Sprache. Neueste Version oben.

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
