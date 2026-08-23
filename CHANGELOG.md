# Changelog

All notable changes to CaloGraph are documented in this file. The project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.2] - 2026-08-23

### Fixed

- Fixed activity-aware calorie labels, tables, and budget series so activity-specific values are shown only when activity calories were actually credited, including historical mixed periods.
- Fixed the sidebar version display by deriving it from the frontend package version and added regression coverage to prevent hardcoded release versions.

### Changed

- Increased the default session idle timeout from 24 hours to seven days while keeping the 30-day absolute session lifetime.
- Updated the development environment's pip to 26.2.1 so the complete dependency audit no longer reports PYSEC-2026-3721.

## [0.4.1] - 2026-08-20

### Changed

- Updated the backend runtime server to Uvicorn 0.52.3.
- Refreshed the backend test, type-checking, and linting toolchain.
- Updated the GitHub Actions uv setup integration to v10.0.1.
- Updated frontend Intlify development types to 11.4.8.

## [0.4.0] - 2026-08-19

### Added

- Added user-scoped, historically versioned activity-energy credit from one
  selected Apple Health, Health Auto Export, or YAZIO source to effective
  calorie budgets, including source-aware analytics, calendar and weekly
  presentation.
- Added an all-time Trends budget balance with tracked, within-budget,
  over-budget, and over-maintenance day counts using historically effective
  budgets.

### Changed

- Finalized activity-credit presentation across overview, target history, daily
  view, calendar, weekly detail, and trends while keeping weekday analysis and
  data-status/import pages focused on their existing aggregations.
- Updated the README and scope documentation; production examples continue to
  default to `latest` with optional `CALOGRAPH_VERSION=vX.Y.Z` pinning.

### Fixed

- Correctly convert small-calorie (`cal`) energy units to kilocalories.
- Clarify that the Daily nutrition-source filter does not override the
  independently versioned activity source.
- Bound all-time Trends budget-balance processing to tracked nutrition days
  instead of materializing every calendar day across the full history.
- Restored `mg`/`µg` to gram conversions while retaining the energy-unit
  normalization.
- Clarified that Trends budget-balance classes are mutually exclusive.

## [0.3.8] - 2026-08-16

### Added

- Added user-scoped achievements with historical reconciliation, progress,
  hidden discoveries, and localized frontend cards.

### Changed

- The production environment template now leaves `CALOGRAPH_VERSION` unset
  by default, so Compose uses the public `latest` image until an operator
  deliberately selects a reviewed release tag.
- Grouped imports, data status, budgets and targets, and account navigation at
  the bottom of the sidebar while keeping analytics and achievements in the
  primary navigation.

### Security

- Hardened achievement reconciliation against stale revoked sessions, hidden
  achievement metadata disclosure, and expensive repeated full-history scans.
  Achievement listing and reconciliation now share user- and IP-based limits.

## [0.3.7] - 2026-08-15

### Changed

- Compatible maintenance dependencies were refreshed without introducing
  deferred major-version upgrades.
- Release tagging and GitHub Release creation now use the scoped repository
  owner release identity while the normal GitHub Actions token remains
  restricted for release metadata operations.

### Fixed

- The daily view shows the newest day first by default and allows every visible
  column to be sorted with deterministic handling of missing values.
- Desktop and mobile CaloGraph branding now link back to the overview.

### Security

- Apple Health ZIP imports process `export.xml` through a single bounded
  decompression stream and roll back failed ZIP imports atomically.
- The expanded ZIP budget is reduced to 512 MiB.
- Large Apple Health uploads are limited to one concurrent connection per
  trusted client IP and two globally, with the backend temporary-storage
  budget validated for that concurrency.

## [0.3.6] - 2026-08-14

### Changed

- CI now builds runtime images once and publishes the exact tested and scanned
  artifacts instead of rebuilding them during publication.
- Releases now use a gated manual promotion workflow that validates the exact
  successful main build, immutable image digests, provenance, and SPDX
  attestations before promoting version and latest tags.

