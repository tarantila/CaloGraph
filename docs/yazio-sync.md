# YAZIO import and direct synchronization

## Context

YAZIO currently provides no documented public export API for this use case.
CaloGraph therefore isolates the integration in its own adapter. Analytics and
the database do not depend on the concrete exporter; if its format or interface
changes, only the adapter should need adjustment.

The stable direct-sync provider is `sdk-v22`, backed by the pinned
`yazio-sdk==0.4.0` and the YAZIO API v22. It maps the v22 aggregate endpoint
to energy, carbohydrates, protein, and fat and reads optional activity energy
from the daily-summary widget. It does not provide a safe daily micronutrient
or fiber aggregate, so SDK results remain explicitly incomplete and never
advance `last_micronutrient_sync_at`.

`legacy-v15`, backed by `yazio-exporter==0.2.0`, remains available as a
deprecated compatibility provider while its micronutrient and fiber coverage
is still needed. It will be removed no later than CaloGraph 1.0.

When direct YAZIO synchronization is enabled, `YAZIO_PROVIDER` must be set
explicitly:

```dotenv
YAZIO_ENABLED=true
YAZIO_PROVIDER=sdk
```

Use `YAZIO_PROVIDER=legacy` only as a temporary compatibility rollback. The
application rejects an enabled installation with a missing or empty provider,
and rejects every value other than `sdk` or `legacy`. Disabled installations
may omit the provider.

### Operator configuration

Normal operator configuration consists of:

- `YAZIO_ENABLED`
- `YAZIO_PROVIDER`
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
tested, versioned defaults; normal installations do not need to set them.
The API origin remains fixed to `https://yzapi.yazio.com`. Maintainers may
override these settings through the application environment when validating a
compatible provider revision, but they are not personal credentials and are
not generated per installation.

The SDK defaults use the publicly documented YAZIO mobile-app OAuth client.
All SDK-mode CaloGraph installations therefore share that client pair. If
YAZIO disables or changes it, multiple installations can be affected at once.
Generating random local values would not create a registered YAZIO client and
would not solve this upstream dependency. CaloGraph does not add an instance
ID or telemetry to YAZIO requests.

The normal user configures only a personal YAZIO email address and password
through CaloGraph. Those credentials remain in the existing per-user
connection flow and are stored encrypted; they are unrelated to the SDK
client defaults.

If YAZIO responds with `403 {"error":"version_blocked"}`, CaloGraph reports:
“Der von CaloGraph verwendete YAZIO-Client wird von YAZIO nicht mehr
akzeptiert. Prüfe, ob eine neuere CaloGraph-Version verfügbar ist.” Users
should update CaloGraph rather than editing User-Agent values.

For internal provenance, each newly created direct-sync `ImportBatch` records
`connector_variant` as `sdk-v22` or `legacy-v15`. The fachliche
`source_type=yazio_export_v1`, `source_name=YAZIO`, stable
`source_identifier`, `external_sample_id`, and `client_identifier=yazio-exporter`
remain unchanged. Existing batches keep their nullable legacy-compatible value.
The connector variant is operational metadata and is intentionally not added to
the normal imports UI or the portable export schema.

Before enabling SDK mode in a deployment, perform the controlled staging
sequence below without putting credentials in logs. This document does not
execute or authorize live YAZIO requests.

### Controlled live verification sequence (later, staging only)

This sequence is intentionally not run as part of development or CI. Use only
with a disposable YAZIO test account and a staging CaloGraph instance. Record
status codes and bounded response shapes, never credentials, access tokens, or
raw health payloads:

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
5. **One-day read:** repeat the provider through the staging CaloGraph worker
   for exactly one harmless day and compare only normalized metrics.
6. **Small historical period:** request a bounded multi-day range, compare it to
   an exporter fixture, and watch rate-limit, timeout, circuit, and import
   behavior.
7. **Historical sync last:** only after the preceding checks pass, run the
   initial historical synchronization for one test account. Keep
   `YAZIO_PROVIDER=legacy` as the immediate rollback.

## Upgrade to CaloGraph v0.6.4

Existing installations with enabled direct YAZIO synchronization must add an
explicit provider selection before upgrading:

```dotenv
YAZIO_PROVIDER=sdk
```

