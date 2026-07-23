<p align="center">
  <img src="frontend/public/branding/calograph-readme-banner.png" alt="CaloGraph" width="680">
</p>

# CaloGraph

CaloGraph ist ein selbst gehostetes, deutschsprachiges Ernährungsdashboard für Daten aus Apple Health und YAZIO. Es setzt einzelne Tage in den Kontext von Wochenbudgets, Trends, Mikronährstoffen und Datenabdeckung – ohne moralische Bewertung und ohne externe Telemetrie. Aktivitäts-, Flüssigkeits- und Gewichtsdaten gehören bewusst nicht zum Funktionsumfang.

> **Wichtige Einschränkung:** Ein Server kann Apple Health beziehungsweise HealthKit nicht aus iCloud abrufen. Eine autorisierte iPhone-App wie Health Auto Export muss die Daten per HTTPS an CaloGraph senden. Alternativ kann ein historischer Apple-Health-Export als XML oder ZIP hochgeladen werden.

## Funktionen

- Tageswerte für Kalorien, Eiweiß, Kohlenhydrate und Fett
- Mikronährstoffanalyse für 13 Vitamine und 13 Mineralstoffe mit Datenabdeckung und neutralem EU-NRV-Vergleich
- historisierte Ernährungsziele und korrekte Wochenbudgets
- 7-, 14- und 28-Tage-Mittelwerte ohne Umdeutung fehlender Tage zu null
- Wochentagsanalyse mit Mittelwert, Median und Perzentilen
- barrierearme Kalenderansicht mit abgestuften Abweichungen vom Kalorienbudget
- verständliche Kennzeichnung vorhandener und fehlender Ernährungsdaten
- idempotenter REST-Import für Health Auto Export v2 und das CaloGraph-Syncformat
- sicherer historischer Apple-Health-XML-/ZIP-Import
- experimenteller YAZIO-JSON-Import, manueller Direktabruf sowie verschlüsselter automatischer und per Dashboard auslösbarer Sync
- lokaler Login, CSRF-Schutz und gehashte Import-Tokens
- vollständig lokaler Docker-Compose-Betrieb ohne Drittanbieter-Analytics

## Architektur

```text
YAZIO → Apple Health auf dem iPhone → iPhone-Exporter → HTTPS/JSON
                                                        ↓
Browser → Nginx/Vue (127.0.0.1:8180) → FastAPI → PostgreSQL
                 Apple-Health-XML/ZIP ↗
                      YAZIO days.json ↗
                  YAZIO-Direktabruf ──↗
```

Die Importadapter normalisieren alle Quellen in dieselbe `health_samples`-Struktur. Analytics und Frontend kennen das ursprüngliche Exportformat nicht. Details stehen in [docs/architecture.md](docs/architecture.md).

## Schnellstart

Voraussetzungen: Docker Engine mit Docker Compose v2. PostgreSQL oder Node müssen auf dem Host nicht installiert sein.

```bash
cp .env.example .env
vim .env
docker compose up -d --build
docker compose ps
```

Erzeuge für `POSTGRES_PASSWORD`, `SESSION_SECRET` und `RATE_LIMIT_SECRET` jeweils unabhängige zufällige Werte. `SESSION_SECRET` und `RATE_LIMIT_SECRET` müssen mindestens 32 Zeichen lang sein. Für den automatischen YAZIO-Sync wird zusätzlich ein eigener `CREDENTIAL_ENCRYPTION_KEY` benötigt; die Erzeugung ist in [docs/yazio-sync.md](docs/yazio-sync.md) beschrieben.

