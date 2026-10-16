# Architecture

## Components

`frontend` contains the statically built Vue 3 application and Nginx. Nginx
serves the SPA and proxies `/api/` internally to `backend`. `backend` is a
FastAPI monolith with modular HTTP, authentication, import, service, and
analytics layers. `yazio-scheduler` uses the same backend image and starts due
YAZIO imports without exposing another HTTP port. `postgres` is the only
persistent runtime dependency and is not bound to the host.

## Data flow

```text
iPhone exporter ── HTTPS/JSON ─────┐
Apple Health XML/ZIP ─ browser ─────┼─ FastAPI ─ adapter ─ normalization ─ PostgreSQL
YAZIO JSON / manual retrieval ──────┤
YAZIO ─ scheduler ─ user account ───┤
Browser ─ Vue/Nginx ────────────────┘                                   ↓
Browser ← Vue/Nginx ← user-scoped API responses ← analytics service ─────┘
```

Adapters produce canonical samples. Only services write data, and analytics
reads only the internal model. Every import batch, sample, and YAZIO connection
has a `user_id`; database queries are always restricted to the authenticated
user. A future native iOS app will use the existing `calograph_sync_v1` format.

Activity energy is normalized as the canonical `active_energy_kcal` metric.
Analytics applies it only when the target version valid on that day enables
activity credit and selects the sample's source type; sources are never summed
automatically. This preserves user isolation and makes historical budget
results reproducible after a later settings change.

The Analysis Hub preference API stores one ordered `users.active_card_ids`
list per account. Built-in cards and available standalone metric IDs share
that sequence; legacy `analysis_keys` and `summary_order` responses are
projections, and legacy writes are translated into the canonical sequence
under the user-row lock and transaction. Registry validation rejects
unavailable or non-card-capable metrics before persistence.

## Operational decisions

`ENVIRONMENT` is explicit. Before production migrations or the scheduler loop,
CaloGraph validates the HTTPS origin, cookie and HSTS policy, independent
secrets, database credentials, request allowlists, YAZIO encryption, and
upload-capacity relationships. Unsafe production configuration aborts startup
with variable names but never secret values. Development keeps localhost HTTP
available.

Compose separates browser-facing proxy traffic, internal data traffic, and
scheduler egress. PostgreSQL is attached only to the externally isolated data
network; the frontend never shares a network with it. Application containers
drop all Linux capabilities, while PostgreSQL receives only the small set its
official entrypoint needs to initialize ownership and switch users. Optional
per-service memory, CPU, and PID ceilings can be enabled through the deployment
environment after measuring representative imports.

Migrations abort container startup on failure. The backend uses multiple
workers, so rate limits use atomic
PostgreSQL upserts. Login protection has independent buckets for all attempts
from an IP address (IPv6 is grouped by `/64`) and failed attempts for a
pseudonymized account identifier. Expired buckets are removed by the scheduler.
Redis and a general-purpose background queue are deliberately omitted. The
single YAZIO scheduler checks for due connections at a configurable interval.
The same hourly maintenance cycle removes revoked sessions and sessions beyond
their server-side idle or absolute lifetime. Internet-facing sessions use a
host-only secure cookie; development retains a separate non-prefixed localhost
cookie so HTTP development remains usable.
Credentials are encrypted per user with Fernet. The host stores the key as an
operator-controlled file outside the image; Compose mounts it only into the
backend and scheduler. Database, session, and rate-limit secrets use the same
file-based mechanism with narrower service grants. The scheduler receives the
rate-limit secret because its provider failures must address the same
deployment-wide circuit-breaker buckets as HTTP workers; it never receives the
session secret. `.env` contains paths, not secret values. The files must be
included in the encrypted backup and permissions strategy together with the
database. YAZIO network work runs in a short-lived isolated child process with
explicit request timeouts and a parent-enforced absolute deadline.

TOTP uses a separate Fernet key mounted only into the backend. Password login
for an enrolled user yields a signed five-minute challenge instead of a
session; a session is issued after the second factor succeeds. The database
stores encrypted TOTP seeds, the last accepted time step for replay
protection, and HMAC-only one-time recovery codes. MFA-management actions
require the current password plus a factor and preserve only the current
session.

Passkeys use WebAuthn discoverable credentials for passwordless sign-in.
CaloGraph stores only the credential public key, user handle, metadata, and
signature counter. Five-minute registration and authentication challenges are
persisted in PostgreSQL and claimed under a row lock, making each challenge
single-use across backend workers. Registration is additionally bound to the
current user session. Verification requires a matching
`CALOGRAPH_PUBLIC_URL` relying-party host and origin plus authenticator user
verification.
PostgreSQL advisory locks coordinate user operations across backend workers
and the scheduler. Ordinary user mutations hold a shared per-user lock;
deactivation, reactivation, and hard deletion hold the exclusive form plus a
deployment-wide administrator-invariant lock. The lifecycle service re-reads
the account only after acquiring those locks and commits status changes,
credential/session invalidation, YAZIO pausing, and deletion atomically.
Separate YAZIO advisory locks still enforce one YAZIO operation per user and a
small deployment-wide provider concurrency budget. PostgreSQL also stores
temporary rate-limit and circuit-breaker buckets, so these protections need no
additional service.

Historical uploads are streamed during the request and expose upload progress
in the browser. Nginx forwards the Apple Health request without buffering it a
second time; its multipart ceiling and Starlette's ephemeral `/tmp` spool are
driven by validated byte settings. The XML adapter yields one record at a time,
services perform bulk lookups and commit accepted samples in 500-record batches,
and a PostgreSQL advisory lock permits only one import per user across backend
workers. Non-ZIP failures after a checkpoint are retained as `partial_failed`
so a retry can continue idempotently; ZIP integrity, XML, and limit failures
roll back their import completely.

