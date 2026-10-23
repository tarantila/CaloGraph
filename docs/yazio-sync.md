# YAZIO import and direct synchronization

## Context

YAZIO currently provides no documented public export API for this use case.
CaloGraph isolates direct access behind its own adapter and uses only the
pinned community `yazio-sdk==0.4.0` integration with YAZIO API v22. Enable it
with `YAZIO_ENABLED=true`; there is no provider selector or Nutrition Domain
write rollout switch.

Every direct manual or scheduled sync uses the user's configured, encrypted
YAZIO connection. It retrieves both:

- A compatibility payload with aggregate energy, carbohydrates, protein, fat,
  optional activity energy, and optional body weight for existing HealthSample
  consumers.
- A food diary with consumed items, simple products, product profiles, and
  daily summaries for canonical Nutrition Domain evidence and projections.

The aggregate endpoint does not provide a safe daily micronutrient or fiber
aggregate. Diary evidence can preserve supported nutrients actually supplied
by YAZIO, but does not establish complete daily micronutrient coverage.
`last_micronutrient_sync_at` remains unchanged, including after a successful
SDK sync. Missing provider values are not synthesized.

Compatible `days.json` and `nutrients.json` uploads remain independent of direct
access: neither a configured connection nor an installed SDK/exporter runtime
is required to parse or import those files.

### Operator configuration

Normal operator configuration consists of:

- `YAZIO_ENABLED`
- a personal connection configured through CaloGraph
- optional rolling-sync defaults `YAZIO_SYNC_INTERVAL_HOURS` and
  `YAZIO_SYNC_DAYS`

Timeouts, worker limits, rate limits, circuit-breaker thresholds, and scheduler
polling/jitter are advanced operational tuning:

`YAZIO_CONNECT_TIMEOUT_SECONDS`, `YAZIO_READ_TIMEOUT_SECONDS`,
`YAZIO_LOGIN_DEADLINE_SECONDS`, `YAZIO_OPERATION_DEADLINE_SECONDS`,
`YAZIO_REQUEST_WORKERS`, `YAZIO_RATE_LIMIT`,
`YAZIO_RATE_LIMIT_WINDOW_SECONDS`, `YAZIO_MAX_PARALLEL_OPERATIONS`,
`YAZIO_CIRCUIT_FAILURE_LIMIT`, `YAZIO_CIRCUIT_WINDOW_SECONDS`,
`YAZIO_SCHEDULER_POLL_SECONDS`, and `YAZIO_SCHEDULER_JITTER_MINUTES`.

`YAZIO_API_BASE_URL`, `YAZIO_SDK_USER_AGENT`, `YAZIO_SDK_CLIENT_ID`, and
`YAZIO_SDK_CLIENT_SECRET` are internal provider details. CaloGraph supplies
tested, versioned defaults for the shared YAZIO mobile client credential. The
API origin remains fixed to `https://yzapi.yazio.com`. Maintainers may override
these settings through the application environment when validating a compatible
provider revision, but they are not personal credentials and are not generated
per installation.

Normal operators do not configure either SDK client credential. Never expose
the effective values in logs, API responses, or the UI.

The normal user configures only a personal YAZIO email address and password
through CaloGraph. Those credentials remain in the existing per-user
connection flow and are stored encrypted; they are unrelated to the SDK
client configuration.

If YAZIO responds with `403 {"error":"version_blocked"}`, CaloGraph reports:
“Der von CaloGraph verwendete YAZIO-Client wird von YAZIO nicht mehr
akzeptiert. Prüfe, ob eine neuere CaloGraph-Version verfügbar ist.” Users
should update CaloGraph rather than editing User-Agent values.

Each new direct-sync `ImportBatch` records `connector_variant=sdk-v22` and
`client_identifier=yazio-sdk`. The compatibility samples retain
`source_type=yazio_export_v1`, `source_name=YAZIO`, the connection's stable
`source_identifier`, and stable daily `external_sample_id` values.
Canonical evidence is scoped to the CaloGraph user and YAZIO connection
(`source_instance_id`).

