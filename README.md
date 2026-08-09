<p align="center">
  <img src="frontend/public/branding/calograph-readme-banner.png" alt="CaloGraph" width="680">
</p>

<h1 align="center">CaloGraph</h1>

<p align="center">
  <a href="CODE_OF_CONDUCT.md">Code of Conduct</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="LICENSE">License</a> ·
  <a href="SECURITY.md">Security Policy</a>
</p>

CaloGraph is a self-hosted nutrition dashboard for Apple Health and YAZIO data.
It puts individual days into the context of weekly budgets, trends,
micronutrients, and data coverage without moral judgment or external telemetry.
Activity, hydration, and weight data are deliberately outside the scope of the
project.

![CaloGraph nutrition dashboard with calorie, macronutrient, weekly summary, and data status views](docs/assets/dashboard-overview.png)

> [!WARNING]
> **Work in progress:** CaloGraph is under active development. Version `0.x`
> releases may contain incomplete features, breaking changes, or migration
> issues. Keep tested backups and review the changelog before updating. It is
> not medical software and must not be used for diagnosis or treatment.

> [!IMPORTANT]
> A server cannot retrieve Apple Health or HealthKit data from iCloud. An
> authorized iPhone app such as Health Auto Export must send the data to
> CaloGraph over HTTPS. A historical Apple Health export can alternatively be
> uploaded as XML or ZIP.

## Features

- Daily calories, protein, carbohydrates, and fat
- Micronutrient analysis for 13 vitamins and 13 minerals, including source
  coverage and a neutral EU NRV comparison
- Versioned nutrition targets and accurate weekly budgets
- 7-, 14-, and 28-day averages without treating missing days as zero
- Weekday analysis with mean, median, and percentiles
- Accessible calendar view with graded calorie-budget deviations
- Clear distinction between recorded and missing nutrition data
- Idempotent REST imports for Health Auto Export v2 and the CaloGraph sync format
- Defensive historical Apple Health XML/ZIP imports
- Experimental YAZIO JSON import, manual retrieval, encrypted scheduled sync,
  and dashboard-triggered sync
- Local authentication, CSRF protection, and hashed import tokens
- Fully local Docker Compose deployment without third-party analytics

## Architecture

```text
YAZIO → Apple Health on iPhone → iPhone exporter → HTTPS/JSON
                                                        ↓
Browser → Nginx/Vue (127.0.0.1:8180) → FastAPI → PostgreSQL
                   Apple Health XML/ZIP ↗
                        YAZIO days.json ↗
                    direct YAZIO sync ──↗
```

Import adapters normalize every source into the same `health_samples` model.
Analytics and the frontend do not depend on the original export format. See
[docs/architecture.md](docs/architecture.md) for details.

## Quick start

Requirements: Docker Engine with Docker Compose v2. PostgreSQL and Node do not
need to be installed on the host.

```bash
cp .env.example .env
scripts/init-secrets.sh
vim .env
docker compose pull
docker compose up -d --no-build --wait
docker compose ps
```

The standard template sets `ENVIRONMENT=development` and is intended only for
a loopback installation. For Internet-facing operation, start from
`.env.production.example`. Production startup fails closed when HTTPS, cookie,
secret, database, proxy, YAZIO encryption, or upload-capacity settings are
unsafe or inconsistent.

`scripts/init-secrets.sh` creates independent database, session, rate-limit,
YAZIO credential-encryption, and MFA-encryption secrets in the ignored
`secrets/` directory without printing them. Compose mounts only the specific
file required by each service under `/run/secrets`; secret values are not
stored in `.env` or placed in container environments. The database DSN is
assembled in memory from its non-secret connection fields and the mounted
password.

Existing installations that still keep direct secret values and
`DATABASE_URL` in `.env` can migrate them once without rotating the database
password or YAZIO key:

```bash
CONFIRM_SECRET_MIGRATION=calograph scripts/migrate-env-secrets.sh
```

The migration is fail-closed, never prints a value, and refuses to overwrite
an existing secret destination. Back up and inspect the installation before
running it. Installations already using the original four secret files add the
separate MFA key with:

```bash
CONFIRM_MFA_SECRET_MIGRATION=calograph scripts/migrate-mfa-secret.sh
```

YAZIO key handling is described in
[docs/yazio-sync.md](docs/yazio-sync.md).

`CALOGRAPH_PUBLIC_URL` is the canonical browser address used for links that
leave the application, especially user invitations. Keep the local default for
a loopback-only installation. When using a reverse proxy, set it to the final
HTTPS origin. Reserved `example.com`, `example.net`, and `example.org`
hostnames are documentation placeholders and are rejected in production. The
configured hostname and origin are automatically added to the effective
request allowlists.
Production nevertheless requires both values to be listed explicitly in
`TRUSTED_HOSTS` and `TRUSTED_ORIGINS`, making the deployed policy auditable.

