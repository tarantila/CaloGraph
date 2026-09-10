# Nutrition Source Priority B4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an immutable, user-scoped, policy-driven cross-provider selection boundary above B1/B3 and below future B5 projection persistence.

**Architecture:** Keep A2 SQLAlchemy models and low-level repositories backward compatible. Add immutable pure contracts and a pure selector under `app.source_priority`; add an application service for full policy-version creation and effective-policy snapshot loading. The selector accepts only B1 `ProviderCandidate` values, invokes B1 eligibility, applies metric-specific-over-wildcard rules, and never evaluates provider quality.

**Tech Stack:** Python 3.14, frozen slotted dataclasses, `StrEnum`, SQLAlchemy `Session`, pytest, Ruff, MyPy, SQLite test harness, isolated PostgreSQL migration tests.

**Spec:** User-provided B4 Cross-Provider Source Priority Selection requirements in the current conversation.

## Global Constraints

- B0/B0.1, B1, B2, B3, source-instance identity, and Alembic head `20260910_0028` remain unchanged.
- No migration, projection persistence, projection head, watermark, version, orchestration, API, frontend, analytics cutover, live provider access, push, merge, release, tag, or deployment.
- Reuse the canonical B1 `ProviderCandidate` and `is_candidate_eligible`; do not duplicate candidate semantics.
- Policy selection is user-configured priority only; coverage, lineage, resolution, confidence, completeness, evidence count, parity, and value never become quality scores or tie-breakers.
- `policy_at` is an explicit timezone-aware input and is normalized to UTC; `local_date` remains a nutrition date scope and never becomes the policy timestamp.
- Policy versions and complete rule sets are immutable after creation. `add_rule(...)` remains a legacy low-level helper and is not used by the B4 full-version creation path.
- Policy creation uses a local savepoint/transaction boundary, flushes policy and all rules atomically, and never performs a surprising global commit.
- Production selection never uses `rule_id`, `provider_key`, or any other technical field to resolve equal applicable ranks. Duplicate rank/provider conflicts fail closed with `priority_rank_conflict` / contract error. Technical ordering is allowed only for deterministic disposition output after valid unique ranks are established.
- The deferred live gate remains exactly `DEFERRED LIVE YAZIO REGRESSION GATE: OPEN`.

---

### Task 1: Add immutable priority contracts

**Files:**
- Modify: `backend/app/source_priority/contracts.py`
- Test: `backend/tests/test_source_priority_b4.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class PriorityRuleSpec:
    data_area: str
    metric_key: str | None
    provider_key: str
    priority_rank: int

@dataclass(frozen=True, slots=True)
class PriorityRuleSnapshot:
    rule_id: UUID
    data_area: str
    metric_key: str | None
    provider_key: str
    priority_rank: int

@dataclass(frozen=True, slots=True)
class PriorityPolicySnapshot:
    policy_id: UUID
    user_id: UUID
    version: int
    effective_from: datetime
    rules: tuple[PriorityRuleSnapshot, ...]
```

Also define immutable selection result contracts, `PriorityReasonCode`, `PrioritySelectionRole`, and `AppliedPriorityScope`. Required reason values include `metric_specific_priority`, `wildcard_priority`, `lower_priority_provider`, `provider_not_available`, `priority_policy_missing`, `no_eligible_provider`, `priority_rank_conflict`, `provider_not_configured_for_metric`, and a distinct ineligible-candidate diagnostic. Contract construction normalizes aware timestamps to UTC, rejects naive timestamps, canonicalizes rules to tuples, validates positive version/ranks and canonical nutrition metrics, and rejects duplicate provider/rank within every `(data_area, metric_key)` scope. Duplicate conflicts are never resolved by technical ordering.

- [ ] Write RED tests for frozen snapshots, tuple rules, UTC normalization, naive timestamp rejection, canonical metric validation, and duplicate rule conflicts.
- [ ] Run the focused tests and verify the expected missing-contract failure.
- [ ] Implement the immutable contracts and central rule-set validation.
- [ ] Run the focused contract tests GREEN.

### Task 2: Add atomic full-policy creation

**Files:**
- Create: `backend/app/source_priority/application.py`
- Modify: `backend/app/source_priority/__init__.py`
- Test: `backend/tests/test_source_priority_b4.py`

**Interfaces:**

```python
def create_policy_with_rules(
    db: Session,
    user_id: UUID,
    version: int,
    effective_from: datetime,
    rules: Sequence[PriorityRuleSpec],
) -> PriorityPolicySnapshot: ...
```

Validate the complete input before any write. Inside `db.begin_nested()`, create the new `SourcePriorityPolicy`, flush it for its generated ID, add every `SourcePriorityRule`, flush once more, and construct a snapshot. Do not call `add_rule(...)`, mutate an existing policy, or commit. A failed validation or database constraint must leave no half-created policy/rules and must leave the caller’s outer transaction usable according to the repository convention.

- [ ] Write RED tests for successful multi-rule creation, duplicate provider/rank rejection, invalid values, independent metric/data-area scopes, no global commit, rollback of a failed complete set, and v1 immutability after v2 creation.
- [ ] Run the creation tests and verify the expected missing-application failure.
- [ ] Implement the savepoint-backed full-version creation path.
- [ ] Run the creation tests GREEN.

