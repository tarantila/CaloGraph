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
neither the password nor the access token for this manual operation. To
separate multiple accounts, only a shortened SHA-256 identifier derived from
the normalized email address is used; the address itself is not stored in
health samples.

### Configure scheduled synchronization

For unattended operation, every YAZIO connection belongs to one CaloGraph
user. Email and password are stored only in encrypted form in PostgreSQL; the
separate key remains in `.env`.

Generate the key once:

```bash
docker compose run --rm --no-deps backend \
  python -m app.cli generate-credential-key
```

Set the resulting value as `CREDENTIAL_ENCRYPTION_KEY` in `.env`, then recreate
the backend and scheduler:

```bash
docker compose up -d --build backend yazio-scheduler
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
button uses only the authenticated account's YAZIO connection, is protected by
CSRF validation, and is limited to two starts per minute.

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
- Manual retrieval sends credentials only to the YAZIO endpoint and does not
  persist them. Scheduled sync stores them using authenticated Fernet
  encryption, with the key kept separately in `.env`.
- If `CREDENTIAL_ENCRYPTION_KEY` is lost, stored connections cannot be
  recovered and must be configured again.
- Anyone who can read both the database and `.env` can decrypt credentials.
  File permissions and backups must protect both.
- A single direct request is limited to 366 days.
- Do not import the same days from Apple Health and YAZIO in parallel. Sources
  remain separate for provenance and are not deduplicated across source
  boundaries.
- If the interface stops working, the Apple Health path remains available
  unchanged.
