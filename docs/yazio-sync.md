# YAZIO import and direct synchronization

## Context

YAZIO currently provides no documented public export API for this use case.
CaloGraph therefore isolates the integration in its own adapter. Analytics and
the database do not depend on the concrete exporter; if its format or interface
changes, only the adapter should need adjustment.

The stable default path remains:

```text
YAZIO → Apple Health → Health Auto Export → CaloGraph
```

The experimental path is:

```text
YAZIO → yazio-exporter → CaloGraph YAZIO adapter
```

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
  --email name@example.com
```

The password is requested without echo and verified through a real YAZIO
request before it is stored. By default, the scheduler re-fetches the previous
seven days every six hours. A random delay of one to 30 minutes is added to
every scheduled follow-up so requests do not always occur at a fixed time.
Configure the maximum through `YAZIO_SCHEDULER_JITTER_MINUTES`; set it to `0`
to disable jitter.

This overlapping window updates later changes, skips unchanged values, and
adds new days. The 26 additional micronutrient endpoints are requested at most
once every 24 hours because they generate substantially more calls. Calories
and macronutrients remain on the normal six-hour schedule. Manually started
imports still begin immediately and include micronutrients.

Authenticated users can start the same personal sync with
**Jetzt synchronisieren** in the nutrition overview's data-status card. The
button uses only the authenticated account's YAZIO connection and is protected
by CSRF validation. Saving credentials and starting a manual sync share a
default limit of two attempts per ten minutes, independently enforced for the
authenticated CaloGraph user and the normalized client IP network.

The micronutrient analysis also offers an explicit 60-day history backfill.
This is intended for the first import or after micronutrient support is added
to an existing installation. It does not change the configured seven-day
window used by scheduled synchronization.

Inspect status or disable automation:

```bash
docker compose exec backend python -m app.cli yazio-status --username admin
docker compose exec backend python -m app.cli disable-yazio --username admin
```

Each user has at most one personal YAZIO connection. Imported samples, import
batches, and synchronization status remain strictly associated with that
CaloGraph account through `user_id`.

## Mapping

| YAZIO field | CaloGraph metric | Unit |
|---|---|---|
| Sum of `energy.energy` across all meals | `dietary_energy_kcal` | kcal |
| Sum of `nutrient.protein` | `protein_g` | g |
| Sum of `nutrient.carb` | `carbohydrates_g` | g |
| Sum of `nutrient.fat` | `fat_g` | g |
| `vitamin.a` through `vitamin.k` | 13 canonical vitamin metrics | mg or µg |
| `mineral.calcium` through `mineral.choline` | 13 canonical mineral metrics | mg or µg |

YAZIO's specific-nutrient endpoint returns these vitamin and mineral values in
grams. The adapter preserves that source unit and converts each value to the
canonical mg or µg unit before storing and analyzing it.

Activity, steps, and water are neither requested by direct sync nor imported by
the YAZIO adapter. Meals, products, recipes, profile fields, and YAZIO targets
are not currently persisted. Even when general raw-payload retention is
enabled, the YAZIO adapter does not store the complete export file. Re-fetching
the same day updates stable daily values idempotently.

Micronutrients come from the 26 separate daily endpoints provided by
`yazio-exporter==0.2.0`. Missing product details can therefore look like low
intake. The analysis reports data coverage separately and treats values as a
reliable orientation only at 70 percent coverage or above. EU NRVs from Annex
XIII of Regulation (EU) No 1169/2011 are a neutral adult reference, not a
medical diagnosis.

## Security and operational limits

- Direct retrieval uses YAZIO's undocumented API through the pinned
  `yazio-exporter==0.2.0` dependency.
- CaloGraph owns the network transport around the exporter. Every request uses
  an explicit connect/read timeout, redirects are rejected, and authentication
  is never retried automatically.
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
- A single direct request is limited to 366 days.
- The timeout, concurrency, rate-limit, and circuit-breaker defaults are
  configurable through the documented `YAZIO_*` values in `.env`. Raising
  these limits can increase both local resource use and the number of requests
  sent to the provider.
- Do not import the same days from Apple Health and YAZIO in parallel. Sources
  remain separate for provenance and are not deduplicated across source
  boundaries.
- If the interface stops working, the Apple Health path remains available
  unchanged.
