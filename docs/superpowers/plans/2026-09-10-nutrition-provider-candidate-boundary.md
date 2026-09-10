# Nutrition Provider Candidate Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a provider-neutral, read-only B3 dispatch and collection boundary around the existing B1 `ProviderCandidate` and B2 YAZIO resolver.

**Architecture:** Add a small typed resolver protocol and a static in-process registry under `app.nutrition.resolution`. A generic single-provider operation dispatches by technical provider family, validates the returned canonical `ProviderCandidate` against the requested user/date/metric/provider scope, and propagates provider programming/invariant errors. A pure collection operation resolves each requested provider exactly once in explicit input order and returns candidates without selecting, sorting by quality, merging, summing, or persisting them. The YAZIO adapter is a thin wrapper over `app.services.yazio_nutrition_resolution.resolve_yazio_metric`; no provider logic is copied.

**Tech Stack:** Python 3.14, dataclasses/typing Protocol, SQLAlchemy Session only at the adapter boundary, pytest, Ruff, MyPy.

**Spec:** User-provided B3 Generic Provider Candidate Interface requirements in the current conversation.

## Global Constraints

- Reuse the existing B1 `app.nutrition.resolution.contracts.ProviderCandidate`; do not create a second candidate envelope.
- No source-priority policy import, policy evaluation, quality ordering, projection persistence, watermark, heads, orchestration, API, frontend, migration, live provider access, push, merge, release, or deployment.
- Provider keys are provider families; the only production registry entry is `yazio`.
- Source-instance scope is an explicit resolver input; B3 never infers, merges, or rewrites source instances.
- The registry is static and code-owned; never import arbitrary modules or execute user-controlled plugin paths.
- Unknown providers fail closed with a typed `ProviderNotAvailableError`; a registered resolver returning no data remains the B1 missing/unsupported candidate outcome.
- Duplicate provider keys fail fast; collection preserves caller provider order deterministically.
- Candidate scope mismatches raise `ProviderCandidateContractError`; no automatic correction or fallback.
- Provider exceptions and invariant errors propagate; broad exception-to-missing conversion is forbidden.
- B3 performs no commits, flushes, ORM writes, or projection writes.
- The deferred live YAZIO regression gate remains open and is not bypassed.
- Existing B1, B2, A3, and source-instance identity behavior must remain green.

---

### Task 1: Define the generic resolver contract

**Files:**
- Create: `backend/app/nutrition/resolution/providers.py`
- Modify: `backend/app/nutrition/resolution/__init__.py`
- Test: `backend/tests/test_nutrition_provider_boundary.py`

**Interface:**

```python
class NutritionProviderResolver(Protocol):
    provider_key: str

    def resolve_metric(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        local_date: date,
        metric_key: str,
    ) -> ProviderCandidate: ...
```

The generic boundary exposes `ProviderCandidateContractError` for returned candidate scope/type violations and `ProviderNotAvailableError` for unknown registry keys. Keep the protocol minimal and annotate the SQLAlchemy session only where the resolver invocation needs it; no persistence behavior belongs to the boundary.

- [x] Write RED tests for the protocol-facing dispatch shape, canonical candidate type, and typed errors.
- [x] Run the focused tests and record the expected missing-boundary failure.
- [x] Add only the contract types and exports needed by later tasks.
- [x] Run the focused contract tests GREEN.

### Task 2: Add a static resolver registry and YAZIO adapter

**Files:**
- Create: `backend/app/nutrition/resolution/providers.py`
- Modify: `backend/app/nutrition/resolution/__init__.py`
- Test: `backend/tests/test_nutrition_provider_boundary.py`

**Interfaces:**

```python
def resolve_provider_metric(
    db: Session,
    *,
    provider_key: str,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> ProviderCandidate: ...

def collect_provider_candidates(
    db: Session,
    *,
    provider_keys: Sequence[str],
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    metric_key: str,
) -> Mapping[str, ProviderCandidate]: ...
```

