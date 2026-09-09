# Task 1 report: Provider contracts and SDK food reads

## Changed files

- `backend/app/services/yazio_provider.py`
  - Added immutable CaloGraph-owned Decimal contracts: `YazioNutrientValues`, `YazioServing`, `YazioConsumedProduct`, `YazioConsumedSimpleProduct`, `YazioProductProfile`, `YazioDailyNutrientSummary`, and `YazioFoodDiary`.
  - Added the SDK-v22-only `YazioFoodDiaryProvider` protocol with `fetch_food_diary(email, password, start_day, end_day) -> YazioFoodDiary`.
  - Contracts use frozen/slots dataclasses, tuples for collections, and immutable sanitized metadata mappings. Optional Decimal fields retain missing values as `None`, including explicit zero as `Decimal("0")`.
  - Legacy `YazioProvider` and `LegacyYazioProvider.fetch()` behavior was not changed.
- `backend/app/services/yazio_sdk_provider.py`
  - Restricted generated SDK imports to this module and added the pinned `diary.list_consumed_items`, `diary.get_daily_nutrients`, and `products.get_product` operations.
  - Added per-day consumed-item reads, one daily nutrient range read, and one Product read per distinct Product ID in a run.
  - Added typed Product event mapping, explicit Mapping validation for `simple_products`, Product profile/serving/EAN/flag mapping, daily summaries, Decimal conversion, and naive provider civil datetime handling. Provider offsets are retained only as a provider timezone string; no UTC is invented.
  - Metadata is bounded to scalar values and filters sensitive key terms; credentials, tokens, headers, cookies, raw responses, and generated SDK representations do not cross the adapter boundary.
  - Existing bounded HTTP transport, timeout, status classification, auth, rate-limit, and safe error wrappers are reused.
- `backend/tests/test_yazio_food_provider.py`
  - Added focused TDD coverage for immutable contracts, typed and untyped events, optional/missing values, explicit zero, malformed numeric and shape rejection, civil datetimes, Product profiles, multiple EANs/servings, missing servings, deleted/private/verified flags, metadata filtering, request counts, Product-ID caching, and daily summaries.

## RED evidence

The new test module was run before production implementation. Collection failed with:

`ImportError: cannot import name 'YazioConsumedProduct' from 'app.services.yazio_provider'`

This was a missing provider contract failure, not a test typo.

## GREEN evidence

Using the available Python 3.12 runtime with temporary test dependencies and an isolated conftest path (the repository's pinned environment targets Python 3.14):

`ENVIRONMENT=development PYTHONPATH=/tmp:/tmp/calo-deps:. python3 -m pytest --confcutdir=/tmp /tmp/test_yazio_food_provider.py /tmp/test_yazio_provider.py -q`

Result: `12 passed in 0.27s`.

A generated-model smoke check using `ConsumedItems.from_dict`, `Product.from_dict`, and `DailyNutrients.from_dict` also passed (`generated model mapping ok`).

The literal repository command was attempted, but this workstation's available Python 3.12 runtime cannot load the repository's Python 3.14-only configuration/model annotations; it fails during `tests/conftest.py` import before tests run.

## Concerns

- No live YAZIO calls were made, as required. The repository `.venv` launcher points at `/app/.venv/bin/python`, which is unavailable in this workstation, so focused proof used temporary dependency installation and isolated test execution.
- The adapter intentionally ignores unresolved recipe portions because the approved boundary does not provide an interpretable recipe contract.

## Review fix round 1

- Preserved simple-product `name` in `YazioConsumedSimpleProduct` and mapped it from the untyped mapping.
- Added explicit structural nutrient exclusions so `amount`, `serving_quantity`, `energy_goal`, IDs, dates, serving labels, and other event fields cannot become nutrient `additional` values; unknown numeric nutrient keys remain available.
- Validated every daily summary date against the requested range and rejected duplicate dates.
- Made both provider protocols expose read-only `mode` properties. `YazioSdkProvider.mode` is `Literal["sdk"]`, `LegacyYazioProvider.mode` is `Literal["legacy"]`, and `get_yazio_food_diary_provider()` assigns the SDK implementation to the protocol type.
- Added adapter-local generated model fixture helpers and changed tests to exercise generated `ConsumedItems`, `Product`, and `DailyNutrients` instances, including simple-product name, flags, missing servings, explicit zero, Product base unit, and offset-bearing naive civil time.
- Fixed Ruff import/spacing issues.

Covering tests:

- `test_sdk_maps_typed_product_simple_product_profiles_and_daily_summary`
- `test_food_reads_one_day_each_and_cache_distinct_product_ids`
- `test_food_rejects_daily_summary_outside_range_and_duplicates`
- `test_food_contracts_are_immutable_and_preserve_zero`

Review-round verification:

- Focused tests: `ENVIRONMENT=development PYTHONPATH=/tmp:/tmp/calo-deps:. python3 -m pytest --confcutdir=/tmp /tmp/test_yazio_food_provider.py /tmp/test_yazio_provider.py -q` -> `14 passed in 0.26s`.
- Ruff: `PYTHONPATH=/tmp/ruff-env python3 -m ruff check app/services/yazio_provider.py app/services/yazio_sdk_provider.py tests/test_yazio_food_provider.py` -> `All checks passed!`.
- Focused mypy smoke (Python 3.12 fallback, with missing-runtime `httpx` diagnostics disabled): `PYTHONPATH=/tmp/mypy-env:/tmp/calo-deps:. ENVIRONMENT=development python3 -m mypy --strict --follow-imports=skip --ignore-missing-imports --disable-error-code unused-ignore --disable-error-code misc --disable-error-code no-any-return app/services/yazio_provider.py app/services/yazio_sdk_provider.py` -> `Success: no issues found in 2 source files`.

## Review fix round 2

- Added the required two blank lines before `YazioFoodDiaryProvider`, before
  `get_yazio_food_diary_provider`, after adapter-local generated model helpers
  and before `MAX_PROVIDER_RESPONSE_BYTES`, and before the test following the
  parametrized daily-summary test.
- Verification: `PYTHONPATH=/tmp/ruff-env python3 -m ruff check app/services/yazio_provider.py app/services/yazio_sdk_provider.py tests/test_yazio_food_provider.py` -> `All checks passed!`.
- Focused provider tests -> `14 passed in 0.36s`.
