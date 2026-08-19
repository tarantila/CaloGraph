# CaloGraph implementation plan

## Goal

CaloGraph is a self-hosted nutrition dashboard for Apple Health and YAZIO data.
It optionally credits imported activity energy from one user-selected source to
the calorie budget; activity tracking, hydration, and weight remain outside the
product scope. Apple Health is never queried server-side or through iCloud;
authorized iPhone exporters send data through the import API.

The dashboard supports German and English. Public authentication pages are
always English; authenticated users can store their dashboard language in the
**Konto** profile.

## Architecture

- Vue 3 SPA behind Nginx; `/api` is proxied to FastAPI.
- FastAPI monolith with separate API, authentication, import, service, and
  analytics layers.
- PostgreSQL as the only persistent runtime dependency.
- Adapters normalize Health Auto Export, the CaloGraph sync format, Apple
  Health XML, and YAZIO exporter JSON into `health_samples`.
- Server-side analytics provide daily totals, weekly budgets, trends,
  calendar, and data-availability metrics.
- Micronutrient analytics compare sufficiently covered daily averages with EU
  NRV reference values using neutral language.

## Security decisions

- Argon2id passwords, hashed import and session tokens, HttpOnly cookies, and
  CSRF protection.
- No health values in logs; optional raw payloads are compressed and
  time-limited.
- Bounded payloads, defensive ZIP checks, and XML parsing without DTDs,
  entities, or network access.
- Frontend and backend are exposed only through `127.0.0.1:8180` by default;
  PostgreSQL remains internal.

## Implementation phases

1. Repository, pinned manifests, containers, and migrations.
2. Authentication, import adapters, idempotent storage, and CLI.
3. Analytics API and transparent nutrition-data availability.
4. Responsive German dashboard with filters and an import interface.
5. Tests, synthetic sample data, CI, backups, and operations documentation.
6. User management with invitations, personal logins, individual YAZIO
   connections, and strictly isolated health data.

## Scope boundaries

Full localization beyond the German and English dashboard and English public
authentication pages is planned but is not part of the current MVP. Activity
calorie credit is part of the current product scope: imported activity energy
from one user-selected source can raise the effective calorie budget. Broader
activity tracking, hydration analytics, and weight data remain outside scope.
Manual and scheduled direct YAZIO retrieval are explicitly experimental because
they rely on an undocumented interface. The scheduler stores every connection
per user with encrypted credentials.

## Current implementation status

The six initial expansion phases are implemented. The encrypted, user-scoped
YAZIO scheduler runs as a dedicated Compose service. Administrators can create
revocable one-time invitations; invited users receive a personal login,
strictly isolated health data, and their own YAZIO connection.

The authoritative release-check matrix is defined in
`.github/workflows/ci.yml`, and the latest CI runs are the source of truth for
the checks and test set that currently pass. The pipeline covers backend and
frontend quality checks, tests and builds, PostgreSQL migrations, container and
end-to-end validation, and the production backup/restore smoke path; exact test
counts are intentionally not duplicated here. The example installation uses
`127.0.0.1:8180`.

The project remains work in progress while its public API, migrations,
locale coverage, and deployment workflow mature toward a stable `1.0` release.
