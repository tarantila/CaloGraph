# CaloGraph – Implementierungsplan

## Ziel

CaloGraph ist ein deutschsprachiges, selbst gehostetes Ernährungsdashboard für Daten aus Apple Health und YAZIO. Aktivitäts-, Flüssigkeits- und Gewichtsdaten sind bewusst ausgeschlossen. Apple Health wird niemals serverseitig oder aus iCloud abgefragt; autorisierte iPhone-Exporter übertragen Daten über die Import-API.

## Architektur

- Vue-3-SPA hinter Nginx; `/api` wird an FastAPI weitergereicht.
- FastAPI-Monolith mit getrennten API-, Auth-, Import-, Service- und Analytics-Schichten.
- PostgreSQL als einzige persistente Laufzeitabhängigkeit.
- Adapter normalisieren Health Auto Export, das CaloGraph-Syncformat, Apple-Health-XML und YAZIO-Exporter-JSON auf `health_samples`.
- Serverseitige Analytics liefern tägliche Summen, Wochenbudgets, Trends, Kalender- und Datenqualitätswerte.
- Mikronährstoff-Analytics vergleichen ausreichend abgedeckte Tagesmittel neutral mit EU-NRV-Orientierungswerten.

## Sicherheitsentscheidungen

- Argon2id-Passwörter, gehashte Import- und Session-Tokens, HttpOnly-Cookies und CSRF-Schutz.
- Keine Gesundheitswerte in Logs; optionale Rohpayloads sind komprimiert und zeitlich begrenzt.
- Begrenzte Payloads, defensive ZIP-Prüfung und XML-Parsing ohne DTD, Entities oder Netzwerk.
- Frontend und Backend standardmäßig nur über `127.0.0.1:8180` erreichbar; PostgreSQL bleibt intern.

## Umsetzung

1. Repository, gepinnte Manifeste, Container und Migrationen.
2. Authentifizierung, Importadapter, idempotente Speicherung und CLI.
3. Analytics-API und transparenter Status der Datenverfügbarkeit.
4. Deutsches responsives Dashboard mit Filtern und Importoberfläche.
5. Tests, synthetische Beispieldaten, CI, Backup und Betriebsdokumentation.
6. Benutzerverwaltung mit Einladungen, persönlichen Logins, eigener
   YAZIO-Verbindung und strikt getrennten Gesundheitsdaten.

## Abgrenzung

Native iOS-App, CSV-Import, Benachrichtigungen und Datenexport sind vorbereitet,
aber nicht Teil dieses MVP. Aktivitäts- und Flüssigkeitsauswertungen sind
bewusst kein Produktziel. Auch Gewichtsdaten werden weder importiert noch
ausgewertet. Manueller und automatischer YAZIO-Direktabruf sind
wegen der nicht dokumentierten Schnittstelle ausdrücklich experimentell. Der
Scheduler speichert jede Verbindung benutzerbezogen und verschlüsselt.

## Umsetzungsstand

Die sechs Ausbauschritte sind umgesetzt. Der verschlüsselte, benutzerbezogene
YAZIO-Scheduler läuft als eigener Compose-Dienst. Administratoren können
widerrufbare Einmal-Einladungen erstellen; eingeladene Personen erhalten einen
persönlichen Login, strikt getrennte Gesundheitsdaten und eine eigene
YAZIO-Verbindung. Verifiziert wurden Ruff, mypy, 28 Backendtests, ESLint,
Vue-Typecheck, 11 Frontendtests, der Vite-Produktionsbuild, die Docker-Images,
Alembic gegen PostgreSQL 18.4 sowie ein vollständiger Backup-/Restore-Test. Die
Beispielinstallation verwendet Port `127.0.0.1:8180`.
