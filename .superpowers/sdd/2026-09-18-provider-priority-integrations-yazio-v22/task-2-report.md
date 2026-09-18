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
- Runtime analytics/API behavior and frontend/YAZIO files were intentionally left unchanged per the brief.
