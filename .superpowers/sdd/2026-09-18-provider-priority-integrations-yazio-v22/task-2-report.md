# Task 2 Report — provider priority migration

## Files

- `backend/alembic/versions/20260918_0031_provider_priority_and_activity_snapshots.py`
  - Creates `nutrition_target_activity_sources` with immutable snapshot columns, ownership composite FK, positive-priority/source checks, and provider/target indexes.
  - Backfills existing activity target projections, preserving exact `activity_source_type` and only assigning an unambiguous activity provider key.
  - Migrates 0030 preferences into one deterministic, versioned policy per user with priority-1 wildcard rules, preserving existing policy versions.
  - Drops `user_provider_preferences` only after successful backfill.
  - Downgrade fails closed for metric-specific or duplicate current `(user, data_area)` rules; otherwise recreates the legacy table and removes activity snapshots.
- `backend/app/models.py`
  - Adds `NutritionTargetActivitySource`, composite target/user ownership, snapshot constraints/indexes, and `NutritionTarget.activity_sources`.
  - Adds the target/user composite uniqueness key required by the ownership FK.
- `backend/tests/test_provider_priority_migration.py`
  - Focused SQLite migration, backfill, wildcard policy, ownership, constraints, and downgrade tests.
- `backend/tests/test_google_health_migration.py`
  - Updates the Alembic head assertion to 0031.

## Tests and results

- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_priority_migration.py`
  - **8 passed**
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_source_priority_a2.py tests/test_source_priority_b4.py tests/test_google_health_migration.py`
  - **40 passed**
- Full suite, formatter, and linter were intentionally not run per Task 2 instructions.

## Migration safety notes

- Upgrade work is ordered as create/validate snapshot schema, backfill snapshots, create migrated policies/rules, then remove the legacy table; Alembic transaction handling prevents a successful-looking partial migration.
- The migration timestamp is fixed at `2026-09-18T00:00:00Z`; an existing same-user timestamp collision advances deterministically by microseconds while retaining the next policy version.
- Activity provider keys are populated only for source types with exactly one reverse mapping; unknown or ambiguous source types retain the exact source string with `provider_key = NULL`.
- Downgrade validates the latest policy version per user before recreating the lossy legacy representation and raises a precise `RuntimeError` instead of silently dropping metric scope or priorities.
- No live or destructive database action was used; tests use isolated temporary SQLite databases.

## Concerns

- PostgreSQL-specific execution was not run; the focused proof is isolated SQLite plus model/source-priority regression coverage.
- Frontend/YAZIO files were intentionally left unchanged per the brief.

## Review fix

- Policy and rule backfill rows now receive deterministic non-null `created_at` values.
- `backend/app/source_priority/compatibility.py` is the sole compatibility facade for provider preference reads/writes; settings and analytics callers no longer query `UserProviderPreference` directly.
- The legacy ORM mapping is retained only for compatibility with installations/tests where the pre-0031 table still exists; the 0031 upgrade still drops that table after migration.
- Review-fix targeted command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_priority_migration.py tests/test_provider_preferences.py tests/test_source_priority_a2.py tests/test_source_priority_b4.py tests/test_google_health_migration.py`
  - **72 passed**

## Follow-up review fix

- Removed legacy provider-preference setting mirroring from the compatibility facade; reads may still fall back to the legacy table when present, and policy-backed writes create immutable `SourcePriorityPolicy`/`SourcePriorityRule` rows only.
- Updated the provider-preference API regression to assert source-priority persistence and added a regression proving setting a preference does not add legacy rows.
- Follow-up targeted command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_priority_migration.py tests/test_provider_preferences.py tests/test_source_priority_a2.py tests/test_source_priority_b4.py tests/test_google_health_migration.py`
  - **73 passed, 1 warning**

## Transitional legacy-delete fix

- A delete with no source-priority policy now removes the matching legacy row when the pre-0031 table is present, allowing legacy-only preferences to disappear from the read fallback. Policy-backed deletes never mutate legacy rows, and legacy delete database errors propagate without rollback/swallowing.
- Added a regression covering API deletion of a legacy-only preference.
- Transitional-delete targeted command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_priority_migration.py tests/test_provider_preferences.py tests/test_source_priority_a2.py tests/test_source_priority_b4.py tests/test_google_health_migration.py`
  - **74 passed, 1 warning**

## Task 3 priority API

- Extended provider preference PUT payloads with complete ordered provider lists (`providers` entries or `provider_keys`) while preserving the legacy single `provider_key` form.
- GET now returns deterministic entries grouped by `data_area` and ascending wildcard `priority_rank`; persisted providers remain visible regardless of current availability.
- Complete-list replacement validates the entire list, provider support, availability, duplicate keys, and contiguous ranks before creating one immutable policy; invalid requests leave the prior policy unchanged.
- DELETE removes all wildcard preference entries for the requested area in one immutable policy version; availability remains a separate endpoint with stable status literals.
- Added focused tests for ordering, atomic replacement, validation/duplicates/ranks, CSRF, user isolation, delete-all, persisted unavailable providers, and availability statuses.
- Targeted Task 2/3 command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_priority_migration.py tests/test_provider_preferences.py tests/test_source_priority_a2.py tests/test_source_priority_b4.py tests/test_google_health_migration.py`
  - **79 passed, 1 warning**

## Task 3 review fixes

- Explicit `priority_rank` ordering is now honored when the request entries arrive inverted; rank validation and canonical persistence use the declared order.
- Provider preference replacement and deletion lock the user row with `FOR UPDATE` before reading current policy/version state, serializing immutable version allocation and effective timestamps for concurrent writers.
- Added inverted-rank and lock assertion regressions.
- Review-fix targeted command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_preferences.py`
  - **32 passed**

## Task 4 Nutrition fallback

- Provider-neutral Nutrition selection now reads the ordered `SourcePriorityPolicy` wildcard chain, skips globally unavailable owned providers, and fails closed for invalid or ambiguous source ownership/readiness.
- Bounded provider-period reads evaluate each provider once per period chunk, then choose one provider per local day; the selected provider’s full canonical metric map is used without metric-by-metric mixing. Explicit `source=` requests remain on the legacy path.
- Added regressions for first-provider wins, no-data fallback, unavailable/invalid/not-ready handling, complete-scope no-mixing, and bounded multi-provider reads.
- Targeted Task 4 command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_nutrition_provider_boundary.py tests/test_nutrition_multi_provider_c0.py tests/test_provider_daily_serving.py`
  - **40 passed**

## Task 4 review fix

- Provider-day selection now requires all canonical daily metrics to have safe complete/resolved states before treating a provider as evidence-bearing; conflict/duplicate states fail closed, while partial/unresolved providers fall through to the next priority.
- Added a regression for partial/unresolved first-provider data followed by a complete second provider.
- Review-fix targeted command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_nutrition_provider_boundary.py tests/test_nutrition_multi_provider_c0.py tests/test_provider_daily_serving.py`
  - **41 passed**
