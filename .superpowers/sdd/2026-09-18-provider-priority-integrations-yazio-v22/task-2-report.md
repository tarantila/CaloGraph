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

## Task 5 Weight fallback

- Weight serving now carries the ordered, available scalar provider chain from the existing `SourcePriorityPolicy` compatibility facade.
- Weight reads use one bounded, user-scoped `HealthSample` query for all prioritized providers, apply the YAZIO connection `source_identifier`, choose the first provider with an actual sample for each local day, and choose the latest timestamp within that provider/day.
- Missing days remain missing: no synthetic YAZIO last-on-or-before behavior and no forward-fill. Existing response shape and configured `selected_provider` semantics remain unchanged.
- Added regressions for YAZIO-over-Apple, Apple-over-YAZIO, no-sample fallback to Health Auto Export, no forward-fill, latest-of-day, and user isolation.
- TDD red proof: the new fallback test failed before implementation with an empty points list.
- Targeted command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_preferences.py`
  - **36 passed**
- Compile proof:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci python -m py_compile app/analytics/scalar_selection.py app/api/analytics.py tests/test_provider_preferences.py`

## Task 5 concerns

- The focused proof used the isolated Docker test database; project-wide validation remains with the integration owner.

## Task 6 Activity fallback and historical snapshots

- Added snapshot-aware Activity resolution to the shared daily-point builder. For each local day, active energy is selected from the target’s immutable `NutritionTargetActivitySource` chain in priority order; source totals are never summed across providers. Targets without snapshots retain the legacy `activity_source_type` fallback.
- Target creation and new target versions persist complete immutable snapshot rows, with `activity_source_type` retained as the priority-1 compatibility projection.
- Activity provider-priority changes now create a new effective target version when the current target is historical, preserve prior target/snapshot rows, and replace only the current same-day snapshot chain. Snapshot replacement deletes old rows before inserting reordered priorities to respect the unique provider constraint.
- Added regressions for per-day fallback without cross-provider summing, immutable historical chains, and same-day priority-chain replacement.
- TDD red proof: the snapshot regression initially observed no snapshot rows after Activity provider preference replacement.
- Targeted Activity consumer command:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_analytics.py tests/test_provider_preferences.py tests/test_calendar_canonical.py tests/test_weekly_canonical.py tests/test_weekdays_canonical.py tests/test_trends_canonical.py tests/test_dashboard_summary.py`
  - **160 passed**
- Provider-daily regression:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_daily_serving.py`
  - **14 passed**
- Target API regression:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_auth_api.py -k 'target or activity'`
  - **28 passed**
- Compile proof:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci python -m py_compile app/services/provider_preferences.py app/api/settings.py app/analytics/service.py app/analytics/provider_daily.py app/analytics/daily_point_parity.py tests/test_analytics.py tests/test_provider_preferences.py`

## Task 6 concerns

- Project-wide validation remains with the integration owner; no frontend/YAZIO files or live databases were touched.

## Task 6 review fixes

- Historical Activity mode/source changes now version targets instead of mutating historical target rows or their immutable snapshot chains; full versions derive the complete current Activity policy chain. New targets likewise derive the complete current chain while retaining the priority-1 projection.
- TDD red proof: the focused review regressions were added before the production fixes; the portable snapshot test then failed with a missing `activity_sources` export field. Historical and new-target regressions now pass against the completed implementation.
- Review-fix targeted commands:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_preferences.py -k 'historical_activity_mode or new_target_captures'` — **2 passed**
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_original_scope.py -k 'activity_snapshot_chain'` — **1 passed**
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_provider_preferences.py` — **39 passed**
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_original_scope.py -k 'portable'` — **26 passed**
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_analytics.py tests/test_provider_daily_serving.py` — **42 passed**
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest tests/test_auth_api.py -k 'target or activity'` — **28 passed**
- Compile proof:
  - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci python -m py_compile app/api/settings.py app/services/data_export.py app/services/portable_import.py tests/test_provider_preferences.py tests/test_original_scope.py`

## Task 6 review-fix concerns

- Project-wide validation remains with the integration owner; no frontend/YAZIO files or live databases were touched.
