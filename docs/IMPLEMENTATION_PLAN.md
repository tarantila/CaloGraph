# CaloGraph implementation plan

## Goal

CaloGraph is a self-hosted nutrition dashboard for Apple Health and YAZIO data.
Activity, hydration, and weight data are deliberately excluded. Apple Health
is never queried server-side or through iCloud; authorized iPhone exporters
send data through the import API.

The dashboard currently uses German as its primary interface language. English
localization and a per-account language preference are planned for a later
development phase.

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

A native iOS app, CSV import, notifications, data export, and interface
localization are prepared or planned but are not part of the current MVP.
Activity and hydration analytics are intentionally not product goals. Weight
data is neither imported nor analyzed. Manual and scheduled direct YAZIO
retrieval are explicitly experimental because they rely on an undocumented
interface. The scheduler stores every connection per user with encrypted
credentials.

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
localization, and deployment workflow mature toward a stable `1.0` release.
