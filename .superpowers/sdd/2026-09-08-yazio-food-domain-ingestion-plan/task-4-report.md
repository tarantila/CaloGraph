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
  - Added rollout flag, read-order, SDK-only, atomic dual-write, rollback, and disabled-provider regression coverage with provider fakes.

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

The enabled test uses injected aggregate and food-diary providers whose markers are emitted before the `_persist_import_locked` shared-transaction marker. The enabled implementation calls the SDK aggregate provider and SDK food-diary provider completely before opening the persistence Session. `_persist_import_locked(..., shared_transaction=True)` passes `commit=False` to batch/checkpoint helpers and flushes only; the orchestration performs the single final `db.commit()` after domain ingestion. The exception path calls `db.rollback()` before the failure is classified by the established sync wrapper. The default `persist_import` call does not pass the option and retains existing commit/checkpoint behavior.
