# Nutrition Daily Projection B5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the seven B4-selected nutrition metrics as immutable, versioned daily projection graphs with deterministic input watermarks, complete B4.5 lineage, atomic head updates, and portable concurrency protection.

**Architecture:** Add a pure `app.nutrition.projection` package for immutable build-input contracts, typed technical tokens, canonical watermarking, and B4/B4.5 mapping. Add one caller-controlled transaction entry point that validates the supplied snapshot, locks the owning `User`, checks history/head integrity, writes projection/facts/lineage inside a savepoint, and updates the head. Reuse the existing A2 ORM tables and low-level repository constructors; do not re-run provider resolution, source priority, or any provider I/O.

**Tech Stack:** Python 3.12, frozen/slots dataclasses, `Decimal`, SHA-256 JSON canonicalization, SQLAlchemy 2, SQLite test harness, disposable PostgreSQL integration tests, pytest, Ruff, mypy, Alembic.

**Spec:** User-provided B5 — Immutable Daily Projection Persistence + Input Watermark + Head.

## Global Constraints

- `PROJECTION_ALGORITHM_VERSION = "nutrition-daily-v1"`.
- `METRIC_REGISTRY_VERSION = "nutrition-metrics-v1"`.
- `WATERMARK_FORMAT_VERSION = "nutrition-watermark-v1"`.
- Exactly `CANONICAL_METRICS` (seven metrics); `salt` is unsupported and never persisted.
- B5 consumes `DailyProjectionBuildInput` only; it does not collect candidates, resolve providers, select priority, call providers, or reconstruct evidence.
- No migration, API, frontend, analytics switch, deployment, push, merge, or release.
- All persistence stays in the caller's transaction; low-level code never calls global `commit()` or `rollback()`.
- Policy absence returns a typed no-mutation result; technical failures roll back the complete new graph.
- Watermark preimages contain only scoped internal IDs, revisions, hashes, version strings, and canonical UTC datetimes; never health values, food/identity values, PII, raw payloads, or credentials.

---

### Task 1: Freeze B5 boundaries and baseline

**Files:**
- Read: `backend/app/nutrition/models.py`, `backend/app/nutrition/repositories.py`, `backend/app/source_priority/contracts.py`, `backend/app/source_priority/selection.py`, existing A2/B4.5 tests.
- Create: `docs/superpowers/plans/2026-09-11-nutrition-daily-projection-b5.md`.

**Interfaces:**
- Existing A2 tables and B4/B4.5 contracts are the only upstream dependencies.
- Later tasks consume the exact names and signatures defined below.

- [ ] **Step 1: Verify baseline**

