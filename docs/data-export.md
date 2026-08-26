# Datenexport

Angemeldete Benutzer können unter **Konto → Datenexport** ein ZIP-Archiv ihrer
in CaloGraph gespeicherten fachlichen Daten herunterladen. Der Endpunkt exportiert
immer ausschließlich den aktuell angemeldeten Benutzer; weder Administratorrechte
noch URL-Parameter erweitern diesen Umfang.

## Format

Das Archiv verwendet das stabile Format `calograph-data-export` mit
`format_version` `1`. `manifest.json` enthält Format, Versionsnummer,
Erstellungszeitpunkt, Anwendung, Anwendungsversion und die enthaltenen Dateien.

| Datei | Inhalt |
| --- | --- |
| `profile.json` | Benutzerprofil und persönliche Anzeigeeinstellungen |
| `settings.json` | Tracking-Qualitätseinstellungen |
| `targets.json` | Aktuelle und historische Ernährungsziele |
| `tracking_overrides.json` | Manuelle Tracking-Overrides |
| `health_samples.jsonl` | Health-, Ernährungs- und Aktivitätsdaten als JSON Lines |
| `import_batches.jsonl` | Import-Historie als JSON Lines |
| `yazio.json` | YAZIO-Verbindungs- und Synchronisationsmetadaten ohne Credentials |
| `achievements.json` | Freigeschaltete Erfolge |

JSON Lines sind zeilenweise unabhängige JSON-Objekte. Dezimalwerte werden als
JSON-Strings ausgegeben, damit ihre gespeicherte Präzision erhalten bleibt.

## Datenschutz und Ausschlüsse

Der Export enthält weder Authentifizierungs- noch technische Sicherheitsdaten.
Ausgeschlossen sind insbesondere Passwort- und Token-Hashes, Sitzungen,
Recovery- und MFA-Daten, Passkey-Material, Rohimporte, interne Datenbank-IDs,
Verschlüsselungsdaten sowie verschlüsselte YAZIO-Zugangsdaten.
Der Export ist kein Datenbank-Dump. Das validierte CaloGraph-Archiv kann
über den dokumentierten Vorschau-/Importpfad in das aktuell angemeldete eigene
Konto eingespielt werden; dabei werden keine Authentifizierungsdaten oder
YAZIO-Zugangsdaten übernommen.

## Streaming und Parallelität

Das ZIP-Archiv wird während des Downloads gestreamt. Eine begrenzte
Producer-/Consumer-Queue mit höchstens 16 Einträgen und einer maximalen
Payload-Größe von 64 KiB pro Eintrag erzeugt Backpressure; das vollständige ZIP
wird weder im Arbeitsspeicher noch als temporäre Datei aufgebaut. Health
Samples, Import-Batches und größere JSON-Arrays werden inkrementell verarbeitet.

Pro Backend-Worker ist höchstens ein Datenexport gleichzeitig aktiv. Bei
mehreren Backend-Workern oder Instanzen können entsprechend mehrere Exporte
parallel laufen; es gibt kein deploymentweites Ein-Export-Limit.

Der Download wird browsernativ über den same-origin-Endpunkt gestartet; die
Session-Cookies werden automatisch verwendet und der Browser verarbeitet den
`Content-Disposition`-Dateinamen. Für den Startstatus verwendet die Oberfläche
eine kurzlebige, nicht zur Authentifizierung geeignete Korrelations-ID. Sie
enthält weder Zugangsdaten noch Exportdaten. Der Frontend-Nginx reicht die
API-Response ohne Response-Buffering weiter.

## CSV und Import

Unter **Konto → Meine Daten** steht zusätzlich ein separates CSV-ZIP für
menschenlesbare Auswertungen bereit. Es enthält UTF-8-CSV-Dateien für Profil,
Ziele, Tracking-Overrides und Samples; textuelle Zellen mit
Formelpräfixen werden gegen Spreadsheet-Formelausführung geschützt.

Eine CaloGraph-Datensicherung kann nach vollständiger Validierung zunächst als
Vorschau und anschließend atomar in das eigene Konto importiert werden. Das
Importformat ist ausschließlich `calograph-data-export` Version `1`. Profil-,
Tracking-, Ziel-, Override-, Achievement- und Sample-Daten werden übernommen;
Import-Historie und YAZIO-Synchronisationsmetadaten dienen nur der
Dokumentation und werden nicht automatisch wieder aktiviert. YAZIO-
Zugangsdaten und Authentifizierungsdaten werden niemals übernommen.