Historical batches and HealthSamples are not rewritten by the cutover:
existing `legacy-v15`, nullable connector variants, and
`client_identifier=yazio-exporter` remain accurate provenance. File imports
do not claim an SDK connector variant. Connector variants are operational
metadata, not a new normal-imports UI field or portable export field.

The staging procedure below is optional and requires separate, explicit
authorization for the staging target and test account. This document neither
executes nor authorizes live YAZIO requests.

### Controlled live verification sequence (later, staging only)

Do not run this sequence during development or CI. Run it only after explicit
authorization, with a disposable YAZIO test account and an isolated staging
CaloGraph instance. Record status codes and bounded response shapes, never
credentials, access tokens, or raw health payloads:

1. **TCP preflight:** verify only DNS resolution and a TCP connection to
   `yzapi.yazio.com:443`; do not send an HTTP request yet.
2. **Token exchange:** send one password-grant request to
   `/v22/oauth/token` with the configured client credentials and captured
   User-Agent. Keep the access token in process memory only.
3. **Single safe read:** use that token for exactly one
   `/v22/user/consumed-items/nutrients-daily` request over a minimal date range.
4. **Response validation:** confirm the expected status, bounded body size,
   ISO dates, optional fields, finite non-negative numeric values, and the
   `403 {"error":"version_blocked"}` distinction. Do not persist the response.
5. **One-day application sync:** use a configured staging connection for
   exactly one harmless day. Confirm compatibility import, canonical evidence,
   projection replay, and completion status in that order, comparing only
   normalized metrics.
6. **Small historical period:** request a bounded multi-day range and compare
   it to synthetic compatible-file expectations. Observe pending replay,
   cursor progress, rate limits, timeout, circuit, and import behavior.
7. **Historical sync last:** only after the preceding checks pass and within
   the authorized scope, run the initial historical synchronization for the
   test account. Use `YAZIO_ENABLED=false` as the operational kill switch if
   necessary; it preserves stored data and credentials. There is no retired
   direct-provider rollback path.

## Automatic synchronization

The dedicated `yazio-scheduler` polls due connections and performs provider
access outside the web backend. When several user accounts are due in one
cycle, the scheduler processes them sequentially with a fixed 90-second
interval between accounts. There is no wait before the first account or after
the last account. Existing per-user locks, provider capacity limits, retry
state, rate limits, circuit breaker, and scheduler heartbeat remain active.

### Daily trigger on app and login entry points

At the first app request through `/auth/me` or after a successful login on each
local calendar day, CaloGraph may queue the user's regular YAZIO
synchronization. The local date is calculated from the user's configured
timezone. A 30-minute freshness guard marks the daily trigger as handled
without queuing another run when a recent successful synchronization already
exists.

The web request only updates PostgreSQL scheduling state. It never contacts
YAZIO or invokes the SDK, transport, credential validation, or payload fetch.
The existing `yazio-scheduler` notices the due `next_sync_at` and performs the
provider operation through its normal VPN-routed path. No additional operator
or environment configuration is required.

## Available methods

### Upload `days.json` or `nutrients.json`