Register only `yazio` through a thin adapter that calls `resolve_yazio_metric` exactly once for each single-provider request. The registry is immutable/static from the caller perspective. Validate supported metrics through the existing B1 metric registry before invoking a resolver; preserve B1 unsupported-metric semantics without inventing values.

- [x] Write RED tests for YAZIO dispatch exactly once, unknown provider failure, duplicate provider failure, and deterministic collection order.
- [x] Add the static registry and YAZIO adapter with no copied YAZIO event/summary logic.
- [x] Run focused GREEN tests.

### Task 3: Enforce scope and output invariants

**Files:**
- Modify: `backend/app/nutrition/resolution/providers.py`
- Test: `backend/tests/test_nutrition_provider_boundary.py`

After every resolver call, require an actual B1 `ProviderCandidate` and exact equality for `provider_key`, `user_id`, `local_date`, and `metric_key`. Preserve the object unchanged, including values, statuses, reason, diagnostics, and lineage. Do not catch provider exceptions. The existing B1 constructor remains the source of metric/unit/Decimal/Reason/Granularity validation; B3 only validates the boundary-level scope and type.

- [x] Add RED tests for provider, user, date, metric, and candidate-type mismatches.
- [x] Add RED tests proving provider invariant exceptions propagate rather than becoming missing.
- [x] Implement fail-closed scope/type validation.
- [x] Run GREEN tests for all scope and error-boundary cases.

### Task 4: Add provider collection and source-instance regressions

**Files:**
- Modify: `backend/app/nutrition/resolution/providers.py`
- Test: `backend/tests/test_nutrition_provider_boundary.py`
- Test: `backend/tests/test_yazio_nutrition_resolution.py`
- Test: `backend/tests/test_nutrition_resolution.py`

Collection returns one envelope per requested provider, preserves explicit provider order, never chooses a winner, never performs quality sorting, and never merges or sums candidates. Synthetic fake resolvers may represent Google/Apple only inside tests; no production adapters or keys are registered. Add a YAZIO source-instance regression using two synthetic source IDs and verify B3 passes the selected source to B2 without cross-source merging.

- [x] Add RED tests for two fake providers, partial/complete and uncertain/confirmed coexistence, missing-versus-value distinction, deterministic output, and source-instance forwarding.
- [x] Implement collection with duplicate detection and no winner logic.
- [x] Run GREEN collection and source-instance tests.

### Task 5: Verify exclusions and regression surface

**Files:**
- Inspect: `backend/app/nutrition/source_priority`, `backend/app/nutrition/models.py`, `backend/app/nutrition/repositories.py`, B1/B2 services and tests.
- Modify: only B3 boundary files/tests if a verified regression requires it.

Confirm B3 imports no source-priority modules, performs no ORM writes/flushes/commits, has no projection or orchestration path, and accepts only canonical v1 metrics. Confirm `salt` remains unsupported and B2 tests remain unchanged and green.

- [x] Run focused B3 GREEN suite plus B1, B2, A3, and source-instance identity regression suites.
- [x] Run full backend pytest, Ruff, and MyPy.
- [x] Run isolated PostgreSQL Alembic upgrade/check and confirm head remains `20260910_0028`.
- [x] Review diff for forbidden scope and sensitive output.

### Task 6: Independent reviews and delivery

**Files:**
- Modify: none unless review findings require a regression-tested fix.

- [x] Dispatch independent change review for provider neutrality, B1 reuse, dispatch, validation, determinism, and exclusions.
- [x] Dispatch independent security review for registry injection, user/date/provider/source scope, exception handling, PII, and read-only behavior.
- [x] Fix material findings with regression tests and re-review.
- [x] Create a separate commit: `feat: add nutrition provider candidate boundary`.
- [x] Verify clean worktree, preserved prior commits, no push/merge/release, and report `B3 IMPLEMENTED: YES` plus `DEFERRED LIVE YAZIO REGRESSION GATE: OPEN`.