The backend container applies pending Alembic migrations before it starts. The
application is then available at
[http://127.0.0.1:8180](http://127.0.0.1:8180).

Compose pulls the public `latest` application images from GHCR by default. The
production template instead pins `CALOGRAPH_VERSION` to its matching tested
`vX.Y.Z` release. Review and update that value deliberately when upgrading.
Contributors can build the checked-out source with `make dev` or
`docker compose up -d --build`.

Both application images are built from the central multi-stage
[`Dockerfile`](Dockerfile). Compose selects separate backend and frontend
targets, so the final images still contain only their respective runtime. Both
run as non-root users. Backend process settings such as `WEB_CONCURRENCY`,
`UVICORN_LOG_LEVEL`, and the Uvicorn timeouts can be overridden in `.env`
without rebuilding the image. Build labels and the backend UID/GID can
optionally be overridden with `CALOGRAPH_VERSION`, `CALOGRAPH_UID`, and
`CALOGRAPH_GID`.

GitHub CI builds, scans, tests, signs, and publishes release images to GHCR.
Every successful release tag publishes both its `vX.Y.Z` tag and `latest`.
Override the image names only when using a mirror or fork.
The immutable pins, SBOMs, provenance verification, and retention policy are
documented in [docs/supply-chain.md](docs/supply-chain.md).

### Create the first user

```bash
docker compose exec backend python -m app.cli create-user
```

The command prompts for a username and password. Initial passwords must contain
at least 15 characters and must not occur in CaloGraph's bundled common-password
blocklist. The first user becomes an administrator automatically and can create
one-time invitation links under **Konto → Benutzerverwaltung**.

Each user can optionally enable TOTP under **Konto →
Zwei-Faktor-Authentifizierung**. CaloGraph shows ten one-time recovery codes
during activation. Users can also enroll one or more passkeys under **Konto →
Passkeys** and then sign in passwordlessly with their device's biometric check
or PIN. Passkeys require HTTPS, except for browser-recognized localhost
development.

An operator can recover an account whose authentication factors are unavailable
with:

```bash
docker compose exec backend python -m app.cli reset-mfa \
  --username USERNAME --confirm USERNAME
```

The command removes TOTP, recovery codes, and passkeys, then revokes every
session for that account.

### Create an import token

```bash
docker compose exec backend python -m app.cli create-import-token
```

The token is displayed once and stored only as an HMAC-SHA-256 hash. It can be
revoked later under **Konto → Import-Tokens**.

## iPhone import

Create a REST API automation in Health Auto Export with these settings:

- Data type: Health Metrics
- Metrics: nutrition only; do not select activity, steps, hydration, or weight
- Format: JSON, Export Version 2
- Summary: disabled where the data volume permits
- Range: previous seven days, so delayed changes are sent again
- URL: `https://your-host.example/api/v1/import/apple-health`
- Header: `Authorization: Bearer cg_YOUR_TOKEN`
- Optional: `X-Client-Identifier: my-iphone`

Example request using deliberately fake data:

```bash
curl --fail-with-body \
  --request POST \
  --header 'Authorization: Bearer cg_FAKE_EXAMPLE_TOKEN_DO_NOT_USE' \
  --header 'Content-Type: application/json' \
  --header 'X-Client-Identifier: example-iphone' \
  --data-binary @examples/health-auto-export-v2.json \
  https://calograph.example/api/v1/import/apple-health
```

Payloads can be validated without storing them:

```bash
curl --fail-with-body \
  --request POST \
  --header 'Authorization: Bearer cg_FAKE_EXAMPLE_TOKEN_DO_NOT_USE' \
  --header 'Content-Type: application/json' \
  --data-binary @examples/health-auto-export-v2.json \
  https://calograph.example/api/v1/import/apple-health/validate
```

The source-neutral format intended for a future native iOS app is documented in
[docs/import-api.md](docs/import-api.md).

## Historical import

In Apple Health, select **Profile → Export All Health Data**. Upload the
resulting ZIP or its `export.xml` file under **Importe**. CaloGraph validates
file sizes, paths, compression ratios, and XML safety. Parsing is streamed, and
repeated imports do not create duplicates. Apple Health uploads are limited to
500 MiB by default. Accepted values are persisted in batches of 500 instead of
being materialized as one large in-memory import.

If a large import stops after one or more committed batches, its status is
`partial_failed` (**Teilweise importiert** in the interface). The completed
batches remain available; uploading the same file again safely continues the
idempotent import without duplicating those values.

A completely synthetic example is available at
`examples/apple-health-export.xml`.

Apple Health supplies measurements and their sources, but often does not
include reliable food, recipe, or meal names. CaloGraph therefore does not
require them.

## Experimental YAZIO import

CaloGraph can import `days.json` and `nutrients.json` files produced by
[`yazio-exporter`](https://github.com/aleksandr-bogdanov/yazio-exporter) from
the **Importe** page. Calories, macronutrients, and supported micronutrients are
aggregated per day. Product, recipe, and meal names are not stored. Direct sync
retrieves 13 vitamins and 13 minerals through the exporter's separate nutrient
endpoints.

A manual direct import retrieves the previous 60 days by default:

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --email name@example.com
```

The password is requested without echo. Passwords and access tokens remain in
memory only for the duration of this command. Use
`--from-date YYYY-MM-DD --end-date YYYY-MM-DD` for a historical range; one
request is limited to 366 days.

This integration relies on an undocumented YAZIO interface and may stop working
after provider-side changes. Health Auto Export remains the recommended
default. Do not import the same days from Apple Health and directly from YAZIO,
because separate sources are intentionally not deduplicated across source
boundaries. Explicit request and operation deadlines, per-user and per-IP rate
limits, PostgreSQL-backed concurrency control, a temporary circuit breaker, and
the `YAZIO_ENABLED` kill switch protect direct access without requiring Redis.
See [docs/yazio-sync.md](docs/yazio-sync.md).

## Demo data

After creating a user, generate 120 completely synthetic days:

```bash
docker compose exec backend python -m app.cli seed-demo-data --username admin
```

The seed includes weekend patterns, missing days, varied intake, a target
change, and the daylight-saving-time transition in Berlin. It never runs
automatically.

## Development and quality

```bash
make dev
make test
make lint
make typecheck
make frontend-test
make build
make e2e
```

Alternatively, run the tools inside their respective directories:

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

The development template enables OpenAPI documentation at `/api/docs` and
`/api/openapi.json`. Production disables both endpoints by default through
`ENABLE_API_DOCS=false`; they can be enabled deliberately for a restricted
operator or API-client network.

The regular backend test suite always replaces an inherited `DATABASE_URL`
with an in-memory SQLite database before importing the application. PostgreSQL
integration tests are isolated behind `./scripts/test-postgres.sh`; their
destructive schema reset requires an explicit opt-in and a local database name
ending in `_test`.

## Migrations and updates

```bash
export BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt
BACKUP_DIR=/srv/calograph-backups \
  BACKUP_SECRETS=1 \
  scripts/update-containers.sh
```

The script first creates an `age`-encrypted database backup without a
plaintext temporary dump. `BACKUP_SECRETS=1` additionally encrypts `.env` and
the service-scoped files under `secrets/`. Key generation, off-host storage,
verification, and restore instructions are in
[docs/backup-restore.md](docs/backup-restore.md).

The backend refuses to start when migrations fail.

## Privacy and operations

- Health values and payloads are excluded from normal logs.
- Raw JSON payload retention is disabled by default
  (`RAW_PAYLOAD_RETENTION_DAYS=0`); large XML/ZIP uploads are not duplicated in
  the database.
- PostgreSQL has no host port mapping.
- The frontend binds to `127.0.0.1:8180` by default.
- `ENVIRONMENT` is mandatory. `production` requires an HTTPS public URL,
  secure cookies, HSTS, non-default independent secrets, and exact request
  allowlists before the backend or scheduler starts.
- TLS terminates at a reverse proxy. Never expose a configuration using
  `ENVIRONMENT=development` through that proxy.
- Forwarded headers are accepted only from the fixed frontend proxy IP.
  Compose derives an exact `/32` from `CALOGRAPH_FRONTEND_PROXY_IP`, and
  production rejects subnet-wide trust.
- No CDN dependencies, telemetry, external analytics, or third-party data
  transfer.

See [SECURITY.md](SECURITY.md), [docs/threat-model.md](docs/threat-model.md),
and [docs/reverse-proxy.md](docs/reverse-proxy.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Import API](docs/import-api.md)
- [Apple Health and exporter setup](docs/apple-health-setup.md)
- [Analytics definitions](docs/analytics-definitions.md)
- [Threat model](docs/threat-model.md)
- [Backup and restore](docs/backup-restore.md)
- [Production Docker operation](docs/production.md)
- [User management](docs/user-management.md)
- [Reverse proxy](docs/reverse-proxy.md)
- [Software supply chain](docs/supply-chain.md)
- [Future native iOS sync](docs/native-ios-sync-future.md)

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and
the project-specific [Code of Conduct](CODE_OF_CONDUCT.md) before opening a
pull request. Report vulnerabilities privately according to
[SECURITY.md](SECURITY.md).

## License

CaloGraph's original code and assets are licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE). You may use, modify, and
redistribute them for noncommercial purposes; commercial use is not permitted.
This is a source-available license, not an OSI-approved open-source license.

External dependencies retain their own licenses. In particular,
`yazio-exporter` is a separately maintained MIT-licensed dependency and is not
CaloGraph-owned code. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Brand assets

The original CaloGraph logos are stored in
[`frontend/public/branding`](frontend/public/branding):

- `calograph-logo-long.png` — horizontal logo
- `calograph-app-logo.png` — app and store logo
- `calograph-icon.png` — isolated color icon
- `calograph-logo-monochrome.png` — monochrome variant

The frontend uses an optimized 256-pixel app image and a theme-independent
README banner derived from these originals.

## Current limitations and roadmap

- The dashboard UI is currently German. English localization and a per-account
  language preference are planned, but not implemented yet.
- No native iOS app and no access to a supposed Apple Health cloud API.
- Scheduled YAZIO sync uses an undocumented interface and may break after
  changes made by YAZIO.
- No CSV importer.
- No meal analysis without corresponding source data.
- No medical diagnosis or automated nutrition advice.
- Horizontal multi-host deployment and Kubernetes are not planned.
