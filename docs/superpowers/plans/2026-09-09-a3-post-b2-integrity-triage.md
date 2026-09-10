# A3 Post-B2 Integrity Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Verify the five reported A3 YAZIO integrity risks against the existing contracts, fix only confirmed defects in separate commits, and leave B1/B2 unchanged.

**Architecture:** Trace provider parsing, A3 persistence, A1 domain constraints, and B2 read semantics for each finding. Use focused failing regression tests before each confirmed fix. Do not add cross-provider abstractions, projection persistence, source priority, or migrations.

**Tech Stack:** Python, FastAPI services, SQLAlchemy, pytest, SQLite test harness, disposable PostgreSQL, Alembic, Ruff, MyPy.

**Spec:** User-provided A3 post-B2 integrity triage request in the current conversation.

## Global Constraints

- Do not amend B1 `b51121be444d2cd832b4da80c9e25a50bfb6245a` or B2 `ea3919e494c36898b92f1478b098111f8c9c6cc8`.
- Do not implement B3, Cross-Provider abstraction, Projection persistence, or Source Priority.
- No migration unless the existing schema cannot express the required isolation; stop and report before migration.
- No live provider read, credentials, push, merge, release, tag, deployment, or unrelated refactor.
- Confirmed bug fixes require RED regression test, minimal fix, GREEN verification.

---

### Task 1: Triage requested date boundaries

**Files:** `backend/app/services/yazio_nutrition_ingestion.py`, provider models, A3 ingestion tests.

Trace requested range validation, provider civil datetime/date mapping, event and summary `local_date`, retries, and persistence. Reproduce boundary and out-of-range cases before deciding classification.

### Task 2: Triage source-instance identity isolation

**Files:** A1 nutrition models/repositories and `backend/app/services/yazio_nutrition_ingestion.py`.

Trace `NutritionExternalIdentity`, `NutritionExternalIdentityLink`, FoodProfile/Snapshot lookup, event identity lookup, source observations, and user/source-instance filters. Test equal IDs across two YAZIO connections, equal product IDs, equal EAN evidence, same-source retry, and different-user isolation. Stop before migration if the schema cannot represent the contract.

### Task 3: Triage ingestion-run coverage

**Files:** A3 ingestion service, run model, existing ingestion tests.

Determine the exact meaning of requested range, provider-read success, empty days, missing summaries, partial responses, exceptions, rollback, and retry. Verify whether run-level coverage affects B2 candidate semantics.

### Task 4: Triage deleted profile preservation

**Files:** A3 product/profile persistence helpers, A1 food profile/snapshot models, ingestion tests, B2 read tests.

Verify `is_deleted` persistence, profile status/current snapshot changes, immutable event binding, historical event preservation, and future deleted-product behavior.

### Task 5: Triage metadata truncation

**Files:** A3 metadata sanitization/fingerprinting helpers and ingestion tests.

Identify metadata shape, cap, security filtering order, hashing inputs, idempotency effects, collision risk, and provenance relevance. Classify based on observed use, not speculation.

### Task 6: Add RED tests for confirmed boundary defects

Add the smallest failing tests for every confirmed blocking or integrity defect. Include exact start/end boundaries, naive civil datetimes around midnight, out-of-range events/summaries, retry behavior, and source-instance identity cases as applicable.

### Task 7: Implement minimal confirmed A3 fixes

Modify only the A3 persistence paths required by confirmed RED tests. Preserve historical immutable snapshots/events, avoid raw payload dumping, and do not modify B1/B2.

### Task 8: Run targeted triage regressions

Run A3 ingestion tests, B1 tests, B2 tests, and all new regression tests. Confirm RED-to-GREEN evidence for each fix.

### Task 9: Run full backend verification

Run full backend pytest, Ruff, MyPy app checks, disposable PostgreSQL migration suite, and `alembic check`. Confirm Alembic head remains `20260909_0027` unless explicitly stopped for schema review.

### Task 10: Perform independent change review

Dispatch read-only change review for the triage diff. Evaluate every finding and fix; do not let the reviewer mutate code.

### Task 11: Perform independent security review

Dispatch read-only security review focused on user/source-instance isolation, date scope, identity namespaces, metadata sanitization, and historical preservation.

### Task 12: Resolve review findings and inspect diff

Verify reviewer findings against code and tests, apply only confirmed fixes, inspect the complete diff, and check for secrets, TODOs, debug output, and unrelated changes.

### Task 13: Create separate triage commit(s)

Commit confirmed A3 fixes separately from B1/B2. Do not amend or push.

### Task 14: Verify final branch state and report

Confirm branch, HEADs, Alembic head, clean status, test evidence, five-finding classification table, B2 impact, open live gates, and `READY FOR B3 IMPLEMENTATION` status.