### Fixed

- CSRF tokens now remain stable across browser tabs within the same session,
  preventing one tab from invalidating another tab's authenticated mutations.
- Stale CSRF state can recover once after an explicitly confirmed CSRF
  validation failure without retrying mutations after transport, origin,
  authentication, or server failures.

## [0.3.5] - 2026-08-14

### Added

- Account settings now allow users to change their password securely.
- Budget and target history entries can now be removed with protected history
  relinking and confirmation.

### Changed

- YAZIO initial-import progress and connection feedback are now shown directly
  in the YAZIO account section.
- Mobile form controls use touch-friendly input sizing to avoid unintended
  browser focus zoom.

### Fixed

- Transient network, proxy, and server errors no longer incorrectly invalidate
  authenticated sessions.
- Short transport interruptions on idempotent reads are retried once while
  mutating requests are never automatically repeated.
- Session restore and CSRF refresh handling is more resilient to concurrent and
  stale requests.
- Target history deletion now preserves consistent validity ranges and user
  isolation.

## [0.3.4] - 2026-08-13

### Added

- German and English localization with persistent per-user language preferences.

### Changed

- Public authentication flows now use a consistent English interface; language
  selection is managed from the authenticated account settings.
- Weekly budget charts can highlight over-budget weeks and respect the
  configured week start.
- Mobile navigation and header branding were simplified.
- Initial YAZIO history synchronization now provides clearer background progress
  feedback.
- CI pins the Playwright package and browser image to the same version.

### Fixed

- Authentication, session, CSRF, and profile/locale race conditions can no
  longer let stale responses overwrite newer session state.
- API errors now use stable problem types for localized frontend messages.
- Localized setup E2E selectors correctly handle unit-bearing labels.

## [0.3.3] - 2026-08-12

### Changed

- YAZIO setup now requires an explicit historical start and end date.
- Historical YAZIO backfills are exclusively explicit range jobs; long ranges
  remain resumable and are split into consecutive requests of at most 366 days.
- The import interface automatically refreshes running historical jobs.

### Fixed

- Valid YAZIO days without entries are no longer treated as import failures.
- Migration `20260812_0014` neutralizes legacy full- and initial-history states
  from v0.3.2.
- Import-page polling stops correctly when the view is unmounted.

## [0.3.2] - 2026-08-12

### Added

- Added persistent, resumable YAZIO history synchronization with complete-history
  and user-selected date-range backfills. Long backfills are split into
  consecutive 366-day requests with persisted queue, cursor, completion, and
  retry state.
- Added historical-sync controls and progress/error status to **Importe**, plus
  `sync-yazio-history` CLI support for complete and date-range jobs.
- Added deployment-wide `YAZIO_SYNC_INTERVAL_HOURS=6` and `YAZIO_SYNC_DAYS=7`
  defaults. Connections may retain explicit per-connection overrides.
- Added migration `20260812_0013` for existing v0.3.1 YAZIO connections. They
  inherit the deployment defaults without an automatic full-history job; newly
  configured connections can receive their initial full-history import.

### Changed

- Improved the YAZIO scheduler heartbeat and health checks while historical
  jobs run, without blocking other eligible connections.

### Security

- Protected history queueing with CSRF validation, user-scoped operation locks,
  existing rate and global-concurrency limits, and resumable jobs that do not
  expose credentials.


## [0.3.1] - 2026-08-11

### Changed

- Updated the supported FastAPI, Alembic, pydantic-settings, Uvicorn, Vue,
  Vite, ESLint, typescript-eslint, vue-tsc, Playwright, test-tooling, and
  GitHub artifact-attestation dependencies.
- Moved the five test and E2E services from the production Compose file into
  the explicit `docker-compose.test.yml` overlay, with matching Make and script
  consumers.
- Simplified the README header navigation and documented the production/test
  Compose boundary.

### Fixed

- Prevented the pydantic-settings debug mode from emitting raw settings-source
  values, including secrets, through application logging.
