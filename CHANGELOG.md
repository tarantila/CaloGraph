# Changelog

All notable changes to CaloGraph are documented in this file. The project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.2] - 2026-08-03

### Changed

- Disabled generated OpenAPI routes by default while keeping an explicit
  development and restricted-operator opt-in.
- Decoupled HSTS from `includeSubDomains` so descendant-host enforcement is a
  deliberate deployment decision.
- Reduced authentication database writes by persisting session and import-token
  activity at most once every five minutes.
- Added deployment-specific Nginx upload guidance and a JSON-log-compatible
  optional Fail2ban example.
- Made public GHCR `latest` images the Compose default while retaining release
  tag pinning and separate local development image names.
- Switched operational Compose services to the explicit
  `postgres:18.4-alpine` tag instead of a manifest digest.
- Kept private pre-publication image jobs green by deferring GitHub artifact
  attestations until the repository is public; release tags still publish both
  their version and `latest` with provenance and SBOM attestations.
- Added the PolyForm Noncommercial 1.0.0 source-available license, contribution
  terms, container license metadata, and explicit third-party attribution for
  the separately maintained MIT-licensed `yazio-exporter` dependency.
- Included the license and notice files shipped by production dependencies in
  both runtime images.
- Made release publication transactional: immutable commit images are scanned
  and attested before `edge`, version, or `latest` tags are promoted, and a
  release tag must match both application versions.
- Pinned the production environment template to `v0.2.2` while retaining the
  convenient `latest` Compose default for local and first-run setups.
- Made the documented Nginx HTTP redirect use the configured canonical host
  instead of reflecting the request's Host header.

## [0.2.1] - 2026-08-03

### Security

- Restricted forwarded-header trust to the exact frontend proxy address and
  made the bundled Nginx replace inbound forwarding and request identifiers at
  the application trust boundary.
- Added bounded request identifiers and structured, pseudonymous security
  events for authentication, throttling, account administration, YAZIO,
  uploads, and imports, together with monitoring and Fail2ban guidance.
- Replaced deterministic email-derived YAZIO source identifiers with opaque
  per-user identifiers and added a collision-safe migration for existing
  records without exposing the original email address.

## [0.2.0] - 2026-07-29

### Changed

- Consolidated backend and frontend container builds into a central,
  multi-stage Dockerfile with separate development, test, and runtime targets.
- Made backend image metadata, runtime UID/GID, worker count, logging, and
  Uvicorn timeouts configurable through Docker Compose.
- Routed containerized backend checks through a development-only image that
  includes the test, lint, and type-check dependencies.
- Excluded environment-specific `.env` variants from the shared Docker build
  context so frontend values cannot be embedded into production assets.
- Switched the frontend runtime to the purpose-built, unprivileged NGINX image
  and moved all writable PID and temporary paths to its ephemeral `/tmp`.

### Security

- Split login throttling into independent IP and account buckets, normalized
  IPv6 clients to `/64`, and made PostgreSQL counter updates atomic.
- Removed the login timing oracle with a dummy Argon2 verification, added
  temporary password-change throttling, pseudonymous authentication events,
  `Retry-After` responses, and automatic cleanup of expired buckets.
- Bounded historical imports with a 500-MiB upload limit, a single ephemeral
  tmpfs spool, proxy concurrency and application rate limits, streamed XML,
  capped parser diagnostics, and bulk database writes in 500-record batches.
- Preserved completed import checkpoints under the explicit `partial_failed`
  status so interrupted Apple Health imports can be retried idempotently.
- Wrapped the unofficial YAZIO client in a CaloGraph-owned transport with
  explicit network timeouts, rejected redirects, no authentication retries,
  and parent-enforced operation deadlines.
- Added independent per-user and per-IP limits for YAZIO credential setup and
  manual sync, PostgreSQL advisory-lock concurrency control, a temporary
  provider circuit breaker, account-specific credential-failure pauses, and a
  `YAZIO_ENABLED` operational kill switch without introducing Redis.
- Made the runtime environment explicit and added fail-closed production
  validation for HTTPS, secure cookies, HSTS, secrets, database credentials,
  request allowlists, YAZIO encryption, and upload-capacity relationships.
- Replaced the fixed Nginx upload ceiling and backend tmpfs size with validated
  byte settings while preserving the 500-MiB Apple Health and 2-GiB streamed
  ZIP defaults.
- Moved invitation secrets from URL paths into browser-only fragments, exchanged
  them for a signed ten-minute `HttpOnly` registration state, and removed query
  strings plus legacy invitation tokens from the bundled Nginx access log.
- Added typed validation boundaries for every JSON import format, enforced
  database-sized identifiers, IANA timezones, and decimal ranges before
  persistence, and converted malformed authenticated imports into safe `422`
  responses without reflecting submitted values.
- Segmented proxy, database, and scheduler-egress traffic across dedicated
  Docker networks, dropped unnecessary Linux capabilities from every runtime
  container, and added optional per-service memory, CPU, and PID ceilings.
- Replaced runtime secret environment values and password-bearing database
  URLs with service-scoped Compose Secret files, including role-specific
  production validation for the YAZIO scheduler.