Run from the repository worktree:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
```

Expected: clean `feature/nutrition-resolution-b1`, HEAD `35c67c9`.

- [ ] **Step 2: Verify schema and lint through the supported container**

Run:

```bash
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci sh -c 'ruff check app tests && alembic heads'
```

Expected: Ruff clean and Alembic head `20260910_0028`.

- [ ] **Step 3: Keep this plan as the execution contract**

No ORM schema changes are permitted. If a required B5 invariant cannot be represented by the existing A2 schema, stop and report it instead of creating revision `0029`.

---

### Task 2: Write RED contract and watermark tests

**Files:**
- Create: `backend/tests/test_nutrition_projection_b5_contracts.py`.
- Create: `backend/app/nutrition/projection/__init__.py` only after RED evidence.

**Interfaces:**
- Tests import `DailyProjectionBuildInput`, `ProjectionInputManifest`, all token classes, `compute_input_watermark`, and version constants from `app.nutrition.projection`.
- Tests use `PriorityPolicySnapshot`, `PriorityRuleSnapshot`, and `PrioritySelection` from existing B4 contracts.

- [ ] **Step 1: Write failing tests first**

Cover: exact seven metric validation, duplicate/missing/extra metric rejection, mixed policy rejection, immutable tuple normalization, token validation (zero UUID, invalid revision/hash, naive tombstone), identical token deduplication, contradictory duplicate rejection, canonical hash shape, order independence for tokens/rules, policy/rank/source/event/field/snapshot/link/tombstone/version changes, `policy_at` exclusion, and token constructors having no raw nutrient-value field.

- [ ] **Step 2: Run the RED suite**

Run:

```bash
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest -q tests/test_nutrition_projection_b5_contracts.py
```

Expected: collection/import failures for the not-yet-created B5 API, not fixture or syntax errors. Record the RED result in the execution notes.

- [ ] **Step 3: Do not add production code before RED**

The first implementation edit starts only after the expected missing-API failure is observed.

---

### Task 3: Write RED persistence and lineage tests

**Files:**
- Create: `backend/tests/test_nutrition_projection_b5.py`.

**Interfaces:**
- Tests call `persist_daily_projection(db, build_input=...) -> ProjectionPersistenceResult`.
- Tests load `NutritionDailyProjection`, `NutritionDailyProjectionFact`, `NutritionDailyProjectionLineage`, and `NutritionProjectionHead` directly for observable assertions.

- [ ] **Step 1: Add SQLite behavior tests before implementation**

Cover first projection v1/head, identical-input no-op after lock, changed watermark v2 with unchanged v1 graph, date/user isolation, policy-missing no mutation, no-winner ready/no-value fact, selected/fallback/rejected/diagnostic mappings, no fake lineage for missing/unconfigured candidates, exact Decimal zero and multi-contribution sums, same-source aggregation metadata, source scope failures, manifest/rule mismatches, head corruption, contribution/value invariants, injected lineage failure rollback, and immutable v1 snapshot after v2.

- [ ] **Step 2: Run the RED suite**

Run:

```bash
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci pytest -q tests/test_nutrition_projection_b5.py
```

Expected: missing B5 persistence import/API failures.

- [ ] **Step 3: Keep tests behavior-focused**

Assert stored facts, lineage, graph counts, watermark/result fields, rollback, and head state; do not assert helper implementation or SQL statement text.

---

### Task 4: Implement immutable B5 contracts and technical tokens

**Files:**
- Create: `backend/app/nutrition/projection/contracts.py`.
- Create: `backend/app/nutrition/projection/tokens.py`.
- Create: `backend/app/nutrition/projection/__init__.py`.

**Interfaces:**
- `DailyProjectionBuildInput(user_id, local_date, selections, input_manifest)`.
- `ProjectionInputManifest(user_id, local_date, projection_algorithm_version, metric_registry_version, watermark_format_version, policy, relevant_rules, technical_evidence)`.
- `SourceObservationToken`, `ConsumptionEventToken`, `FieldObservationToken`, `FoodSnapshotToken`, `IdentityLinkToken`, `TombstoneToken` are frozen/slots contracts.
- All token classes expose deterministic `kind`, `token_id`, and serialization fields; no raw value field exists.
- `ProjectionPersistenceResult` and `ProjectionPersistenceStatus` are defined in `contracts.py` for later persistence use.

- [ ] **Step 1: Implement validation minimally for the RED tests**

Normalize aware datetimes to UTC, require non-zero UUIDs, positive revisions, lowercase 64-character hexadecimal hashes, canonical metric/provider strings when supplied, and immutable tuples. Deduplicate exactly identical technical tokens by canonical identity; reject same kind/ID with differing payload.

- [ ] **Step 2: Validate build scope/policy invariants**

Require exactly the seven canonical metrics once each, identical user/date, one coherent policy snapshot or all seven policy values absent, manifest scope equality, and relevant rule IDs equal to the union of disposition rule IDs. Never infer or reselect a rule.

- [ ] **Step 3: Run contract tests GREEN**

Run the focused contract test command from Task 2. Fix production validation, not assertions, until it passes.

- [ ] **Step 4: Refactor only while green**

Keep contracts pure and independent of SQLAlchemy and provider SDKs.

---

### Task 5: Implement deterministic privacy-safe watermarking

**Files:**
- Create: `backend/app/nutrition/projection/watermark.py`.
- Modify: `backend/app/nutrition/projection/__init__.py`.

**Interfaces:**
- `compute_input_watermark(manifest: ProjectionInputManifest) -> str`.
- Result format is exactly `sha256:<64 lowercase hex>`.

- [ ] **Step 1: Build only canonical JSON**

Serialize a dict with explicit watermark format, user/date, algorithm/metric versions, policy ID/user/version/effective-from (or explicit null), sorted relevant rule snapshots, and sorted technical token payloads using `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Normalize UUIDs to lowercase canonical strings and datetimes to UTC ISO-8601.

