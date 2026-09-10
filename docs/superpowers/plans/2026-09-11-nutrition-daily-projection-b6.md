# Nutrition Daily Projection B6 Implementation Plan

**Goal:** Orchestrate exactly one nutrition day from persisted YAZIO source data through B3 candidate resolution, B4 priority selection, a B5 input manifest, and B5 projection persistence.

**Scope:** Backend application orchestration only. No frontend/API/scheduler/migration/deployment/merge/release work.

## Frozen Contracts

- B0/B0.1 and B1-B5 remain unchanged unless a narrowly required B6 integration defect is proven.
- Production provider registry contains only `yazio`.
- The canonical metric set is exactly `CANONICAL_METRICS` (seven metrics).
- The caller supplies one timezone-aware `policy_at`; B6 normalizes it once and uses one immutable policy snapshot for all seven selections.
- B5 remains commit-free and owns its existing `User` row lock, history/head checks, manifest validation, watermark comparison, and projection writes.
- B6 returns the existing typed `ProjectionPersistenceResult`; it does not expose raw health values or provider payloads.

## Transaction and Snapshot Contract

- Entry requires a clean SQLAlchemy `Session`; an already active transaction fails closed before any SQL.
- B6 owns one complete application transaction per attempt and commits only after `persist_daily_projection` succeeds.
- PostgreSQL attempts set `REPEATABLE READ` before the first SQL statement. SQLite uses the repository's isolated test transaction semantics.
- The first snapshot covers policy lookup, source-instance lookup, all B3 reads, all B4 selections, manifest reads/cross-checks, B5 validation, and B5 writes. No early B6 `User ... FOR UPDATE` is used: B5 already supplies write serialization, and a lock waiter cannot refresh a PostgreSQL repeatable-read snapshot.
- Every retry rolls back the entire attempt and starts a new transaction with a new snapshot. No read graph or write fragment is retried independently.

## Bounded Retry Contract

Retry only PostgreSQL concurrency failures:

- SQLSTATE `40001` (`serialization_failure`)
- SQLSTATE `40P01` (`deadlock_detected`)
- the specifically identified first-projection race on `uq_nutrition_projections_user_date_version`

Never retry arbitrary `IntegrityError` instances. Extract SQLSTATE/constraint names from the DBAPI diagnostic object and require the PostgreSQL dialect. Use a bounded maximum of three attempts. Exhaustion raises a typed `NutritionProjectionConcurrencyError` after full rollback; no partial graph escapes.

## Orchestration Flow

1. Validate `user_id`, `local_date`, and timezone-aware `policy_at` through existing contracts.
2. Start a PostgreSQL repeatable-read transaction before any query.
3. Resolve the YAZIO `source_instance_id` from the user's unique `YazioConnection`, unless an explicitly scoped internal source-instance override is supplied for tests.
4. Load `get_effective_policy_snapshot` exactly once with the normalized `policy_at`.
5. For every canonical metric, call the B3 provider boundary with the static provider key `yazio`; collect exactly one candidate mapping and call `select_by_source_priority` with the same policy snapshot.
6. Build a manifest from the same transaction snapshot. Include only rules appearing in actual B4 dispositions and technical evidence reachable from candidates on those applicable rule paths. Exclude candidates behind non-applicable rules and unconfigured providers.
7. Create source-observation tokens from matching persisted rows and evidence tokens for events, fields, food snapshots, identity links, and tombstones that B2 actually consulted for those candidate paths. Include superseded revision-chain rows when they are needed to validate the current event revision. Never include raw values, provider identities, metadata, or payloads.
8. Cross-check manifest scope against all seven selections, B4 rule IDs, candidate lineage source IDs, user/date/provider scope, and B5 token contracts.
9. Build `DailyProjectionBuildInput` and call `persist_daily_projection` exactly once per attempt.
10. Commit and return the B5 `ProjectionPersistenceResult`. On a retryable error, rollback and restart from step 2.

## Required Verification

Synthetic SQLite coverage must prove:

- seven metrics and one shared policy snapshot/policy timestamp;
- policy-missing typed no-mutation result;
- no-value versus explicit-zero persistence;
- partial-event priority behavior;
- metric-specific versus wildcard rule evidence;
- unconfigured-provider exclusion from the manifest;
- relevant revision and policy changes alter the watermark/version;
- unrelated/non-applicable changes do not alter the watermark;
- candidate/manifest mismatch is rejected before persistence;
- complete B4.5 lineage token coverage without raw values.

PostgreSQL coverage must prove:

- candidate and manifest remain on one repeatable-read snapshot while a concurrent nutrition revision commits;
- a subsequent B6 run sees the new revision;
- concurrent policy changes are snapshot-consistent;
- same-input stale-snapshot writer race retries and returns unchanged;
- different-input stale-snapshot writer race retries and creates the next version;
- no duplicate version, inconsistent head, or leaked raw database error;
- bounded retry and lock/deadlock behavior are explicit.

## Delivery Gates

- Write B6 RED tests before production orchestration code.
- Run targeted tests, backend tests, Ruff, and mypy using the repository's current commands.
- Run independent change and security reviews after implementation.
- Keep the live YAZIO regression gate hard: never use secrets or provider writes; if safe credentials are unavailable, report `B6 IMPLEMENTED: YES`, `LIVE YAZIO REGRESSION GATE: OPEN`, `READY FOR MERGE: NO`.
- Create only the requested B6 commit: `feat: orchestrate nutrition daily projections`. Do not amend B5 or perform merge/push/release/deploy actions.