Der Backend-Container führt ausstehende Alembic-Migrationen vor dem Start kontrolliert aus. Die Anwendung ist anschließend unter [http://127.0.0.1:8180](http://127.0.0.1:8180) erreichbar.

### Ersten Benutzer anlegen

```bash
docker compose exec backend python -m app.cli create-user
```

Der Befehl fragt Benutzername und Passwort interaktiv ab. Ein Initialkennwort muss mindestens zwölf Zeichen lang sein.
Der erste Benutzer wird automatisch Administrator und kann anschließend unter
**Konto → Benutzerverwaltung** einmalige Einladungslinks erstellen.

### Import-Token erzeugen

```bash
docker compose exec backend python -m app.cli create-import-token
```

Das Token wird nur einmal angezeigt und in der Datenbank ausschließlich als HMAC-SHA-256-Hash gespeichert. Es kann später unter **Konto → Import-Tokens** widerrufen werden.

## iPhone-Import

In Health Auto Export eine REST-API-Automation mit folgenden Werten anlegen:

- Datenart: Health Metrics
- Metriken: nur Ernährung; Aktivität, Schritte, Flüssigkeit und Gewicht nicht auswählen
- Format: JSON, Export Version 2
- Zusammenfassung: aus, soweit die Datenmenge praktikabel bleibt
- Zeitraum: vorherige sieben Tage, damit verspätete Änderungen erneut übertragen werden
- URL: `https://dein-host.example/api/v1/import/apple-health`
- Header: `Authorization: Bearer cg_DEIN_TOKEN`
- optional: `X-Client-Identifier: mein-iphone`

Ein funktionierender Aufruf mit offensichtlich fiktiven Daten:

```bash
curl --fail-with-body \
  --request POST \
  --header 'Authorization: Bearer cg_FAKE_EXAMPLE_TOKEN_DO_NOT_USE' \
  --header 'Content-Type: application/json' \
  --header 'X-Client-Identifier: beispiel-iphone' \
  --data-binary @examples/health-auto-export-v2.json \
  https://calograph.example/api/v1/import/apple-health
```

Payloads können ohne Speicherung geprüft werden:

```bash
curl --fail-with-body \
  --request POST \
  --header 'Authorization: Bearer cg_FAKE_EXAMPLE_TOKEN_DO_NOT_USE' \
  --header 'Content-Type: application/json' \
  --data-binary @examples/health-auto-export-v2.json \
  https://calograph.example/api/v1/import/apple-health/validate
```

Das neutrale Format für eine spätere eigene iOS-App ist in [docs/import-api.md](docs/import-api.md) beschrieben.

## Historischer Import

In Apple Health **Profil → Gesundheitsdaten exportieren** wählen. Die erhaltene ZIP-Datei oder die enthaltene `export.xml` kann unter **Importe** hochgeladen werden. CaloGraph prüft Dateigröße, Pfade, Kompressionsverhältnis und XML-Sicherheit. Der Parser arbeitet streamend; eine wiederholte Ausführung erzeugt keine Duplikate.

Ein vollständig synthetisches XML-Beispiel liegt unter `examples/apple-health-export.xml`.

Apple Health liefert Messwerte, aber häufig keine Lebensmittel-, Rezept- oder Mahlzeitennamen. CaloGraph erwartet diese Angaben daher nicht.

## Experimenteller YAZIO-Import

CaloGraph kann die `days.json` von
[`yazio-exporter`](https://github.com/aleksandr-bogdanov/yazio-exporter)
über die Seite **Importe** einlesen. Kalorien, Makro- und unterstützte
Mikronährstoffe werden je Tag aggregiert; Produkt-, Rezept- und Mahlzeitennamen
werden nicht gespeichert. Der Direktabruf übernimmt 13 Vitamine und 13
Mineralstoffe über die separaten Nährstoffendpunkte des Exporters.

Ein manueller Direktabruf der letzten 60 Tage ist ebenfalls möglich:

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --email name@example.com
```

Das Passwort wird verdeckt abgefragt. Passwort und Zugriffstoken bleiben nur für
die Dauer des Befehls im Arbeitsspeicher. Ein historischer Bereich kann mit
`--from-date YYYY-MM-DD --end-date YYYY-MM-DD` abgerufen werden; pro Aufruf sind
höchstens 366 Tage erlaubt.

Dieser Weg nutzt eine nicht dokumentierte YAZIO-Schnittstelle und kann nach
Änderungen auf Anbieterseite ausfallen. Health Auto Export bleibt deshalb der
empfohlene Standard. Dieselben Tage sollten nicht gleichzeitig aus Apple Health
und direkt aus YAZIO importiert werden, weil getrennte Quellen bewusst nicht
quellenübergreifend dedupliziert werden. Details stehen in
[docs/yazio-sync.md](docs/yazio-sync.md).

## Demodaten

Nach der Benutzeranlage können 120 vollständig synthetische Tage erzeugt werden:

```bash
docker compose exec backend python -m app.cli seed-demo-data --username admin
```

Der Seed enthält Wochenendmuster, Datenlücken, unterschiedlich große Esstage, eine Zieländerung und den Berliner Wechsel zur Sommerzeit. Er läuft niemals automatisch.

## Entwicklung und Qualität

```bash
make dev
make test
make lint
make typecheck
make frontend-test
make build
make e2e
```

Alternativ lokal im jeweiligen Verzeichnis:

```bash
cd backend
uv sync --frozen --all-extras
uv run pytest
uv run ruff check app tests
uv run mypy app

cd ../frontend
npm ci
npm run lint
npm run typecheck
npm run test:unit
npm run build
```

Die OpenAPI-Dokumentation liegt nach dem Start unter `/api/docs`.

## Migrationen und Updates

```bash
BACKUP_DIR=/sicherer/verschluesselter/pfad \
  BACKUP_SECRETS=1 \
  scripts/update-containers.sh
```

Das Skript erstellt zuerst ein geprüftes Datenbankbackup. `BACKUP_SECRETS=1`
darf nur mit einem verschlüsselten Backupziel verwendet werden und sichert dann
zusätzlich die für YAZIO und Sitzungen benötigte `.env`. Details und Restore
stehen in [docs/backup-restore.md](docs/backup-restore.md).

Das Backend startet nicht, wenn Migrationen fehlschlagen.

## Datenschutz und Betrieb

- Gesundheitswerte und Payloads erscheinen nicht in normalen Logs.
- Rohpayload-Speicherung für JSON-Importe ist standardmäßig deaktiviert (`RAW_PAYLOAD_RETENTION_DAYS=0`); große XML-/ZIP-Uploads werden nicht zusätzlich in der Datenbank dupliziert.
- PostgreSQL besitzt kein Host-Port-Mapping.
- Frontend ist standardmäßig nur an `127.0.0.1:8180` gebunden.
- TLS wird am vorgeschalteten Reverse Proxy terminiert; erst dann `COOKIE_SECURE=true` und `ENABLE_HSTS=true` setzen.
- Keine CDN-Abhängigkeit, Telemetrie, externe Analytics oder Datenübertragung an Dritte.

Siehe [SECURITY.md](SECURITY.md), [docs/threat-model.md](docs/threat-model.md) und [docs/reverse-proxy.md](docs/reverse-proxy.md).

## Dokumentation

- [Architektur](docs/architecture.md)
- [Datenmodell](docs/data-model.md)
- [Import-API](docs/import-api.md)
- [Apple-Health-/Exporter-Einrichtung](docs/apple-health-setup.md)
- [Analytics-Definitionen](docs/analytics-definitions.md)
- [Threat Model](docs/threat-model.md)
- [Backup und Restore](docs/backup-restore.md)
- [Produktiver Docker-Betrieb](docs/production.md)
- [Benutzerverwaltung](docs/user-management.md)
- [Reverse Proxy](docs/reverse-proxy.md)
- [Zukünftige native iOS-Synchronisierung](docs/native-ios-sync-future.md)

## Markenassets

Die originalen CaloGraph-Logos liegen unter
[`frontend/public/branding`](frontend/public/branding):

- `calograph-logo-long.png` – horizontales Logo
- `calograph-app-logo.png` – App- und Store-Logo
- `calograph-icon.png` – freigestelltes Farbsymbol
- `calograph-logo-monochrome.png` – monochrome Variante

Für das Frontend werden eine optimierte 256-Pixel-App-Variante und ein
theme-unabhängiger README-Banner aus diesen Originaldateien erzeugt.

## Bekannte Grenzen des MVP

- keine native iOS-App und kein Zugriff auf eine angebliche Apple-Health-Cloud-API
- die automatische YAZIO-Synchronisierung nutzt eine nicht dokumentierte
  Schnittstelle und kann durch Änderungen bei YAZIO ausfallen
- kein CSV-Importer
- keine Mahlzeitenanalyse ohne entsprechende Quelldaten
- keine medizinischen Diagnosen oder automatische Ernährungsberatung
- horizontaler Multi-Host-Betrieb und Kubernetes sind nicht vorgesehen