- [ ] **Step 2: Exclude prohibited data by construction**

Do not serialize candidate values, contribution values, amounts, food names, producer/EAN/external identity values, source record IDs, raw metadata/payload, secrets, or credentials. `policy_at` is not a manifest field and cannot affect the hash.

- [ ] **Step 3: Run all W1–W20 tests GREEN**

Run the focused contract suite and verify all listed change/no-change semantics.

---

### Task 6: Implement B4 fact mapping and B4.5 lineage mapping

**Files:**
- Create: `backend/app/nutrition/projection/mapping.py`.
- Modify: `backend/app/nutrition/projection/__init__.py`.

**Interfaces:**
- Internal pure mapping functions return validated fact/lineage value objects; they accept only `PrioritySelection` and `ProjectionInputManifest` data.
- Mapping emits exactly seven fact payloads and zero or more lineage payloads; it never queries or reconstructs source data.

- [ ] **Step 1: Map winner/no-winner facts**

Copy winner metric/value/unit/provider/granularity/presence/coverage/resolution/lineage exactly. For no winner emit canonical unit, `None` provider/granularity/value, `unknown` presence/coverage/lineage, and `unresolved` resolution. Add only safe selection diagnostics.

- [ ] **Step 2: Map lineage roles**

Winner `selected`/`fallback` contributing evidence maps to the same role with exact Decimal contribution values; winner diagnostic evidence maps diagnostic. Lower eligible candidates map contributing evidence to rejected with null contribution; ineligible applicable candidates map all evidence diagnostic; missing candidates and candidates without an applicable rule emit no lineage.

- [ ] **Step 3: Aggregate exact same-source relations**

Group by source observation ID and final role, sum only real contributing Decimal values, retain deterministic evidence kind/ID metadata, and emit `multiple_evidence_reasons` plus sorted reason codes when needed. Reject contribution sums that do not exactly equal a non-null fact value, selected/fallback mixing, value-less contribution rows, and zero facts without an exact zero contribution.

- [ ] **Step 4: Run mapping/persistence RED tests to the next failure**

Run the focused B5 persistence suite; expected next failures should be ORM persistence/API gaps rather than mapping contract errors.

---

### Task 7: Implement atomic persistence, policy/source validation, and head integrity

**Files:**
- Create: `backend/app/nutrition/projection/persistence.py`.
- Modify: `backend/app/nutrition/projection/__init__.py`.
- Modify: `backend/app/nutrition/repositories.py` only if a missing low-level read helper is required; preserve existing helper behavior.

**Interfaces:**
- `persist_daily_projection(db: Session, *, build_input: DailyProjectionBuildInput) -> ProjectionPersistenceResult`.
- It never commits or globally rolls back.

- [ ] **Step 1: Validate policy and source observation scope**

For policy-bearing input, validate persisted policy ID/user/version/effective-from and relevant rule snapshots without invoking selection. For every emitted lineage row, query the referenced `NutritionSourceObservation` by ID and verify user and provider match the candidate; reject missing or cross-scope rows. No source ID correction or fake observation is allowed.

- [ ] **Step 2: Acquire the portable serialization lock**

Inside the caller transaction, select the `User` row for update by `user_id` before reading history/head. This serializes all projection writes for that user on PostgreSQL; SQLite remains a functional non-concurrency test only.

- [ ] **Step 3: Enforce head integrity**

Read all scoped projections and the scoped head after the user lock. Allow no history/no head as v1. Reject history without head, head without its scoped projection, a head not pointing at the highest version, and wrong user/date scope. Never repair history.

- [ ] **Step 4: Implement no-op and version creation**

After the lock, return `created=False, reason=unchanged` when the current head matches watermark, algorithm version, and policy ID. Otherwise create `max_version + 1`, seven facts, all mapped lineage, and update/insert the head inside one `begin_nested()` savepoint. Return immutable created result; do not update old rows.

- [ ] **Step 5: Roll back complete graph on any failure**

