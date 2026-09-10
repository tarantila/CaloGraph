# Nutrition Source-Instance Identity Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `NutritionExternalIdentity` source-instance-scoped with safe fail-closed migration/backfill and exact identity-target compatibility, without implementing B3.

**Architecture:** Choose Variant A. Add `source_instance_id` to `NutritionExternalIdentity`, replace the unscoped unique key and lookup index, and enforce target compatibility in the repository. Keep `NutritionExternalIdentityLink` unchanged: its existing composite user-scoped foreign keys, one-target check, and append-only revision constraints remain DB-enforced; source/provider compatibility remains centralized in the repository. Migration 0028 preflights all existing identity links, fails before schema mutation for orphaned or cross-source identities, backfills only uniquely-derived source instances, then applies constraints. Downgrade fails closed if source-scoped rows would collide under the old key.

**Tech Stack:** Python, SQLAlchemy, Alembic, SQLite, PostgreSQL, pytest, Docker Compose.

**Spec:** User-provided source-instance identity fix requirements in the current conversation.

## Global Constraints

- Do not amend B1 `b51121be444d2cd832b4da80c9e25a50bfb6245a`, B2 `ea3919e494c36898b92f1478b098111f8c9c6cc8`, or prior A3 triage commits.
- Do not implement B3, projections, source priority, cross-provider matching, or EAN matching behavior.
- No guessed source IDs, metadata heuristics, current-connection fallbacks, identity splitting, link rewrites, merges, or revision rewrites.
- Migration preflight must complete before schema mutation; invalid legacy data fails closed with counts only and no external identity values.
- Preserve existing `id`, links, events, profiles, snapshots, fields, provenance, and revisions.
- Use disposable SQLite/PostgreSQL test databases only; never use real `.env` values or persistent production data.

---

### Task 1: Verify current identity design and migration shape

**Files:** `backend/app/nutrition/models.py`, `backend/app/nutrition/repositories.py`, `backend/app/services/yazio_nutrition_ingestion.py`, A1/A3 Alembic revisions, identity/migration tests.

- [x] Confirm the current model/repository/caller evidence and record the selected Variant A design in the plan/report.
- [x] Confirm every production YAZIO identity caller already passes `source_instance_id` for consumed items, products, EANs, simple products, and summaries.
- [x] Confirm link-level DB changes are not needed because source/provider compatibility can be checked centrally and existing link FKs remain user-scoped.

### Task 2: Add RED source-scope tests

**Files:** `backend/tests/test_nutrition_ingestion_repositories.py`, `backend/tests/test_nutrition_repositories.py`, `backend/tests/test_nutrition_constraints.py`, `backend/tests/test_nutrition_isolation.py`, `backend/tests/test_nutrition_models.py`.

- [x] Add tests for same-source retry identity reuse and different-source identity separation.
- [x] Add tests that source A identities cannot link to source B ConsumptionEvent, FoodProfile, or SourceObservation, including no mutation after rejection.
- [x] Add tests for different-user rejection, same-source YAZIO retry idempotency, same Product ID separation, same consumed-item ID separation, and same EAN separation without event/profile hard merge.
- [x] Update direct test fixtures/constructors to provide the required source instance.
- [x] Run the focused repository/constraint tests before production changes and confirm the new expectations fail for the current implementation.

### Task 3: Add RED migration preflight/backfill tests

**Files:** `backend/tests/test_nutrition_migration_sqlite.py`, a new focused migration test module if needed, `backend/tests/test_nutrition_migration_postgres.py` only when the isolated PostgreSQL gate is enabled.

- [x] Add a SQLite 0027-to-0028 fixture with one identity linked only to source A and assert the source backfills to A.
- [x] Add same-source multiple-link evidence and assert it backfills once without rewriting links.
- [x] Add cross-source identity evidence and assert upgrade fails before adding/changing schema or rows.
- [x] Add orphan identity evidence and assert upgrade fails closed before schema/data mutation.
- [x] Add post-upgrade constraint tests: same-source duplicate rejected, cross-source duplicate allowed, `source_instance_id` non-null, old unique/index removed, new unique/index present.
- [x] Add downgrade tests: clean non-colliding data downgrades losslessly if supported; source-scoped collisions fail closed without deletes/merges.
- [x] Run focused migration tests and confirm RED evidence for missing revision/model behavior.

### Task 4: Implement source-scoped model and repository behavior

**Files:** `backend/app/nutrition/models.py`, `backend/app/nutrition/repositories.py`.

- [x] Add non-null `NutritionExternalIdentity.source_instance_id`.
- [x] Replace the identity unique constraint with `(user_id, source_instance_id, provider_key, namespace, identity_value)` and change the lookup index to `(user_id, source_instance_id, provider_key, namespace)`.
- [x] Update `get_or_create_external_identity()` SELECT and INSERT to include `source_instance_id`; preserve same-source idempotency and cross-source separation.
- [x] Update `_require_identity_target_compatibility()` to compare target `provider_key` and exact target `source_instance_id` against the identity after validating target ownership; reject mismatch before link mutation.
- [x] Update `get_or_create_food_profile()` identity validation to require exact user/provider/source compatibility before following or creating the profile link.
- [x] Keep `append_identity_link()`, `get_or_create_identity_link()`, and `current_identity_link()` signatures unchanged unless tests prove an existing caller needs source context; identity scope makes links source-specific through their identity target.

### Task 5: Implement Alembic 0028 safely

**Files:** `backend/alembic/versions/20260910_0028_nutrition_identity_source_scope.py`, migration tests.

- [x] Add revision `20260910_0028` after `20260909_0027`.
- [x] Before any `ALTER`, inspect every existing identity's linked target source-instance set through ConsumptionEvent, FoodProfile, and SourceObservation links.
- [x] Count identities with zero evidence and identities with more than one distinct source; raise a migration error containing only counts.
- [x] Add `source_instance_id` nullable temporarily, backfill only the uniquely-derived source per identity, verify every row is populated, then enforce NOT NULL.
- [x] Drop `uq_nutrition_external_identity_value`, create the source-scoped unique constraint, replace the lookup index, and use SQLite batch operations where direct alteration is unsupported.
- [x] Implement downgrade preflight: if multiple rows would share an old unscoped key, abort before dropping the source column/constraints; otherwise restore the old constraint/index and remove the column without changing IDs or links.
- [x] Ensure all failure paths roll back the migration transaction and do not rewrite domain data.

### Task 6: Verify targeted behavior and schema

- [x] Run identity repository, link compatibility, A3 ingestion, B1, and B2 tests.
- [x] Run SQLite migration upgrade/backfill/conflict/orphan/downgrade tests.
- [x] Run isolated PostgreSQL migration tests, duplicate/NULL constraints, and Alembic `upgrade head && alembic check`.
- [x] Confirm no B2 code changes are required and B2 source-scoped resolution remains green.

### Task 7: Review, commit, and report

- [x] Run Ruff for app/tests/migration and MyPy for app.
- [x] Dispatch independent change-review and security-review agents focused on source isolation, backfill, downgrade, links, EANs, and user isolation.
- [x] Inspect the full diff, Alembic head, commit ancestry, and clean status.
- [ ] Commit the identity fix as a new commit; do not amend or push.
- [ ] Report schema variant, application/DB invariants, migration/backfill outcomes, downgrade semantics, all tests, reviews, final HEAD, and explicit `NUTRITION SOURCE-INSTANCE IDENTITY FIX`, `A3 POST-B2 INTEGRITY TRIAGE`, and `READY FOR B3 IMPLEMENTATION` statuses.