## User-selected provider priority

Provider priority is a user-scoped, versioned policy in
`source_priority_policies` and `source_priority_rules`. The authenticated
settings API exposes the ordered preference and a separate availability view;
the historical `user_provider_preferences` table is only a migration input.
The analytics endpoint uses the stored priority when the `source` query
parameter is omitted. An explicit `source` remains a Legacy compatibility
path.

Nutrition and weight resolve providers in priority order independently for each
local day and use the first provider with complete evidence for that day.
Activity uses the target-version snapshot chain for historical days and the
current priority chain for new target versions. Providers are never summed or
silently replaced by an unconfigured Legacy aggregate. If no configured
provider can safely resolve a requested day, the endpoint returns a safe
not-ready or unavailable problem response. Missing preferences retain the
temporary Legacy YAZIO behavior during migration.

## Analysis catalogue and cards

`GET /api/v1/analytics/registry` publishes backend-owned analysis keys and
reader capabilities without UI presentation data. The frontend keeps labels,
icons, and routes in its presentation map; a backend test checks that every
registry key has frontend metadata.

The authenticated `GET /api/v1/settings/analysis-preferences` returns the
canonical mixed ordered `active_card_ids` list. New accounts receive the four
domain cards `weight`, `nutrition`, `micronutrients`, and `activity`;
`meals_today` is an optional built-in card, and standalone metric IDs must be
available and card-capable in the analysis registry. The hub presents this
single list in the saved order; all entries are removable.

`PUT` accepts `active_card_ids` as the source of truth and rejects duplicate,
legacy-alias, unknown, and unavailable metric IDs. It also returns
`analysis_keys` and `summary_order` as compatibility projections for older
clients; legacy writes are adapted into the canonical list. Revision
`20261006_0039` adds `users.active_card_ids` and deterministically backfills
the previous summary order followed by selected metric rows, mapping the
weight and active-energy aliases to their built-in cards and deduplicating
them. Invalid persisted preferences abort the migration rather than discard
data.

Analysis cards and details use the existing canonical weight and activity
readers (up to 3661 days), the seven canonical nutrition analyses via
`analytics.nutrition.analysis` (up to 365 days), and the existing micronutrient
reader `analytics.nutrition.micronutrients` (up to 365 days). Internal
micronutrient provider reads remain at most 31 days; all required chunks finish
before one complete aggregate is returned. A required-chunk failure or unsafe
conflict fails the request with the existing HTTP 503 provider problem, never a
partial aggregate. These registry capabilities do not depend on whether a user
has records. Weight summaries derive only from the endpoint's sparse daily
points. Missing days remain missing, and source-comparison views remain
separate from Analysis.
The shared frontend `PeriodPicker` is controlled by its route view. Views own
the reader, data loading, URL query state, and reader changes; the component
owns only the uncommitted date draft and emits one `apply` event. Quick presets
apply immediately. The desktop bar remains `7 days | 30 days | 90 days |
Individuell`; unsupported fixed slots are disabled in place, while the mobile
control lists only supported choices. `30 days` is the default, `6 months`
means 180 inclusive rolling days, and `1 year` means 365.
Weight detail alone adds an `Alle` preset mapped to the bounded rolling
`period=3661` range. It remains distinct from capability-gated `period=all`.

Registry `min_days`, `max_days`, and `custom_range` are static request limits,
not an all-data capability. A successful data-reader response supplies the
dynamic capability for that reader request. The Analysis hub intersects the
capabilities of its four fixed Summary readers and selected available Metric
readers, not selected readers alone. `All data` is selectable only when the
response explicitly supports it and supplies a valid matching range within the
existing reader limit. An unresolved or failed response does not enable it;
current readers without a complete reader-owned start boundary report
`supports_all_data: false`. Existing API range and Legacy `period=all`
semantics remain unchanged.

Analysis period state remains URL-owned, with no period store or persisted
preference. Rolling presets use only `period`; custom ranges use absolute
`period=custom&start=...&end=...` bounds. Unrelated query parameters and hashes
survive. Picker changes replace the current navigation entry; Summary, Metric,
and breadcrumb links navigate normally while retaining the selected range.
Back/Forward restores the range attached to each route entry.

The real authenticated browser acceptance is:

```bash
npm run test:e2e -- tests/e2e/product-wide-period-picker.spec.ts --project=chromium --workers=1
```

Run it only with a confirmed isolated local Development/Test runtime and the
dedicated synthetic demo sign-in privately supplied to the test process:
`CALOGRAPH_ANALYSIS_DEMO_SAFE_RUNTIME=1`, a local `BASE_URL`, and
`CALOGRAPH_ANALYSIS_DEMO_PASSWORD`. The test fails closed if these are absent.
Do not fall back to a default target or API mocks, and never print or persist
the demo credential.



### Offline analysis demo

The demo seed is restricted to development/test environments and creates the
dedicated non-admin `calograph-analysis-demo` account. Its offline
`analysis_demo` source/provider supplies synthetic records through the normal
canonical readers and user-scoped policies; it does not contact real providers
or change their contracts or the Legacy analytics paths.

Only the exact reserved demo user also opts into `analysis_demo` for Overview
Summary and Trends in `development`/`test`; the resolver's environment and
user-ID gates remain authoritative. These reads use canonical Nutrition data
and do not create Legacy `HealthSample` nutrition rows.

Removal first audits the account's rows and references for exclusive seed
ownership and refuses unproven changes. It then removes the seed-owned data
and account. Security audit events are preserved; their account references can
be cleared through `SET NULL`.
