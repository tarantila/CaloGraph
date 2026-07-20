# CaloGraph – Implementierungsplan

## Ziel

CaloGraph ist ein deutschsprachiges, selbst gehostetes Dashboard für Ernährungs-, Aktivitäts- und Körperdaten aus Apple Health. Apple Health wird niemals serverseitig oder aus iCloud abgefragt; autorisierte iPhone-Exporter übertragen Daten über die Import-API.

## Architektur

- Vue-3-SPA hinter Nginx; `/api` wird an FastAPI weitergereicht.
- FastAPI-Monolith mit getrennten API-, Auth-, Import-, Service- und Analytics-Schichten.
- PostgreSQL als einzige persistente Laufzeitabhängigkeit.
- Adapter normalisieren Health Auto Export, das CaloGraph-Syncformat und Apple-Health-XML auf `health_samples`.
- Serverseitige Analytics liefern tägliche Summen, Wochenbudgets, Trends, Kalender- und Datenqualitätswerte.

## Sicherheitsentscheidungen

- Argon2id-Passwörter, gehashte Import- und Session-Tokens, HttpOnly-Cookies und CSRF-Schutz.
- Keine Gesundheitswerte in Logs; optionale Rohpayloads sind komprimiert und zeitlich begrenzt.
- Begrenzte Payloads, defensive ZIP-Prüfung und XML-Parsing ohne DTD, Entities oder Netzwerk.
- Frontend und Backend standardmäßig nur über `127.0.0.1:8180` erreichbar; PostgreSQL bleibt intern.

## Umsetzung

1. Repository, gepinnte Manifeste, Container und Migrationen.
2. Authentifizierung, Importadapter, idempotente Speicherung und CLI.
3. Analytics-API und konfigurierbare Vollständigkeitsheuristik.
4. Deutsches responsives Dashboard mit Filtern und Importoberfläche.
5. Tests, synthetische Beispieldaten, CI, Backup und Betriebsdokumentation.

## Abgrenzung

Native iOS-App, CSV- und direkter YAZIO-Import, Benachrichtigungen und Datenexport sind vorbereitet, aber nicht Teil dieses MVP.

## Umsetzungsstand

Das MVP ist umgesetzt. Verifiziert wurden Ruff, mypy, zwölf Backendtests, ESLint, Vue-Typecheck, acht Vitest-Tests, der Vite-Produktionsbuild, alle Docker-Images, Alembic gegen PostgreSQL 18.4, drei gesunde Compose-Container, der idempotente Playwright-End-to-End-Ablauf und ein lesbarer Custom-Format-Datenbankdump. Die Beispielinstallation verwendet Port `127.0.0.1:8180`.