A mapping, scope, integrity, or injected lineage failure must remove the new projection/facts/lineage and leave head and historical projections unchanged while preserving caller transaction control.

- [ ] **Step 6: Run SQLite B5 suite GREEN**

Run the focused persistence suite and the existing A2/B4.5 suites. Fix implementation defects without weakening assertions.

---

### Task 8: Add PostgreSQL integration and concurrency coverage

**Files:**
- Create: `backend/tests/test_nutrition_projection_b5_postgres.py`.

**Interfaces:**
- Tests use the existing explicit disposable PostgreSQL opt-in contract and `CALOGRAPH_POSTGRES_TEST_URL`; no production database is allowed.

- [ ] **Step 1: Add catalog/constraint checks**

Verify current head remains `20260910_0028`, existing projection/fact/lineage/head tables and composite foreign keys remain usable, unique metric/version/relation constraints hold, and policy/source scope failures reject.

- [ ] **Step 2: Add same-input concurrency test**

Use two independent sessions and a barrier/thread pool against the isolated PostgreSQL database. Both call the same build input and commit. Assert one v1 graph/head, one `created=True`, one `reason=unchanged`, no leaked IntegrityError, and exactly seven facts.

- [ ] **Step 3: Add different-input concurrency test**

Use the same user/date with distinct technical input tokens. Assert two serialized versions `{1, 2}`, two complete graphs, one head at version 2, and no duplicate version or partial graph regardless of scheduling order.

- [ ] **Step 4: Run the opt-in PostgreSQL suite only against the disposable `_test` database**

Run the repository’s explicit PostgreSQL command with `.env.production.example` and verify skipped-vs-enabled status in the output.

---

### Task 9: Full verification and independent reviews

**Files:**
- All changed B5 source/tests; no unrelated files.

- [ ] **Step 1: Run targeted regression matrix**

Run B5 contract/persistence/PostgreSQL tests plus B4.5, B4, B3, B2, B1, and relevant A3 integrity/identity tests.

- [ ] **Step 2: Run full backend quality checks**

Run:

```bash
docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.test.yml run --rm --build backend-ci sh -c 'pytest -q && ruff check app tests && mypy app'
```

- [ ] **Step 3: Verify migrations without changing schema**

Run full disposable PostgreSQL migration suite, `alembic upgrade head`, `alembic check`, and `docker compose ... config --quiet`. Confirm head is still `20260910_0028` and no migration file changed.

- [ ] **Step 4: Request independent change review**

Review specifically for no B6 orchestration, seven metrics, policy/no-winner behavior, lineage roles/sums, privacy watermark, immutability, no-op ordering, first-head race, and atomicity. Fix every material finding with a regression test.

- [ ] **Step 5: Request independent security review**

Review user/date/policy/source scope, source-instance contamination, fake IDs, candidate/value dumps, watermark privacy, diagnostic privacy, head corruption, concurrency, and rollback. Treat any confirmed issue as blocking until fixed and verified.

- [ ] **Step 6: Re-run verification after review fixes**

Repeat affected focused tests and the complete backend quality/schema checks before commit.

---

### Task 10: Commit and final B5 sufficiency gate

**Files:**
- Only the verified B5 implementation/tests/plan.

- [ ] **Step 1: Inspect final diff and worktree**

Run `git diff --check`, `git status --short`, and inspect the complete diff for secrets, raw health values, unrelated changes, TODOs, and schema changes.

- [ ] **Step 2: Create the dedicated commit**

```bash
git add backend/app/nutrition/projection backend/tests/test_nutrition_projection_b5.py backend/tests/test_nutrition_projection_b5_postgres.py docs/superpowers/plans/2026-09-11-nutrition-daily-projection-b5.md
git commit -m "feat: persist nutrition daily projections"
```

Do not amend earlier commits; do not push, merge, tag, release, or deploy.

- [ ] **Step 3: Verify commit and clean status**

Run `git rev-parse HEAD`, `git status --short`, and the required post-commit verification commands. Report exact evidence, not inferred success.

- [ ] **Step 4: Perform the read-only B5 sufficiency gate**

Confirm every B5 requirement is implemented and explicitly confirm the deferred live YAZIO regression gate remains `OPEN`. Stop and wait for B6 authorization.
