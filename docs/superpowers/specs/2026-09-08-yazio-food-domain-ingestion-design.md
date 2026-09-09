# YAZIO Food Domain Ingestion Design

**Status:** approved for implementation
**Base:** `origin/main` at `f8a501638b92925a3bf513d2504e2570f5b4f1c5`
**Scope:** YAZIO SDK v22 Food Diary ingestion into the existing A1 Nutrition Domain

## Goal

Persist read-only YAZIO v22 Food Diary data as CaloGraph-owned Nutrition Domain
records while preserving the existing YAZIO Daily Aggregate/HealthSample path.
The feature is controlled by an internal disabled-by-default flag.

## Empirical SDK Boundary

The pinned `yazio-sdk==0.4.0` exposes these generated operations:

- `diary.list_consumed_items(date=...)`
- `diary.get_daily_nutrients(start=..., end=...)`
- `products.get_product(id=...)`

The generated SDK models expose typed product events and product profiles.
`ConsumedItems.simple_products` is generated as `list[Any]`; the adapter validates
those values as mappings and never exposes generated SDK values outside the SDK
adapter. Recipe portions remain unresolved unless their payload is proven
interpretable.

All SDK calls continue through the existing bounded HTTP transport, timeout,
response-size, authentication, rate-limit, and safe-error classification.

## Provider Contracts

`backend/app/services/yazio_provider.py` owns immutable contracts consumed by the
domain service. The contracts use `Decimal`, preserve missing values distinctly
from explicit zero, retain provider civil datetimes without an invented offset,
and carry only sanitized additional metadata.

The contract set covers:

- consumed product events
- consumed simple-product events
- product profiles
- product servings
- nutrient values
- daily nutrient summaries
- a food-diary range envelope

Generated SDK classes remain restricted to `yazio_sdk_provider.py`.

## Adapter Mapping

The SDK adapter maps:

- one consumed-item request per local date
- one daily-nutrient range request
- at most one product lookup per distinct Product ID per run

Product event amount is the consumed amount in the Product `base_unit`.
Product nutrients are interpreted per one base unit and are multiplied by the
consumption amount using Decimal arithmetic, with no division by 100 and no
serving-based primary scaling.

Simple-product nutrient values are absolute event values and are stored without
scaling. Profile servings and event serving metadata are retained separately.

Provider civil datetimes remain naive in `provider_civil_datetime`; `local_date`
is derived from the provider value; `provider_timezone` and canonical timestamps
remain unset unless the provider supplies a real timezone.

## Domain Persistence

A new internal ingestion service writes the existing A1 tables:

- `NutritionIngestionRun`
- `NutritionSourceObservation`
- `NutritionExternalIdentity`
- `NutritionExternalIdentityLink`
- `NutritionConsumptionEvent`
- `NutritionFoodProfile`
- `NutritionFoodSnapshot`
- `NutritionServingObservation`
- `NutritionFieldObservation`
- `NutritionProvenance`

No new Alembic migration is planned. Any evidence that A1 cannot represent a
required provider fact stops implementation for explicit review.

Namespaces are provider-scoped and never cross-merged:

- `yazio.consumed_item`
- `yazio.product`
- `yazio.ean`
- `yazio.daily_summary`

Consumed-item IDs identify events, Product IDs identify food profiles, and EANs
are additional profile identities only.

Product snapshots are immutable and content-hash based. Identical payloads reuse
the current snapshot; changed payloads create a new snapshot and move the
profile's current pointer. Existing snapshots are never updated.

Daily summaries become separate source observations and field observations. They
are not converted into events and are never added to reconstructed event values.

## Idempotence and Transactions

Source observations, event logical keys, external identities, identity links,
and snapshot content hashes are checked before insert. Identical retries reuse
existing records. Changed provider records create the next source/event/link or
snapshot revision as supported by A1.

The existing legacy import path remains unchanged when the flag is disabled,
including its commit, error, and result semantics. When enabled for SDK-v22,
all YAZIO remote reads—Daily Aggregates, Consumed Items, and every Product
lookup—complete before the shared database transaction opens. No network
request is permitted inside that transaction. The legacy import and new
domain write then execute through one shared transaction boundary. The
internal legacy import commit/checkpoint boundary is configurable only for
this orchestrated path; the default legacy behavior remains unchanged. A
domain failure rolls back both the legacy HealthSample write and the domain
write and is surfaced as a sync failure.

## Rollout and Security

`YAZIO_NUTRITION_DOMAIN_WRITE_ENABLED` defaults to `false`. It is an internal
configuration switch; no API or UI flag is added.

Only `YazioConnection.id` is used as `source_instance_id`. Credentials, tokens,
headers, cookies, raw HTTP responses, and generated SDK representations are not
persisted or logged. Metadata is bounded and sanitized.

## Verification

TDD covers provider mapping, malformed/missing values, explicit zero,
per-base-unit Decimal formulas, simple-product absolute values, civil time,
identity separation, snapshot revisions, daily-summary separation, retries,
user isolation, metadata safety, disabled-flag regression, SQLite behavior, and
disposable PostgreSQL constraints and repeated ingestion.

No projection computation, source-priority selection, analytics cutover,
frontend, Apple/Google Nutrition, AI, release, tag, deployment, or `latest`
promotion is included.
