<p align="center">
  <img
    src="frontend/public/branding/calograph-readme-banner.png"
    alt="CaloGraph logo and wordmark"
    width="720"
  >
</p>

<p align="center">
  <strong>Self-hosted nutrition and health analytics for Apple Health, Google Health, Withings and YAZIO</strong>
</p>

<p align="center">
  <a href="#quick-start-localprivate">Quick start</a> |
  <a href="docs/apple-health-setup.md">Apple Health</a> |
  <a href="docs/google-health-setup.md">Google Health</a> |
  <a href="docs/withings-setup.md">Withings</a> |
  <a href="docs/yazio-sync.md">YAZIO sync</a> |
  <a href="docs/production.md">Production</a> |
  <a href="#operations-and-development">Documentation</a>
</p>

---

CaloGraph is a self-hosted, multi-user nutrition and health dashboard for
Apple Health, Google Health, Withings, and YAZIO data. It combines daily
nutrition, versioned budgets and targets, optional activity-calorie credits,
trends, achievements, and data-quality views without moral judgment or external
telemetry.

> [!WARNING]
> **CaloGraph is under active development.** Version `0.x` releases may contain
> incomplete features, breaking changes, or migrations that require attention.
> Features and behavior may change between releases. Keep tested backups and
> review the changelog before updating.

<p align="center">
  <img
    src="docs/assets/dashboard-overview-v0.6.5.png"
    alt="CaloGraph nutrition dashboard showing calorie intake, remaining budget, macronutrient trends, weekly summary, and data status"
    width="924"
  >
</p>

## Highlights

- **Analysis V2:** a customizable analysis workspace for nutrition,
  micronutrients, activity and weight, with persistent reorderable cards,
  shared period controls, detailed charts, and meal and food drill-downs.
- **Google Health integration:** connect your own Google account through the
  official API to import available nutrition, active energy, and weight with
  personal OAuth credentials. See [Google Health setup](docs/google-health-setup.md).
- **Withings integration:** import weight, supported body composition, and
  daily active energy through a personal OAuth connection. See
  [Withings setup](docs/withings-setup.md).
- **Versioned nutrition analytics:** daily calories and macros, historically
  effective budgets and targets, calendar, coverage and data-quality views.
- **Activity-aware budgets:** optional imported activity-calorie credits can
  increase the effective daily calorie budget without turning CaloGraph into a
  workout tracker.
- **Multi-user by design:** each account has isolated imports, samples, targets,
  achievements, tokens and personal integrations.
- **Portable data:** ZIP export/import and separate CSV archive export are
  available; CSV import is not currently supported.
- **Managed account lifecycle:** the first administrator can be created through
  the browser, then further accounts are invitation-only with password, MFA and
  passkey options.

## Future development

CaloGraph is actively evolving. Planned areas of development include:

- **AI-assisted nutrition analysis:** use structured nutrition, meal and food
  data for deeper, opt-in analysis beyond charts and daily aggregates.
- **Richer Apple Health ingestion:** explore additional nutrition and meal-level
  data where the available Apple Health export paths provide reliable metadata.

These are planned development directions, not compatibility guarantees or
release commitments. External provider APIs and available data may change over
time.

## Multi-user by design

Run one instance for multiple accounts. Health samples, imports, target history,
achievements, import tokens and personal provider connections are scoped to
their owner. Administrators manage account metadata, lifecycle and one-time
invitations; the administration UI does not expose another account’s nutrition
values. The deployment operator controls the database, secrets and backups.
See [User management](docs/user-management.md).

## Data sources

### Recommended: Health Auto Export

For ongoing iPhone transfer, Health Auto Export sends the HealthKit categories
you authorize to your CaloGraph endpoint over HTTPS with an account-scoped
import token. iOS may delay background exports; CaloGraph cannot bypass those
platform restrictions. See [Apple Health setup](docs/apple-health-setup.md) and
the [import API](docs/import-api.md).

### Historical Apple Health

Upload Apple Health’s `export.xml` or ZIP in CaloGraph. Apple Health often lacks
reliable food, recipe and meal names, so CaloGraph analyzes daily nutrient totals
rather than inventing meal detail.

### Google Health

Connect your own Google Health account through the official API v4 and manually
synchronize available nutrition, active energy, and weight. Each user configures
their own Google Cloud OAuth client in **Konto → Integrationen**. The integration
is optional and disabled by default. See [Google Health setup](docs/google-health-setup.md)
for permissions, the exact callback, and Testing-mode token expiry.

### Withings

Connect a personal Withings account to manually synchronize weight, supported
body-composition measurements, and daily active energy. Client credentials and
OAuth authorization are scoped to the signed-in account. The integration is
optional and disabled by default. See [Withings setup](docs/withings-setup.md)
for developer-app registration, callback configuration, and supported metrics.

### Experimental YAZIO integration

