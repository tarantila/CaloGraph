# CaloGraph


CaloGraph is a self-hosted, multi-user nutrition dashboard for Apple Health and
YAZIO data. It combines historically versioned budgets and targets, optional
activity-energy credits, trends, achievements and data-quality views without
moral judgment or telemetry.

CaloGraph is 0.x work in progress. It is not medical software and does not
diagnose, treat or prescribe. Do not use it as a substitute for professional
medical advice.

## Highlights

- **Multi-user by design:** each account has isolated imports, samples, targets,
  achievements, tokens and personal integrations.
- Historically effective calorie and macro budgets, trends, calendar and data
  quality views.
- Optional imported activity-energy credit; it does not create a workout,
  hydration or body-weight-history tracker.
- Portable ZIP export/import and separate CSV archive export (there is no CSV
  importer).
- Invitation-only account creation after the first administrator, with
  password, MFA and passkey options.

## Multi-user by design

Run one instance for multiple accounts. Health samples, imports, target history,
achievements, import tokens and each personal YAZIO connection are scoped to
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

### Experimental YAZIO integration

File import and direct/scheduled sync use an undocumented provider interface and
may stop after provider changes; Health Auto Export remains the recommended
default. Scheduled credentials are encrypted and user-scoped. Enabling direct
YAZIO sync deliberately communicates with YAZIO. CaloGraph is independent of
and not endorsed by YAZIO; `yazio-exporter` is a separately maintained
MIT-licensed dependency. See [YAZIO sync](docs/yazio-sync.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).

## Quick start (local/private)

Prerequisites: Docker Compose and a local checkout. This example is for a
loopback/private development instance, not an Internet-facing deployment.

```sh
cp .env.example .env
./scripts/init-secrets.sh
# Review .env; keep CALOGRAPH_PUBLIC_URL on the address you use

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose ps
```

Open <http://localhost:8180>. On an empty instance, the sign-in page offers
one-time creation of the first administrator. The browser setup creates only
the account; sign in to continue through the normal onboarding. The CLI remains
available when needed:

```sh
docker compose exec backend python -m app.cli create-user
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

## Privacy and security

CaloGraph ships no telemetry, analytics or CDN dependencies. Imported data is
stored in the operator’s deployment. Direct YAZIO sync is an explicit outbound
integration. Sessions, CSRF checks, trusted hosts/origins, password policy,
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
Apple Health / Health Auto Export / YAZIO
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
- [YAZIO synchronization](docs/yazio-sync.md)

Backend development uses Python 3.14 and the project lockfile; frontend
development uses the package lockfile. The repository CI workflow contains the
supported checks. Do not use development defaults for a public deployment.

## Limitations

- This is 0.x software; review migrations and maintain independent backups.
- There is no native iOS app and no server-side Apple Health/HealthKit iCloud
  retrieval. An authorized iPhone exporter is required for ongoing data, and
  iOS can delay background delivery.
- No medical diagnosis, treatment or automated nutrition advice is provided.
- Meal or recipe analysis is unavailable where a source does not provide
  reliable detail.
- There is no CSV importer; CSV is an export format only.
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
