"""CaloGraph-owned YAZIO provider boundary.

The rest of the application only consumes :class:`YazioProviderResult` and
never imports generated SDK models.  Provider failures deliberately expose
only stable, non-sensitive messages.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast

_MetadataValue = str | int | float | bool | None


def _immutable_metadata(value: Mapping[str, _MetadataValue]) -> Mapping[str, _MetadataValue]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class YazioNutrientValues:
    """Normalized nutrient values; ``None`` means the provider omitted a value."""

    energy: Decimal | None = None
    protein: Decimal | None = None
    carb: Decimal | None = None
    fat: Decimal | None = None
    fiber: Decimal | None = None
    sugar: Decimal | None = None
    saturated_fat: Decimal | None = None
    salt: Decimal | None = None
    additional: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "additional", MappingProxyType(dict(self.additional)))


@dataclass(frozen=True, slots=True)
class YazioServing:
    label: str | None = None
    amount: Decimal | None = None
    unit: str | None = None
    metadata: Mapping[str, _MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class YazioConsumedProduct:
    consumed_item_id: str
    product_id: str
    amount: Decimal | None
    provider_civil_datetime: datetime | None
    local_date: date
    daytime: str | None
    serving: str | None
    serving_quantity: Decimal | None
    provider_timezone: str | None = None
    metadata: Mapping[str, _MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class YazioConsumedSimpleProduct:
    consumed_item_id: str
    amount: Decimal | None
    provider_civil_datetime: datetime | None
    local_date: date
    daytime: str | None
    nutrients: YazioNutrientValues
    serving: str | None
    serving_quantity: Decimal | None
    provider_timezone: str | None = None
    name: str | None = None
    metadata: Mapping[str, _MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class YazioProductProfile:
    product_id: str
    name: str | None
    producer: str | None
    category: str | None
    base_unit: str | None
    nutrients: YazioNutrientValues
    servings: tuple[YazioServing, ...]
    eans: tuple[str, ...]
    language: str | None
    countries: tuple[str, ...]
    updated_at: datetime | None
    is_verified: bool | None
    is_private: bool | None
    is_deleted: bool | None
    metadata: Mapping[str, _MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class YazioDailyNutrientSummary:
    local_date: date
    nutrients: YazioNutrientValues
    energy_goal: Decimal | None
    metadata: Mapping[str, _MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class YazioFoodDiary:
    requested_start_day: date
    requested_end_day: date
    consumed_products: tuple[YazioConsumedProduct, ...]
    consumed_simple_products: tuple[YazioConsumedSimpleProduct, ...]
    product_profiles: tuple[YazioProductProfile, ...]
    daily_summaries: tuple[YazioDailyNutrientSummary, ...]


class YazioFoodDiaryProvider(Protocol):
    """SDK-v22 food diary reads returning only owned normalized DTOs."""

    def fetch_food_diary(
        self, email: str, password: str, start_day: date, end_day: date
    ) -> YazioFoodDiary:
        """Fetch and normalize consumed items, profiles, and daily summaries."""



ProviderOperation = Literal[
    "oauth_token",
    "daily_nutrients",
    "daily_summary",
    "latest_weight",
    "consumed_items",
    "product_lookup",
    "simple_product_normalization",
    "domain_worker",
]
ProviderEndpointKey = Literal[
    "oauth_token",
    "daily_nutrients",
    "daily_summary",
    "latest_weight",
    "consumed_items",
    "product",
    "domain_worker",
]
ProviderErrorCategory = Literal[
    "http",
    "validation",
    "response_validation",
    "transport",
    "timeout",
    "authentication",
    "rate_limit",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class YazioProviderErrorContext:
    operation: ProviderOperation
    endpoint_key: ProviderEndpointKey
    upstream_status_code: int | None = None
    response_model: str | None = None
    error_category: ProviderErrorCategory = "validation"
    validation_location: str | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "endpoint_key": self.endpoint_key,
            "upstream_status_code": self.upstream_status_code,
            "response_model": self.response_model,
            "error_category": self.error_category,
            "validation_location": self.validation_location,
            "retryable": self.retryable,
        }

    @classmethod
    def from_mapping(cls, value: object) -> YazioProviderErrorContext | None:
        if not isinstance(value, Mapping):
            return None
        operations = {
            "oauth_token", "daily_nutrients", "daily_summary", "latest_weight",
            "consumed_items", "product_lookup", "simple_product_normalization", "domain_worker",
        }
        endpoints = {
            "oauth_token", "daily_nutrients", "daily_summary", "latest_weight",
            "consumed_items", "product", "domain_worker",
        }
        categories = {
            "http", "validation", "response_validation", "transport", "timeout",
            "authentication", "rate_limit", "unavailable",
        }
        operation = value.get("operation")
        endpoint_key = value.get("endpoint_key")
        category = value.get("error_category")
        status = value.get("upstream_status_code")
        response_model = value.get("response_model")
        location = value.get("validation_location")
        retryable = value.get("retryable")
        if (
            operation not in operations
            or endpoint_key not in endpoints
            or category not in categories
            or (status is not None and (isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599))
            or (response_model is not None and (not isinstance(response_model, str) or len(response_model) > 128))
            or (location is not None and (not isinstance(location, str) or len(location) > 128))
            or not isinstance(retryable, bool)
        ):
            return None
        return cls(
            operation=cast(ProviderOperation, operation),
            endpoint_key=cast(ProviderEndpointKey, endpoint_key),
            upstream_status_code=status,
            response_model=response_model,
            error_category=cast(ProviderErrorCategory, category),
            validation_location=location,
            retryable=retryable,
        )


class YazioProviderError(RuntimeError):
    """Base class for safe, typed provider failures."""

    kind = "provider"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int | None = None,
        context: YazioProviderErrorContext | None = None,
    ) -> None:
        super().__init__(message or "YAZIO provider request failed")
        self.retry_after = retry_after
        self.context = context


class YazioProviderAuthenticationError(YazioProviderError):
    kind = "authentication"

    def __init__(self, *, context: YazioProviderErrorContext | None = None) -> None:
        super().__init__("YAZIO authentication failed", context=context)


class YazioProviderVersionBlockedError(YazioProviderError):
    kind = "version_blocked"

    def __init__(self, *, context: YazioProviderErrorContext | None = None) -> None:
        super().__init__("YAZIO API client version is blocked", context=context)


class YazioProviderRateLimitedError(YazioProviderError):
    kind = "rate_limited"

    def __init__(
        self,
        retry_after: int | None = None,
        *,
        context: YazioProviderErrorContext | None = None,
    ) -> None:
        super().__init__(
            "YAZIO provider rate limit exceeded",
            retry_after=retry_after,
            context=context,
        )


class YazioProviderUnavailableError(YazioProviderError):
    kind = "unavailable"

    def __init__(self, *, context: YazioProviderErrorContext | None = None) -> None:
        super().__init__("YAZIO provider is temporarily unavailable", context=context)


class YazioProviderNetworkTimeoutError(YazioProviderError):
    kind = "network_timeout"

    def __init__(self, *, context: YazioProviderErrorContext | None = None) -> None:
        super().__init__("YAZIO provider request timed out", context=context)


class YazioProviderDeadlineError(YazioProviderError):
    kind = "deadline"

    def __init__(self, *, context: YazioProviderErrorContext | None = None) -> None:
        super().__init__("YAZIO provider operation exceeded its deadline", context=context)


class YazioProviderInvalidResponseError(YazioProviderError):
    kind = "invalid_response"

    def __init__(self, *, context: YazioProviderErrorContext) -> None:
        super().__init__("YAZIO provider returned an invalid response", context=context)

@dataclass(frozen=True, slots=True)
class YazioProviderMetadata:
    """Metadata needed by synchronization without leaking provider models."""

    micronutrient_complete: bool


@dataclass(frozen=True, slots=True)
class YazioProviderResult:
    """Normalized provider payload and its synchronization metadata."""

    payload: dict[str, Any]
    metadata: YazioProviderMetadata


class YazioProvider(Protocol):
    """Provider operations executed inside the isolated transport worker."""

    def validate_credentials(self, email: str, password: str) -> None:
        """Validate credentials without returning or persisting provider tokens."""

    def fetch(
        self,
        email: str,
        password: str,
        start_day: date,
        end_day: date,
    ) -> YazioProviderResult:
        """Fetch a CaloGraph-owned normalized envelope."""
