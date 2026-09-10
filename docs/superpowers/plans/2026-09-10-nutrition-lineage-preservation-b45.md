# Nutrition Lineage Preservation B4.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve concrete source-observation lineage and all decision-relevant provider candidates from B2 through B1/B3/B4 without changing resolution or priority outcomes.

**Architecture:** Extend the pure B1 `MetricContribution` contract with explicit `EvidenceKind` and `source_observation_id`, and carry resolver-loser evidence in the immutable `ProviderCandidate`. Extend B4 `ProviderDisposition` with the canonical candidate object, preserving `rule_id is None` as the no-applicable-rule boundary. No ORM types, DB re-queries, persistence, migration, B5 code, or provider orchestration are added.

**Tech Stack:** Python 3.14, frozen slotted dataclasses, Decimal, UUID, pytest, SQLAlchemy only in the existing B2 adapter layer.

**Spec:** User-provided B4.5 Projection Lineage Contract Preservation requirements in the current conversation.

## Global Constraints

- No B5 projection persistence or watermark code.
- No schema migration; Alembic head remains `20260910_0028`.
- B1 remains pure Python and imports no ORM models.
- B2 maps ORM rows directly to explicit source-observation references.
- B4 preserves canonical `ProviderCandidate` values and evidence without repr/log dumps.
- Existing resolution, eligibility, parity, priority, metric-specific, wildcard, selected, and fallback outcomes remain unchanged.
- No fake source observations, DB reconstruction, provider re-resolution, event/summary recomputation, or artificial contribution distribution.

---

### Task 1: Add explicit B1 evidence references

**Files:**
- Modify: `backend/app/nutrition/resolution/reasons.py`
- Modify: `backend/app/nutrition/resolution/contracts.py`
- Modify: `backend/app/nutrition/resolution/aggregation.py` only if an invariant helper is required
- Test: `backend/tests/test_nutrition_resolution.py`
- Test: `backend/tests/test_nutrition_provider_boundary.py`
- Create: `backend/tests/test_nutrition_lineage_b45.py`

**Interfaces:**
- `EvidenceKind.FIELD_OBSERVATION`, `CONSUMPTION_EVENT`, and `SOURCE_OBSERVATION` are canonical enum members; existing aliases remain compatibility-safe where needed.
- `MetricContribution` gains required `source_observation_id: UUID`.
- `ProviderCandidate` gains immutable `resolution_diagnostic_evidence` and exposes `contributing_evidence` plus combined `diagnostic_evidence` views.

**Acceptance:**
- Every persisted B2-derived contribution has a concrete source observation ID.
- Contribution values remain Decimal-exact and existing aggregation semantics do not change.
- Candidate value equals the sum of `contributing_evidence` values.
- A value-less candidate has no numeric contributing evidence.

### Task 2: Preserve B2 event, field, and summary provenance

**Files:**
- Modify: `backend/app/services/yazio_nutrition_resolution.py`
- Test: `backend/tests/test_yazio_nutrition_resolution.py`
- Test: `backend/tests/test_nutrition_lineage_b45.py`

**Interfaces:**
- `_missing_contribution(..., source_observation_id=...)` requires the domain relation explicitly.
- Field contributions use `field.source_observation_id`.
- Event missing contributions use `event.source_observation_id`.
- Summary field contributions use `field.source_observation_id`.

**Acceptance:**
- Product, simple-product, daily-summary, missing-event, partial-event, explicit-zero, and Decimal sum cases preserve source observation IDs.
- Event-vs-summary resolution retains losing candidate evidence as diagnostic evidence while selecting exactly one candidate value path.
- No B2 provider outcome changes except richer immutable evidence.

### Task 3: Preserve B4 candidate dispositions

**Files:**
- Modify: `backend/app/source_priority/contracts.py`
- Modify: `backend/app/source_priority/selection.py`
- Test: `backend/tests/test_source_priority_b4.py`
- Test: `backend/tests/test_nutrition_lineage_b45.py`

**Interfaces:**
- `ProviderDisposition.candidate: ProviderCandidate | None` is canonical and optional only when no candidate exists.
- Applicable rules retain candidates for selected/fallback/rejected/diagnostic dispositions.
- `rule_id is None` remains the explicit no-applicable-rule boundary; those candidates are diagnostic and not decision-relevant.
- `PrioritySelection` validates that the selected/fallback disposition candidate equals `selected_candidate`.

**Acceptance:**
- Winning, rejected eligible, and ineligible applicable candidates are preserved unchanged.
- Missing applicable providers retain `candidate=None`.
- Unconfigured candidates remain diagnostic and non-decision-relevant.
- Candidate input order does not alter preservation.

### Task 4: Verify B4.5 and repeat sufficiency gate

**Commands:**
- `pytest -q tests/test_nutrition_lineage_b45.py tests/test_nutrition_resolution.py tests/test_yazio_nutrition_resolution.py tests/test_nutrition_provider_boundary.py tests/test_source_priority_b4.py`
- `pytest -q`
- `ruff check app tests`
- `mypy app`
- PostgreSQL migration suite and `alembic check`

**Acceptance:**
- All targeted and full tests pass.
- No migration or B5 code exists.
- Read-only sufficiency review can identify concrete source observation IDs, exact contributing values, rejected/diagnostic candidate evidence, and selected/fallback roles from `PrioritySelection` alone.
- Independent change/security reviews report no material findings.
- Separate B4.5 commit; no push, merge, release, tag, or deployment.