1. Obtain a compatible `days.json` or `nutrients.json` file. The external
   [`yazio-exporter`](https://github.com/aleksandr-bogdanov/yazio-exporter)
   is one optional file producer, not a CaloGraph runtime requirement.
2. Open **Importe** in CaloGraph.
3. Select and import the files one after another.

When a YAZIO connection is configured, JSON and file uploads use that
connection's stable source identifier as well. Therefore imported `weight_kg`
samples remain visible when YAZIO is selected as the weight provider; an
explicit `X-Client-Identifier` remains a separate source instance.

The adapter supports the exporter's original date object, a wrapper shaped as
`{ "days": { ... } }`, and the original `nutrients.json`. It also accepts
simple daily objects containing `energy`, `protein`, `carb`, and `fat`.

### Synchronize a configured connection from the CLI

First configure the personal connection as described below. `sync-yazio`
requires that enabled connection and uses its encrypted credentials, stable
source identifier, user locks, and the same canonical sync flow as the UI and
scheduler. It does not accept ad-hoc email/password or source-identifier
overrides and does not prompt for credentials.

```bash
docker compose exec backend python -m app.cli sync-yazio --username admin
```

Without date options, the CLI retrieves the previous 60 days including today.
Use `--days` for a different trailing window (1–366 days), or an explicit
inclusive range of at most 366 days:

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin --days 7
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --from-date 2026-01-01 \
  --end-date 2026-03-31
```

For longer ranges, queue `sync-yazio-history` instead. The stable default
source identifier is `yazio:<CaloGraph user UUID>`; neither the YAZIO email
address nor an unkeyed digest of it is stored in health samples.

### Configure scheduled synchronization

For unattended operation, every YAZIO connection belongs to one CaloGraph
user. Email and password are stored only in encrypted form in PostgreSQL. The
separate key remains in the host-side `secrets/credential_encryption_key` file;
Compose mounts it only into the backend and scheduler.

Generate the key once:

```bash
docker compose run --rm --no-deps backend \
  python -m app.cli generate-credential-key
```

For a new installation, `scripts/init-secrets.sh` already creates a valid key.
If a key is generated manually, write the resulting value to the file named by
`CREDENTIAL_ENCRYPTION_KEY_FILE` in `.env`, keep its parent directory at mode
`0700`, apply read-only mode `0444` to the file for the non-root container UID,
and then recreate the backend and scheduler:

```bash
docker compose up -d --no-build --force-recreate backend yazio-scheduler
```

Configure a personal connection:

```bash
docker compose exec backend python -m app.cli configure-yazio \
  --username admin \
  --email name@example.com \
  --from-date 2026-01-01 \
  --end-date 2026-03-31
```

The password is requested without echo and verified through a real YAZIO
request before it is stored. A new connection requires an explicit initial
date range, starting no earlier than 2000-01-01. The scheduler processes this
range in chunks of at most 366 calendar days; only after it completes does the
regular rolling sync begin. A failed or interrupted chunk remains retryable
from its persisted cursor without creating duplicate samples.

`YAZIO_SYNC_INTERVAL_HOURS` (default `6`) and `YAZIO_SYNC_DAYS` (default `7`)
are deployment-wide defaults. Connections with no stored override inherit them,
so later environment changes take effect for those connections without a
database rewrite. `configure-yazio --interval-hours … --days …` stores an
explicit per-connection override when that is required. A random delay of one
to 30 minutes is added to normal scheduled follow-ups; configure the maximum
through `YAZIO_SCHEDULER_JITTER_MINUTES`, or set it to `0` to disable jitter.

Authenticated users can start the same personal rolling sync with **Jetzt
synchronisieren** in the nutrition overview's data-status card. The button
uses only the authenticated account's YAZIO connection and is protected by
CSRF validation. Saving credentials and starting a manual sync share a default
limit of two attempts per ten minutes, independently enforced for the
authenticated CaloGraph user and the normalized client IP network.

Historical operations are in **Importe**. There, the user can queue an explicit
date range to import initial data or re-fetch corrected periods. The request
returns immediately; status, failures, and completion are persisted and shown
in the same area. Existing connections upgraded from v0.3.2 are not
automatically backfilled.

Operators can inspect status, disable automation, or queue the same background
range job without the browser:

```bash
docker compose exec backend python -m app.cli yazio-status --username admin
docker compose exec backend python -m app.cli sync-yazio-history --username admin \
  --from-date 2026-01-01 --end-date 2026-03-31
docker compose exec backend python -m app.cli disable-yazio --username admin
```

Each user has at most one personal YAZIO connection. Imported samples, import
batches, and synchronization status remain strictly associated with that
CaloGraph account through `user_id`.

## Persistence, replay, and source priority

Direct UI, CLI, and scheduled synchronization all use this ordering:

1. Under the existing per-user operation locks, replay pending canonical runs
   for that user and connection, oldest first, before fetching new data.
2. Retrieve the SDK compatibility payload and food diary in the bounded worker.
   A fetch failure does not persist a new compatibility import or diary run.
3. Parse and persist compatibility HealthSamples and the import batch, then
   ingest canonical diary evidence in the **same database transaction**.
   Persist the ingestion run with `projection_pending=true` and commit.
   A failure before this commit rolls back both paths.
4. Bootstrap source priority without replacing existing policies, then rebuild
   replay dates from committed evidence, requested-range observations, and
   affected event revisions. Each day uses an independent clean session.
5. Only after replay returns successfully, atomically clear the run's pending
   marker and update connection success/scheduling state and, for historical
   work, advance the cursor or mark the job completed.

Projection or finalization failure does not erase already committed evidence
or compatibility samples. Earlier successfully rebuilt days can remain visible
when a later day fails. The run remains pending until acknowledged; the next
attempt replays durable evidence idempotently before fetching another payload.
A failed pending replay stops that drain so newer runs cannot overtake it.
Recovering an older run alone does not mark a current sync successful or
advance a historical cursor: the requested range must still complete and
finalize. Historical chunks run from the newest end backwards, at most 366
days per chunk; a failed chunk keeps its cursor for retry.

For non-authentication sync failures, the next attempt is scheduled after the
smaller of the connection interval and 60 minutes, plus configured jitter.
Authentication failures disable the connection's schedule until credentials
are verified and saved again. Individual SDK requests are not automatically
retried. Local ingestion, projection, or acknowledgement errors are not
classified as provider failures and do not open the shared provider circuit.

Source priority is a **downstream projection decision**, not a switch deciding
whether SDK evidence is written. With no policy and just one available source,
bootstrap can establish that source; with several sources, explicit selection
is required. Existing user policies are preserved. Replay selects the policy
effective at an explicit, timezone-aware replay timestamp (`policy_at`), not at
the diary date or the original fetch time. Pending drains share one current
timestamp; a later retry can therefore use a newly effective policy. A
future-only policy is not applied early.

A missing effective policy yields `policy_missing` for affected days rather
than inventing a source selection. This is not a replay exception and can
still allow sync finalization; successful fetching does not imply that every
day has a selected daily projection. Canonical evidence stays available for
later policy-aware rebuilds.

Existing historical HealthSamples are not converted to canonical diary
evidence automatically. To populate canonical data for an older period, queue
an explicit historical SDK sync; its cursor and pending-replay rules are the
same as for new data.

## Mapping and coverage

### Compatibility HealthSamples and compatible files

| Input | CaloGraph metric | Unit |
|---|---|---|
| SDK daily aggregate `energy` or compatible meal `energy.energy` totals | `dietary_energy_kcal` | kcal |
| SDK/compatible daily `protein`, `carb`, `fat` | `protein_g`, `carbohydrates_g`, `fat_g` | g |
| Daily `activity_energy`, when supplied | `active_energy_kcal` | kcal |
| SDK `/v22/user/bodyvalues/weight/last` or compatible weight entries | `weight_kg` | kg |
| Compatible `nutrients.json` vitamin and mineral entries | 26 canonical vitamin/mineral metrics | mg or µg |

The direct aggregate payload is only the macro/optional activity/weight
compatibility path; it is not the diary Nutrition Domain. Steps and water are
not imported through this path. Activity energy is credited only after the
user selects YAZIO for the applicable target version. Re-fetching stable
daily IDs updates values idempotently; a day omitted by YAZIO is not a
deletion. SDK weight is normalized to kg with
`<local-date>:weight_kg` as its external ID and uses CaloGraph's bounded
authenticated HTTP transport.

Compatible `nutrients.json` retains the historical specific-nutrient format:
vitamin/mineral values supplied in grams are converted to the canonical mg or
µg unit. This file-format support is not a direct-runtime call to the retired
exporter's separate nutrient endpoints. Files create compatibility
HealthSamples, not consumed-event/product diary evidence. The compatibility
adapter does not persist meals, products, recipes, profile fields, YAZIO
targets, or the complete raw file even when general raw-payload retention is
enabled. Historical HealthSamples retain these semantics.

### Canonical diary evidence

The SDK diary path preserves consumed products, simple products, linked
product profiles, and daily summaries as bounded canonical observations and
consumption evidence. Numeric additional nutrient fields whose YAZIO IDs are
known to the canonical catalog are preserved and mapped. Product-profile
nutrients contribute to event evidence only when linked to a real consumed
product and scaled by its provider-supplied amount. Unlinked profiles do not
contribute daily intake. Simple-product and daily-summary fields remain
direct provider observations; missing values remain missing.

Daily Nutrition Domain projections currently cover energy, protein,
carbohydrates, fat, fiber, sugar, and saturated fat.
Micronutrients are not added to `DAILY_PROJECTION_METRICS`; supported diary
micronutrient evidence is not a promise of complete daily micronutrient
coverage and does not advance `last_micronutrient_sync_at`.

Missing product details can look like low intake. Analysis reports coverage
separately and treats values as a reliable orientation only at 70 percent
coverage or above. EU NRVs from Annex XIII of Regulation (EU) No 1169/2011 are
a neutral adult reference, not a medical diagnosis.

## Security and operational limits

- Direct retrieval uses only the pinned community `yazio-sdk==0.4.0`
  integration. External compatible-file producers are not runtime providers.
- CaloGraph owns the bounded network transport. Every request uses explicit
  connect/read timeouts, redirects are rejected, and authentication is never
  retried automatically.
- The SDK provider uses `httpx.Timeout`, rejects redirects, and uses a bounded
  request worker pool without unbounded parallelism. It calls generated
  `sync_detailed` operations so status and `Retry-After` can be classified
  without exposing response bodies.
- Worker serialization distinguishes authentication, `version_blocked`,
  bounded `rate_limited`, unavailable, network timeout, absolute deadline, and
  invalid response failures. No SDK request is retried automatically.
- Each login runs in an isolated child process with a 25-second absolute
  deadline. A complete data retrieval has a five-minute absolute deadline. The
  parent process terminates the child when that deadline is exceeded.
- Credentials are passed to the isolated process through standard input, never
  through command-line arguments or environment variables. Provider responses
  and transport errors are reduced to bounded, non-sensitive messages.
- PostgreSQL advisory locks permit only one active YAZIO operation per
  CaloGraph user and two active operations across the deployment by default.
  This coordination works across backend workers and the scheduler without
  Redis.
- Five provider or deadline failures in a ten-minute window temporarily open a
  shared circuit breaker. Authentication failures do not open the circuit. An
  authentication failure pauses that user's scheduled sync until the
  credentials are saved and verified again.
- Deactivating a CaloGraph account pauses its scheduled YAZIO connection and
  blocks manual and scheduled writes. Reactivation preserves the encrypted
  credentials but does not resume the schedule; the user must verify and save
  the connection again.
- Set `YAZIO_ENABLED=false` to disable credential setup, manual retrieval, and
  scheduled synchronization immediately. Existing encrypted credentials
  remain stored so the feature can be re-enabled later.
- Direct UI, CLI, and scheduler retrieval all use the configured connection's
  credentials, stored with authenticated Fernet encryption. The key is kept in
  a separate host file and delivered through a service-scoped secret mount.
  Access tokens are not persisted.
- Stored source identifiers use only CaloGraph's random internal user UUID.
  Neither the YAZIO email address nor an unkeyed digest of it is stored or
  written to logs.
- If `CREDENTIAL_ENCRYPTION_KEY` is lost, stored connections cannot be
  recovered and must be configured again.
- Anyone who can read both the database and credential key file can decrypt
  credentials. File permissions and encrypted backups must protect both.
- A single provider request is limited to 366 days. Longer explicit historical
  ranges are split into sequential requests of that size.
- The timeout, concurrency, rate-limit, circuit-breaker, and regular-sync
  defaults are configurable through the documented `YAZIO_*` values in
  `.env`. Raising these limits can increase both local resource use and the
  number of requests sent to the provider.
- Do not import the same days from Apple Health and YAZIO in parallel. Sources
  remain separate for provenance and are not deduplicated across source
  boundaries.
- If the interface stops working, the Apple Health path remains available
  unchanged.