### Task 3: Add effective policy snapshot loading

**Files:**
- Modify: `backend/app/source_priority/application.py`
- Modify: `backend/app/source_priority/repositories.py` only if a narrow adapter helper is required
- Test: `backend/tests/test_source_priority_b4.py`

**Interfaces:**

```python
def get_effective_policy_snapshot(
    db: Session,
    user_id: UUID,
    policy_at: datetime,
) -> PriorityPolicySnapshot | None: ...
```

Use the existing user-scoped `get_effective_policy(...)` with the explicit `policy_at`, then load the complete rule set through `list_rules(...)`. Convert ORM rows into immutable snapshots without exposing ORM objects to pure selection. Treat legacy SQLite round-tripped UTC policy timestamps as stored UTC in this adapter, but reject naive caller `policy_at`. Return `None` when no effective policy exists; never select defaults or a policy from another user.

- [ ] Write RED tests for v1/v2 selection before/after `effective_from`, offset-equivalent UTC times, naive `policy_at`, cross-user isolation, complete rule loading, and missing policy.
- [ ] Run the lookup tests and verify the expected missing adapter failure.
- [ ] Implement the effective snapshot adapter with no current-time calls.
- [ ] Run the lookup tests GREEN.

### Task 4: Implement pure source-priority selection

**Files:**
- Create: `backend/app/source_priority/selection.py`
- Modify: `backend/app/source_priority/application.py`
- Modify: `backend/app/source_priority/__init__.py`
- Test: `backend/tests/test_source_priority_b4.py`

**Interfaces:**

```python
def select_by_source_priority(
    *,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    candidates: Mapping[str, ProviderCandidate] | Sequence[ProviderCandidate],
    policy: PriorityPolicySnapshot | None,
) -> PrioritySelection: ...

def select_nutrition_candidate(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    metric_key: str,
    candidates: Mapping[str, ProviderCandidate] | Sequence[ProviderCandidate],
    policy_at: datetime,
) -> PrioritySelection: ...
```

The pure selector first validates candidate type/scope/unique provider keys, then computes B1 `is_candidate_eligible(...)`, then resolves applicable rules. For the requested `data_area="nutrition"`, any metric-specific rules for the requested metric replace the wildcard set completely; otherwise only wildcard rules apply. Sort valid unique rules by `priority_rank` for selection. Technical keys may order already-valid dispositions only; they must never resolve a duplicate rank/provider conflict. For each rule, record missing-provider or ineligible-candidate diagnostics, choose the first eligible candidate, assign `selected` if no higher rule was skipped or `fallback` otherwise, and mark lower eligible candidates `rejected`. Candidates with no applicable rule receive non-contributing diagnostic dispositions. Return explicit outcomes for missing policy, no applicable rule, and no eligible provider. Preserve explicit zero values and never sum or rewrite candidates.

- [ ] Write RED tests for the complete S1–S18 matrix: wildcard selection/fallback, missing rule candidate, metric-specific override without wildcard fallback, missing policy, no applicable rule, no eligible candidate, unconfigured candidates, uncertain/partial priority, explicit zero, duplicate pure rules, candidate scope mismatch, input-order determinism, selected/fallback roles, and rejected/diagnostic dispositions.
- [ ] Run the pure-selection tests and verify the expected missing-selector failure.
- [ ] Implement the minimal pure selector and explicit-time DB wrapper.
- [ ] Run the selection tests GREEN.

### Task 5: Verify boundaries and regressions

**Files:**
- Inspect: `backend/app/nutrition/resolution/eligibility.py`
- Inspect: `backend/app/nutrition/resolution/providers.py`
- Inspect: `backend/app/nutrition/models.py`
- Inspect: `backend/app/nutrition/repositories.py`
- Test: `backend/tests/test_source_priority_a2.py`
- Test: B1/B2/B3/A3 and source-instance identity regression suites

Confirm the selector imports B1 eligibility but not provider internals, source-priority policy evaluation, projection persistence, or network clients. Confirm policy creation is the only new write path, the pure selector performs no ORM writes, no values are aggregated, and no provider order or candidate order changes selection results. Confirm the partial-event/uncertain/explicit-zero contracts remain B1-owned.

- [ ] Run focused B4 tests plus A2/B1/B2/B3/A3/source-instance regressions.
- [ ] Run full backend pytest, Ruff, and MyPy.
- [ ] Run isolated PostgreSQL `alembic upgrade head`, `alembic check`, and migration tests; verify head `20260910_0028`.
- [ ] Run `git diff --check` and inspect all changed files for forbidden scope, secrets, raw health values, and placeholders.

### Task 6: Independent review and delivery

**Files:**
- Modify: none unless review findings require regression-tested fixes.

- [ ] Dispatch an independent change review focused on eligibility-before-priority, no quality scoring, metric override, fallback roles, immutability, explicit `policy_at`, and B3/B5 boundaries.
- [ ] Dispatch an independent security review focused on cross-user policy isolation, candidate scope validation, untrusted rules/provider keys, savepoint atomicity, no hidden defaults, and sensitive output.
- [ ] Fix material findings with regression tests and re-run affected verification.
- [ ] Create a separate commit: `feat: apply nutrition source priority`.
- [ ] Verify clean status, protected commit ancestry, Alembic head, no push/merge/release, and report the deferred live gate unchanged.
