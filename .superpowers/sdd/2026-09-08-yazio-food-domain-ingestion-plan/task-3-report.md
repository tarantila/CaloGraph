# Task 3 report: YAZIO nutrition domain ingestion

## Changed files

- `backend/app/services/yazio_nutrition_ingestion.py`
  - Added the caller-owned-flush `ingest_yazio_food_diary` domain boundary.
  - Persists YAZIO v22 runs, stable source observations, product/simple-product/daily-summary identities, profiles, immutable snapshots, EAN links, servings, field observations, events, revisions, and provenance.
  - Performs no provider/network calls and never commits.
- `backend/app/nutrition/repositories.py`
  - Added the minimal optional current-snapshot advancement gate used to retain stale provider snapshots without rewinding profile state.
- `backend/tests/test_yazio_nutrition_ingestion.py`
  - Added focused domain acceptance coverage for Decimal base-unit formulas, direct simple-product values, explicit zero/missing state, unknown nutrients, metadata/flags/EANs/servings, summary separation, civil time, roles/lineage/provenance, idempotence/revisions, isolation, and rollback.

## TDD evidence

Tests were written before the service implementation. The first focused command was:

```text
PYTHONPATH=/tmp/calo-deps:. python3 -m pytest tests/test_yazio_nutrition_ingestion.py -q
```

Result: RED at collection in the available Python 3.12 runtime before the new test module executed: existing `app.config.Settings` forward annotation raised `NameError: name 'Settings' is not defined` (exit code 4). The repository requires Python 3.14; this is the same runtime limitation recorded by Task 2.

After implementation, using the prepared test-only runtime shim (production files and assertions unchanged):

```text
cp tests/test_yazio_nutrition_ingestion.py /tmp/repo-shim/test_yazio_nutrition_ingestion.py
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_yazio_nutrition_ingestion.py -q
```

Result: `10 passed in 0.54s`.

Focused Ruff:

```text
PYTHONPATH=/tmp/ruff-env python3 -m ruff check \
  app/services/yazio_nutrition_ingestion.py tests/test_yazio_nutrition_ingestion.py
```

Result: `All checks passed!`.

## Mapping and provenance decisions

- Stable namespaces are explicit: `yazio.consumed_item`, `yazio.product`, `yazio.simple_product`, `yazio.ean`, and `yazio.daily_summary`. The current provider contract has no dedicated `is_ai_generated` member; the service preserves that user-visible value through the provider's existing safe metadata mapping rather than changing the provider/SDK adapter.
- Fingerprints use canonical JSON with Decimal values serialized as strings/tags, civil dates/times serialized explicitly, and no credentials or raw payloads. Metadata is bounded and filters credential/header/cookie/password/secret/token/raw/response keys.
- Product profile nutrient observations are provider fields. Product event nutrient fields are derived fields whose values are `profile nutrient * event amount`; no `/100` or serving scaling is applied. Explicit zero and missing values retain distinct presence states. Simple-product nutrients are direct provider fields with no scaling; unknown Decimal nutrients remain raw provider fields without invented metric/unit mappings.
- Product snapshots carry name, producer, category, base unit, language, flags, provider update time, and safe metadata. Product-ID and EAN identities link to the profile; EAN identities never link to events. Profile servings carry label/amount/unit. Event servings carry label/quantity only and never copy event amount.
- Provider civil datetimes are stored offset-naive, with local date and provider timezone retained separately. Daily summaries are separate observations and fields, never consumption events or projection rows. The current contract has no recipe portion representation, so no recipe portions are inferred.
- Every created field/event/snapshot/serving has one-target provenance. Repeated observations, events, fields, servings, and provenance reuse deterministic identities; changed profile content creates a new immutable snapshot and changed event content creates the reviewed event revision/supersession.

## Schema and migration decision

No migration was added. The reviewed A1 schema and Task 2 repository primitives represent the required user/source-instance composite ownership, source revisions, immutable snapshot hashes, current snapshot pointer, event revisions, Decimal/civil-time/state columns, servings, field observations, provenance, and safe JSON metadata. No projections, heads, analytics, or legacy samples are written.

## Exclusions

No network I/O, live YAZIO calls, provider/SDK changes, sync/import/config/API/frontend/analytics/projection/source-priority changes, migration, or commit from the service were performed. The service only flushes; commit/rollback remains caller-owned.

## Review round 1 fixes

- Canonical provider vocabulary is now explicit: `dietary_energy_kcal`, `protein_g`, `carbohydrates_g`, `fat_g`, `saturated_fat_g`, `fiber_g`, and `sugar_g`, with kcal/g units. Salt remains provider-only raw data; provider raw units are nutrient measurement units rather than profile base units.
- Product-ID identities link to product events in addition to profile links; EAN identities remain profile-only.
- Fingerprints canonicalize only sanitized safe metadata, so filtered token/header changes do not create revisions.
- Successful provider envelopes are complete for the requested range, including empty envelopes; run covered bounds are always the requested bounds.
- Added the minimal repository `advance_current` option and provider-update-time comparison so older snapshots remain history without rewinding the current profile pointer.
- Daily energy-goal field is always persisted; absent goals are missing with the raw `energy_goal` path and no invented metric.
- Profile serving idempotence includes amount and unit. Derived and source event presence uses the resulting value, preserving explicit zero.
- A1 provenance has no identity-link target column; supported snapshot/event/serving/field provenance remains one-target, and no schema was added to invent identity-link provenance.

Review-fix verification:

```text
cp tests/test_yazio_nutrition_ingestion.py /tmp/repo-shim/test_yazio_nutrition_ingestion.py
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_yazio_nutrition_ingestion.py -q
```

Result: `11 passed in 0.71s`.

```text
PYTHONPATH=/tmp/ruff-env python3 -m ruff check \
  app/nutrition/repositories.py app/services/yazio_nutrition_ingestion.py \
  tests/test_yazio_nutrition_ingestion.py
```

Result: `All checks passed!`.

## Review round 2 fixes

- Product-ID to event links use an event-specific bounded hash role, so multiple events sharing a product identity retain independent current links and retries do not append revisions.
- Profile ingestion always returns the incoming snapshot for event binding. Current-pointer advancement is decided inside the repository while holding the profile row lock; the explicit provider-time ordering mode rejects only older known timestamps, while timestamp-less payloads advance deterministically.
- Source-observation presence now preserves explicit zero amounts. Energy goals remain provider-only fields (`energy_goal` path, raw kcal when supplied, no metric/canonical unit), including the missing-goal row.
- Unknown profile nutrient paths do not invent raw units. Known nutrients retain their explicit nutrient units; salt has raw g only and no canonical metric/unit.

Round-two focused verification:

```text
cp tests/test_yazio_nutrition_ingestion.py /tmp/repo-shim/test_yazio_nutrition_ingestion.py
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_yazio_nutrition_ingestion.py -q
```

Result: `17 passed in 1.11s`.

Ruff and `py_compile` passed for the repository helper, ingestion service, and focused tests.

## Review round 3 fix

- The repository's provider-time snapshot ordering query now uses `populate_existing=True` together with `with_for_update()`, refreshing stale SQLAlchemy identity-map state before deciding whether to advance the current pointer. Focused coverage updates the pointer behind the ORM object's back and confirms the locked read uses the refreshed state.

Round-three verification:

```text
cp tests/test_yazio_nutrition_ingestion.py /tmp/repo-shim/test_yazio_nutrition_ingestion.py
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_yazio_nutrition_ingestion.py -q
```

Result: `18 passed in 1.23s`.

Ruff and `py_compile` passed for the repository helper, ingestion service, and focused tests.