- Aligned SQLAlchemy constraint metadata with the existing PostgreSQL schema,
  enabled `alembic check` in CI, and adopted Alembic's current path-separator
  configuration without creating a migration.

## [0.3.0] - 2026-08-11

### Added

- Added atomic administrator APIs for account deactivation, safe reactivation,
  and irreversible deletion of already inactive users, with cross-worker
  operation locks, last-administrator protection, and pseudonymous audit
  events.
- Added administrator-issued, single-use account recovery links with
  policy-checked password replacement, uniform public failures, per-client and
  per-token rate limits, and explicit administrator reactivation.
- Added freshly reauthenticated administrator operations for authenticator
  reset and irreversible account deletion, plus matching CLI recovery and
  authenticator-reset commands.
- Added a German administrator interface for multi-user invitations and account
  lifecycle operations, transient recovery-link handoff through URL fragments,
  and an unauthenticated password-recovery page that never signs in or
  reactivates the account.
- Added a dedicated required first-login setup screen for accounts without a
  nutrition target, using the existing user-scoped target history API.
- Added locale-independent German calendar-date entry with manual
  `TT.MM.JJJJ` input and an optional native calendar picker.

### Changed

- Deactivation now invalidates sessions, API tokens, open invitations, and
  pending WebAuthn challenges while preserving nutrition data and
  authentication factors; YAZIO synchronization remains paused after
  reactivation until explicitly reconfigured.
- User lifecycle serialization now also covers recovery issuance and
  completion, authenticator reset, and destructive reauthentication so those
  operations cannot race across API, CLI, imports, or the YAZIO scheduler.
- New account registration and CLI user creation no longer persist synthetic
  2200 kcal / 140 g nutrition targets.
- The central password policy now rejects obvious repetition and sequence
  patterns across registration, password changes, recovery, and CLI creation
  while retaining long passphrases and password-manager values.

### Fixed

- Allowed calorie budgets below, equal to, or above the optional maintenance
  estimate while retaining budget-first calendar classification.
- Standardized visible German calendar dates as `TT.MM.JJJJ` (or `TT.MM.` on
  compact axes) without changing ISO API values.

### Security

- Hardened Apple Health ZIP imports so malformed archive metadata and integrity
  failures are rejected before any samples are persisted.
- Added pseudonymous security events for YAZIO credential-decryption failures
  and expanded privileged account-operation coverage without logging secrets
  or user-owned nutrition values.
- Hardened encrypted database backups by authenticating and fully processing
  the PostgreSQL archive before publishing the final backup and checksum.
- Resolved known backend and frontend dependency advisories and extended CI
  coverage for account-lifecycle migrations and exact release candidates.
- Reserved login account attempts before password verification and capped
  concurrent Argon2 work across backend workers to prevent parallel
  rate-limit bypasses and authentication-driven resource exhaustion.

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
- Added a project-specific community code of conduct and prominent links to
  the conduct, contribution, license, and security policies.
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

[Unreleased]: https://github.com/tarantila/CaloGraph/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/tarantila/CaloGraph/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/tarantila/CaloGraph/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/tarantila/CaloGraph/compare/v0.3.8...v0.4.0
[0.3.8]: https://github.com/tarantila/CaloGraph/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/tarantila/CaloGraph/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/tarantila/CaloGraph/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/tarantila/CaloGraph/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/tarantila/CaloGraph/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/tarantila/CaloGraph/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/tarantila/CaloGraph/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tarantila/CaloGraph/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tarantila/CaloGraph/compare/661990b...v0.3.0
[0.2.2]: https://github.com/tarantila/CaloGraph/compare/55fc57d...661990b
[0.2.1]: https://github.com/tarantila/CaloGraph/compare/v0.2.0...55fc57d
[0.2.0]: https://github.com/tarantila/CaloGraph/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/tarantila/CaloGraph/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/tarantila/CaloGraph/compare/b4ca2cf...v0.1.1
