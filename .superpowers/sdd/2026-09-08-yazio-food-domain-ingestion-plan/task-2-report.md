# Task 2 report: A1 repository primitives

## Changed files

- `backend/app/nutrition/repositories.py`
  - Added caller-owned-flush `create_ingestion_run`.
  - Added user/source-instance scoped source-observation lookup and idempotent create helpers. The existing `create_source_observation` name now delegates to the idempotent implementation.
  - Added idempotent external identity lookup/create with safe last-seen/provider metadata refresh.
  - Added idempotent identity-link lookup/create behavior while preserving append-only revisions for changed targets.
  - Added user-scoped food-profile lookup/create, immutable content-hashed snapshot lookup/create, and safe current-snapshot assignment.
  - Added current consumption-event lookup and idempotent event create/revision/supersession helpers. Revisions are append-only and compare source observation identity/content hashes.
  - All new writes flush and do not commit. YAZIO source-instance ownership and composite user ownership are validated before writes.
- `backend/tests/test_nutrition_ingestion_repositories.py`
  - Added focused repository acceptance coverage for source identity retries, fingerprint identity, external namespace separation, profile/snapshot idempotence and immutable revisioning, event revision/supersession, cross-user rejection, rollback ownership, Decimal/civil-time/local-date, and metadata round trips.

## RED evidence

The requested repository command was run before implementation:

```text
python3 -m pytest tests/test_nutrition_ingestion_repositories.py -q
```

The system Python has no pytest (`/usr/bin/python3: No module named pytest`). With the prepared temporary dependency path, the command reached the repository's existing Python-runtime incompatibility before collecting tests:

```text
PYTHONPATH=/tmp:/tmp/calo-deps:. python3 -m pytest --confcutdir=/tmp tests/test_nutrition_ingestion_repositories.py -q
```

Result: import failed in pre-existing `app.models` (`NameError: name 'NutritionTarget' is not defined`, caused by the available Python 3.12 runtime evaluating the repository's Python 3.14-style forward annotations). This was environmental and occurred before Task 2 code execution.

## GREEN evidence

Using the same prepared dependencies and a temporary test-only shim for the unavailable Python 3.14 application runtime (the production files and test assertions are unchanged):

```text
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_nutrition_ingestion_repositories.py -q
```

Result: `6 passed in 0.12s`.

Additional touched-file checks:

```text
PYTHONPATH=/tmp/ruff-env python3 -m ruff check \
  app/nutrition/repositories.py tests/test_nutrition_ingestion_repositories.py
```

Result: `All checks passed!`

```text
python3 -m py_compile app/nutrition/repositories.py \
  tests/test_nutrition_ingestion_repositories.py
```

Result: success.

## Schema and migration decision

No migration was added. The approved A1 schema already provides the required composite user/source-instance identities, append-only event and identity-link revisions, immutable snapshot content hashes, current profile snapshot pointer, Decimal columns, civil datetime/local-date columns, state columns, and JSON metadata columns.

## Assumptions

- Repository get-or-create helpers return the persisted SQLAlchemy model; callers do not need a creation boolean because idempotence is observable by stable IDs and revision values.
- Source observation identity uses source record plus source revision when a record ID exists, and source fingerprint when it does not, matching the existing partial unique A1 indexes.
- Event content identity is represented by the source observation fingerprint and, when supplied, a bounded provider metadata `content_hash`; unchanged retries never append a revision.
- Repository metadata is treated as already sanitized by the provider/domain boundary; repository code does not log or emit credentials, tokens, headers, cookies, or raw provider responses.
- No live YAZIO calls, orchestration, migration, projections, source priority, analytics, API, or frontend work was performed.
## Review round 1 fixes

- Changed source-record observation retries to compare fingerprints. A changed
  payload appends the next source revision instead of reusing the prior row.
- Made event idempotence compare source fingerprints, explicit event content
  hashes, and supplied event fields; explicit `content_hash` is authoritative.
  Supersession targets are validated before an idempotent early return.
- Kept historical snapshots immutable and prevented retries of snapshot A from
  moving the profile pointer backward after snapshot B is current.
- Enforced provider/source-instance compatibility for identity-link targets and
  required YAZIO source-instance validation for external identity creation and
  current-event lookup.
- Added regression coverage for source revision 2, A-to-B snapshot pointer
  safety, namespace/provider separation, foreign source instances, and target
  compatibility.

Review-fix verification:

```text
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_nutrition_ingestion_repositories.py -q
```

Result: `6 passed in 0.15s`.

```text
PYTHONPATH=/tmp/ruff-env python3 -m ruff check \
  app/nutrition/repositories.py tests/test_nutrition_ingestion_repositories.py
```

Result: `All checks passed!`

```text
python3 -m py_compile app/nutrition/repositories.py \
  tests/test_nutrition_ingestion_repositories.py
```

Result: success.
## Review round 2 fix

- Restored `source_namespace` propagation through the preserved
  `create_source_observation` public entry point.
- Added a direct regression assertion that the public entry point persists the
  requested namespace.

Verification:

```text
PYTHONPATH=/tmp/repo-shim:/tmp/calo-deps:/home/wizard/Projects/CaloGraph/backend \
  python3 -m pytest /tmp/repo-shim/test_nutrition_ingestion_repositories.py -q
```

Result: `6 passed in 0.13s`.

```text
PYTHONPATH=/tmp/ruff-env python3 -m ruff check \
  app/nutrition/repositories.py tests/test_nutrition_ingestion_repositories.py
```

Result: `All checks passed!`