- Added directly streamed `age` encryption for database and
  environment/secret-file backups, authenticated verification and restore,
  and non-destructive migration helpers for existing plaintext dumps and
  legacy `.env` secret values.
- Pinned destructive unit-test setup to an in-memory SQLite database even when
  the invoking shell exports a deployable `DATABASE_URL`, with a second
  fail-closed guard immediately before schema reset. PostgreSQL integration
  tests additionally require an explicit destructive-test opt-in, two identical
  URLs, an allowlisted local host, and a database name ending in `_test`.
- Restored one shared rate-limit HMAC secret for backend and scheduler YAZIO
  circuit-breaker buckets without exposing the browser session secret.
- Added an iterative pre-Pydantic limit over every raw YAZIO container entry
  and removed sorted day/micronutrient materialization.
- Raised new single-factor passwords to 15 characters, added a local hashed
  common-password blocklist, switched production sessions to a secure
  `__Host-` cookie, and added server-enforced idle/absolute expiry plus hourly
  session cleanup.
- Added optional TOTP authentication with a dedicated encryption key, strict
  short-lived login challenges, replay protection, one-time recovery codes,
  management rate limits, session revocation, and an explicit administrative
  recovery command.
- Added passwordless passkey authentication with discoverable credentials,
  required user verification, exact RP/origin binding, one-time database-backed
  challenges, sign-counter tracking, per-IP throttling, and account-level
  registration and revocation controls.
- Revalidate cached frontend authentication on every protected navigation and
  clear local user/CSRF state plus return to login when a protected API reports
  an expired or revoked session.
- Added regression coverage proving a successful password change invalidates
  every existing browser session before the new password can sign in.
- Pinned every GitHub Action and container base to immutable revisions, added
  weekly Dependabot maintenance, and forced the affected development-only
  `brace-expansion` tree onto the patched 5.0.8 release.
- Moved the backend build and runtime to pinned Alpine 3.23 after the current
  Debian slim base failed the strict image gate on 23 unfixed high/critical OS
  findings; both final runtime images now pass that gate without exclusions.
- Added production and full-tree npm/Python audits, full-history Gitleaks
  scanning, and blocking high/critical Trivy checks for both runtime images.
- Added exact-image browser and production-smoke gates before GHCR publication,
  SPDX SBOM artifacts, and Sigstore-backed build/SBOM attestations.
- Added conservative GHCR retention that preserves every release-tagged image
  and tagless attestation record while aging out old non-release builds.
- Restored YAZIO synchronization after strict import validation exposed binary
  float artifacts, while retaining range and precision rejection for unsafe
  values and no longer marking fully rejected payloads as successful syncs.

## [0.1.2] - 2026-07-27

### Added

- Added a dashboard screenshot to the README.
- Added an explicit 60-day YAZIO history backfill to the micronutrient
  analysis.
- Added an optional, versioned maintenance-calorie estimate to budget settings.

### Changed

- Standardized public project documentation on English while keeping the
  application interface German.
- Renamed the Compose files to the conventional `docker-compose.yml` and
  `docker-compose.dev.yml` filenames.
- Documented the work-in-progress status and planned per-account language
  selection.
- Added a canonical `CALOGRAPH_PUBLIC_URL` for externally shared links,
  including user invitations.
- Restricted Uvicorn forwarded-header handling to configured proxy networks
  instead of trusting every sender.
- Moved the micronutrient explanation below the values and made the EU
  reference bars and coverage requirements explicit.
- Changed the calendar to calendar-month navigation with budget-based
  green/orange/red classifications and clearer summary metrics.
- Added week and custom date ranges to weekday analysis and moved the calendar
  directly below weekday analysis in the sidebar.
- Reworked the public entry screen into a minimal sign-in method selection;
  credentials appear only after choosing password sign-in.

### Fixed

- Corrected YAZIO micronutrient values from their gram source unit to canonical
  milligrams or micrograms, including a migration for existing samples.
- Corrected calendar average calculations for decimal values returned by the
  API.
- Corrected the daily calorie average for decimal values returned by the API.

## [0.1.1] - 2026-07-23

### Added

- Personal user accounts with invitations and strictly isolated nutrition data.
- Manual and scheduled YAZIO sync with encrypted credentials, a six-hour
  interval, and randomized scheduling.
- Micronutrient analysis for vitamins and minerals with data coverage and a
  neutral EU NRV comparison.
- Versioned calorie and macronutrient budgets with accurate daily and weekly
  calculations.
- Operations, backup, restore, and update documentation with supporting
  scripts.

### Changed

- Redesigned the nutrition overview, weekly view, calendar, trends, and data
  quality screens.
- Data status now reports whether nutrition data exists and no longer treats
  low values as incomplete.
- Removed activity, hydration, and weight data from import, analysis, and the
  interface.
- Replaced placeholder branding and application icons with the CaloGraph
  assets.

### Security

- YAZIO credentials are stored encrypted.
- Imports, targets, and analytics are consistently scoped to their user.

[Unreleased]: https://github.com/tarantila/CaloGraph/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/tarantila/CaloGraph/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/tarantila/CaloGraph/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/tarantila/CaloGraph/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/tarantila/CaloGraph/compare/b4ca2cf...v0.1.1