Direct/scheduled sync uses only the pinned `yazio-sdk==0.4.0` integration with
an undocumented provider interface and may stop after provider changes; Health
Auto Export remains the recommended default. Scheduled credentials are encrypted
and user-scoped. Enabling direct YAZIO sync deliberately communicates with YAZIO.
Compatible historical `days.json` and `nutrients.json` files can be imported
independently of direct sync; `yazio-exporter` is only an optional external
producer of those files, not a runtime dependency. CaloGraph is independent of
and not endorsed by YAZIO. See [YAZIO sync](docs/yazio-sync.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).

## Quick start (local/private)

Prerequisites: Docker Compose and Git. This example is for a loopback/private
development instance, not an Internet-facing deployment.

```sh
git clone https://github.com/tarantila/CaloGraph.git
cd CaloGraph
cp .env.example .env
./scripts/init-secrets.sh
# Review .env; keep CALOGRAPH_PUBLIC_URL on the address you use

docker compose -f docker-compose-dev.yml up -d --build
docker compose -f docker-compose-dev.yml ps
```

`make dev` runs the same detached build/start command. The standalone
`docker-compose-dev.yml` starts PostgreSQL, the reload-enabled backend, the Vite
frontend, and `yazio-scheduler`; do not combine it with production Compose.
Local images default to `calograph-backend-dev:edge` and
`calograph-frontend-dev:edge`. Set `CALOGRAPH_DEV_VERSION` to change their local
tag independently of the production `CALOGRAPH_VERSION`.

Open <http://localhost:8180>. On an empty instance, the sign-in page offers
one-time creation of the first administrator. The browser setup creates only
the account; sign in to continue through the normal onboarding.

There is a CLI alternative available when needed:

```sh
docker compose -f docker-compose-dev.yml exec backend python -m app.cli create-user
```

After initialization, further accounts are created only through
**Administration → Einladungen**. See [User management](docs/user-management.md)
for lifecycle and recovery operations.

## Production deployment

`.env.example` is for local/private development and must not be exposed through
an Internet-facing proxy. Start production from
[`.env.production.example`](.env.production.example), use the final HTTPS
origin, keep `INITIAL_ADMIN_SETUP_ENABLED=false` unless a controlled first-run
window is explicitly intended, and follow the [production checklist](docs/production.md).
The [reverse-proxy guide](docs/reverse-proxy.md) covers TLS and upload limits.

The repository has exactly two Compose definitions: `docker-compose.yml` for
production and standalone `docker-compose-dev.yml` for development and isolated
checks. Production commands use only the production definition; no test runner,
test database, Vite server, or review service is added to production.

## Privacy and security

CaloGraph ships no telemetry, analytics or CDN dependencies. Imported data is
stored in the operator’s deployment. Direct Google Health, Withings, and YAZIO
synchronization are explicit outbound integrations. Sessions, CSRF checks,
trusted hosts/origins, password policy,
rate limits and security events are part of the application boundary; review
[SECURITY.md](SECURITY.md) and the [security monitoring guide](docs/security-monitoring.md).

Account exports contain only the authenticated user’s data and exclude
credentials, token hashes, sessions and other security material. The portable
format is documented in [Data export](docs/data-export.md). It is not a database
dump.

## Architecture and data provenance

Adapters normalize source records for analytics while retaining source type,
source name and source identifier with per-user deduplication. The same day
imported from Apple Health and direct YAZIO is not deduplicated across source
boundaries; choose one path for overlapping days. See
[architecture](docs/architecture.md), [data model](docs/data-model.md) and
[analytics definitions](docs/analytics-definitions.md).

```text
Apple Health / Health Auto Export / Google Health / Withings / YAZIO
                 │
                 ▼
       account-scoped import adapters
                 │  source provenance + deduplication
                 ▼
       versioned targets and analytics API
                 │
                 ▼
          account-scoped dashboard
```

## Operations and development

- [Architecture](docs/architecture.md)
- [User management](docs/user-management.md)
- [Production operations](docs/production.md)
- [Reverse proxy and TLS](docs/reverse-proxy.md)
- [Backup and restore](docs/backup-restore.md)
- [Security monitoring](docs/security-monitoring.md)
- [Threat model](docs/threat-model.md)
- [Supply-chain and image retention](docs/supply-chain.md)
- [Data export and portable import](docs/data-export.md)
- [Import API](docs/import-api.md)
- [Google Health setup](docs/google-health-setup.md)
- [Withings setup](docs/withings-setup.md)
- [YAZIO synchronization](docs/yazio-sync.md)

Backend development uses Python 3.15 and the project lockfile; frontend
development uses Node 26 and the package lockfile. The repository CI workflow
contains the supported checks. Do not use development defaults for a public
deployment.

### Isolated check workflows

The development definition includes the `test`, `e2e`, `verification`, and
`analysis-review` profiles. **Profiles select services; they do not isolate
projects, networks, volumes, secrets, or databases.** Never start a workflow with
profile-wide `up` (or `--profile '*' up`): unprofiled development services would
also start. Use the supported entrypoints, which select the exact services and
separate project/resources:

| Workflow | Entrypoint | Compose project |
| --- | --- | --- |
| Backend/frontend checks | `make test`, `make lint`, `make typecheck`, `make frontend-test` | `calograph-tests` |
| PostgreSQL regressions (also invoked by `make test`) | `./scripts/test-postgres.sh` | `calograph-postgres-tests` |
| Browser E2E | `make e2e` or `./scripts/e2e.sh` | `calograph-e2e` |
| Synthetic-data verification E2E | `./scripts/verification-e2e.sh` | `calograph-verification-e2e` |
| Persistent Analysis review | `make analysis-review-up` / `make analysis-review-down` | `calograph-analysis-v2-review` |

Keep these workflows on disposable or dedicated synthetic databases. A Compose
project name alone does not make a real source database safe; do not point
workflow settings or secret sources at production or synchronized health data.

Use `TEST_PROJECT`, `POSTGRES_TEST_PROJECT`, `E2E_PROJECT`,
`VERIFICATION_E2E_PROJECT`, or `PRODUCTION_SMOKE_PROJECT` to give checks a
unique disposable project name. E2E/Verification use the runtime backend and
nginx frontend (`:8080`), never Vite, with synthetic credentials and tmpfs
PostgreSQL; `E2E_USE_PREBUILT_IMAGES=true` retains the CI image path.
The supported tmpfs-workflow cleanup removes containers/networks without
`--volumes`. Do not use `down --volumes` with the development definition:
profiles do not protect the globally declared persistent Review volume.

### Analysis demo (disposable developer databases only)

Use `seed-analysis-demo` **only against a confirmed disposable development or
test database** (`ENVIRONMENT=development` or `ENVIRONMENT=test`). Never point
these commands at a real health database or a production instance. Create a
separate real account first; the dedicated non-admin
`calograph-analysis-demo` account cannot be the first user. From the backend
directory:

```sh
cd backend
uv run python -m app.cli seed-analysis-demo
uv run python -m app.cli seed-analysis-demo --include-current-day-supplement
uv run python -m app.cli seed-analysis-demo --include-follow-up-meals
uv run python -m app.cli remove-analysis-demo
```

The seed command prompts privately for a password only when creating the
account. After successful seeding, log in as `calograph-analysis-demo` with
that privately entered password. Rerunning does not reset the password or
replace the original 90-day seed anchor. The optional
`--include-current-day-supplement` flag adds today's synthetic
`Europe/Berlin` meals as one strictly audited supplement when the date lies
outside the original seed window. Repeating it for the same date is
idempotent; it does not move the anchor or create supplements for later dates.
`--include-follow-up-meals` additionally adds one strictly audited pudding
after dinner today and three meals tomorrow. It leaves the existing supplement
unchanged, preserves both days' projection history, and is idempotent for the
same date; it does not create supplements for later dates.
Removal refuses an account containing changes or references it cannot prove
belong to the seed and does not delete any other user's data. Repeating
removal when the account is absent is a no-op;
Changes made by the former legacy demo seeder to unmarked target histories are
**not automatically reversible**. Restore those from a known-good disposable
database snapshot if needed; the current removal command is not a legacy cleanup.

#### Persistent Analysis review runtime

From the repository root:

```sh
make analysis-review-up
```

This starts the isolated review site at `http://localhost:8289` and stores its
`calograph_analysis_v2_test` database in the named Docker volume
`calograph-analysis-v2-review-postgres-data`. On the first start, the existing
secret generator creates private review-only secrets, and the existing CLI
privately prompts for the synthetic bootstrap and demo account passwords.
Later starts migrate to the current Alembic head and skip seeding when the
demo account already exists, preserving its data and preferences.

The start command remains the supported restart path after a VM reboot. It
selects only the review services from the `analysis-review` profile under
`calograph-analysis-v2-review`, without the development scheduler or ordinary
development stack. Secrets persist outside the repository at
`${XDG_DATA_HOME:-$HOME/.local/share}/calograph/analysis-review/secrets`;
locations under the repository or `/tmp` are rejected. An existing database
volume must have this review project's Compose ownership labels and matching,
readable secrets; the script refuses an unowned volume rather than adopting or
resetting it. First initialization requires an interactive terminal for the
private password prompts.

```sh
make analysis-review-down
```

This removes only the review containers and networks; the database volume and
review secrets remain. No production `.env` or `:8288` runtime is used.

## Limitations

- This is 0.x software; review migrations and maintain independent backups.
- There is no native iOS app and no server-side Apple Health/HealthKit iCloud
  retrieval. An authorized iPhone exporter is required for ongoing data, and
  iOS can delay background delivery.
- CaloGraph is not medical software and does not provide medical diagnosis or
  treatment.
- There is no CSV importer yet; CSV is currently an export format only.
- Overlapping Apple Health and YAZIO days are intentionally not cross-source
  deduplicated; provenance remains distinct.
- The undocumented YAZIO interface is experimental and may break.

## Contributing

Before proposing changes, read the relevant architecture, security and data
export documentation. Keep account isolation, source provenance and the
non-medical boundary intact.

## License and provenance

CaloGraph source is available under
[PolyForm Noncommercial 1.0.0](LICENSE). This is not an OSI-approved open-source
license. Third-party components retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Brand assets

The wordmark and application logo in `frontend/public/branding/` are project
brand assets.