For a temporary rollback or compatibility window, use:

```dotenv
YAZIO_PROVIDER=legacy
```

`legacy` is deprecated and will be removed no later than CaloGraph 1.0.
Normal operators do not need to configure API URLs, User-Agent strings, SDK
client values, or timeout, worker, and circuit-breaker settings.

## Available methods

### Upload `days.json` or `nutrients.json`

1. Use
   [`yazio-exporter`](https://github.com/aleksandr-bogdanov/yazio-exporter) to
   create `days.json` and, optionally, `nutrients.json`.
2. Open **Importe** in CaloGraph.
3. Select and import the files one after another.

The adapter supports the exporter's original date object, a wrapper shaped as
`{ "days": { ... } }`, and the original `nutrients.json`. It also accepts
simple daily objects containing `energy`, `protein`, `carb`, and `fat`.

### Retrieve data directly from the backend

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --email name@example.com
```

Without dates, the command retrieves the previous 60 days including today. Use
an explicit range when required:

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --email name@example.com \
  --from-date 2026-01-01 \
  --end-date 2026-03-31
```

The YAZIO password is requested interactively without echo. CaloGraph stores
neither the password nor the access token for this manual operation. Unless
`--source-identifier` is supplied, manual retrieval uses
`yazio:<CaloGraph user UUID>`, the same stable source identifier as scheduled
synchronization. It is based only on CaloGraph's random internal user UUID;
neither the YAZIO email address nor an unkeyed digest of it is stored in health
samples. An explicit `--source-identifier` can separate the provenance of
multiple YAZIO accounts under one CaloGraph user; because it is persisted with
the samples, it must be stable and non-sensitive.

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

## Mapping

| YAZIO field | CaloGraph metric | Unit |
|---|---|---|
| Sum of `energy.energy` across all meals | `dietary_energy_kcal` | kcal |
| Daily `activity_energy`, when supplied | `active_energy_kcal` | kcal |
| Sum of `nutrient.protein` | `protein_g` | g |
| Sum of `nutrient.fat` | `fat_g` | g |
| `vitamin.a` through `vitamin.k` | 13 canonical vitamin metrics | mg or µg |
| `mineral.calcium` through `mineral.choline` | 13 canonical mineral metrics | mg or µg |

In SDK mode, `energy`, `protein`, `carb`, and `fat` are mapped directly from
the v22 `nutrients-daily` response. `activity_energy` is read only from the
daily-summary widget; meals and product/recipe maps are intentionally ignored
because they cannot safely reconstruct daily micronutrients. Every requested
date is emitted, including dates missing from the provider response, and
provider values must be finite, non-negative numbers within the requested
range.

YAZIO's specific-nutrient endpoint returns these vitamin and mineral values in
grams. The adapter preserves that source unit and converts each value to the
canonical mg or µg unit before storing and analyzing it.

The YAZIO adapter imports daily `activity_energy` as `active_energy_kcal` when
the exporter supplies it. It is credited only after the user selects YAZIO for
the applicable target version. Steps and water are not imported. Meals,
products, recipes, profile fields, and YAZIO targets are not currently
persisted. Even when general raw-payload retention is enabled, the YAZIO adapter
does not store the complete export file. Re-fetching the same day updates stable
daily values idempotently.

Micronutrients come from the 26 separate daily endpoints provided by
`yazio-exporter==0.2.0`. Missing product details can therefore look like low
intake. The analysis reports data coverage separately and treats values as a
reliable orientation only at 70 percent coverage or above. EU NRVs from Annex
XIII of Regulation (EU) No 1169/2011 are a neutral adult reference, not a
medical diagnosis.

## Security and operational limits

- Direct retrieval uses the explicitly selected provider. Legacy uses the
  pinned `yazio-exporter==0.2.0` dependency; SDK uses the pinned community
  `yazio-sdk==0.4.0` provider.
- CaloGraph owns the network transport around both providers. Every request
  uses explicit connect/read timeouts, redirects are rejected, and
  authentication is never retried automatically.
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
- Manual retrieval sends credentials only to the YAZIO endpoint and does not
  persist them. Scheduled sync stores them using authenticated Fernet
  encryption, with the key kept in a separate host file and delivered through
  a service-scoped secret mount.
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
