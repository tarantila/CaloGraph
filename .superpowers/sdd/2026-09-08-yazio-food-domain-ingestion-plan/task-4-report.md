# Task 4 report: atomic SDK rollout and legacy regression

## Changed files

- `backend/app/config.py`
  - Added `settings.yazio_nutrition_domain_write_enabled`, defaulting to `False` and sourced from `YAZIO_NUTRITION_DOMAIN_WRITE_ENABLED`.
- `backend/app/services/import_service.py`
  - Added explicit `shared_transaction` mode to `_persist_import_locked`; shared callers flush/checkpoint without committing while default `persist_import` behavior remains unchanged.
- `backend/app/services/yazio_sync.py`
  - Added SDK-only enabled orchestration that completes aggregate and food-diary provider reads before shared persistence, then persists legacy and nutrition-domain contracts in one Session/commit with rollback on domain failure.
  - Disabled mode retains the existing legacy branch.
- `backend/tests/test_yazio_domain_sync.py`
  - Added rollout flag, read-order, SDK-only, atomic dual-write, rollback, and disabled-provider regression coverage with a bounded transport fake.

## TDD evidence

RED was attempted before production implementation:

```text
cd backend && python -m pytest tests/test_yazio_domain_sync.py -q
```

Result: the command could not start because `python` is not installed in the shell. The project virtualenv launcher is a broken symlink to `/usr/local/bin/python3`; available system Python 3.12 has no pytest installed (`python3 -m pytest ...` -> `No module named pytest`). Therefore no test assertion result can be claimed from this environment.

A syntax smoke check was run after implementation:

```text
cd backend && python3 -m py_compile app/config.py app/services/import_service.py app/services/yazio_sync.py tests/test_yazio_domain_sync.py
```

Result: passed with no output.

Focused pytest and Ruff remain pending a working project runtime. No live YAZIO calls were made.

## Transaction evidence (static/fake-marker coverage)

The enabled test uses an injected bounded-transport fake whose markers are emitted before the `_persist_import_locked` shared-transaction marker. The production implementation runs aggregate and food-diary SDK reads in the subprocess worker before opening the persistence Session. `_persist_import_locked(..., shared_transaction=True)` suppresses transaction-bound start/completed events, passes `commit=False` to batch/checkpoint helpers, and flushes only; the orchestration performs the single final `db.commit()` after domain ingestion and emits safe import events afterward. The exception path calls `db.rollback()` before the failure is classified by the established sync wrapper. The default `persist_import` call does not pass the option and retains existing commit/checkpoint behavior.

## Round 1 review fixes

- Enabled SDK reads now run through `fetch_yazio_domain_transport`, a bounded subprocess worker using `settings.yazio_operation_deadline_seconds`; worker output is reduced to a normalized aggregate payload and `YazioFoodDiary`, with provider-safe mapping for timeout/deadline and other failures.
- Enabled orchestration calls `_require_yazio_enabled()` and `_ensure_yazio_circuit_closed()` before provider reads, preserves failure recording/clearing, and rejects fully failed import summaries before domain ingestion.
- Shared import mode suppresses `import.started` and `import.completed` until the outer transaction commits successfully; default legacy import behavior is unchanged.
- Added the required `energy_goal=None` to the diary fixture.

Round 1 runtime checks remain blocked by the same environment limitation above: the available system interpreter has neither the project dependencies nor pytest. The changed Python modules were syntax-checked with `python3 -m py_compile`.

## Round 2 review fixes

- Restored the legacy bounded transport imports (`fetch_yazio_payload_transport`, `threading`, and micronutrient IDs) and updated the disabled-rollout regression to patch the current domain transport boundary.
- Kept malformed aggregate/diary worker responses inside the transport boundary as `YazioTransportInvalidResponseError`, while preserving typed provider failures for safe sync mapping.
- `python3 -m py_compile` passed for all changed modules. Focused pytest, existing YAZIO sync tests, and Ruff remain unavailable because the environment lacks project dependencies, pytest, and the Ruff executable.

## Round 3 review fixes

- Restored `collections.abc.Mapping` alongside the bounded worker's `threading` dependency.
- Moved domain result-shape validation and diary decoding into the same transport error boundary so malformed worker output is mapped to `YazioProviderInvalidResponseError` at the provider boundary and reaches sync circuit-failure handling safely.
- Added a focused malformed-worker-result regression test.
- `python3 -m py_compile` passed. Focused pytest, existing YAZIO sync tests, and Ruff remain unavailable in this environment.
