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
