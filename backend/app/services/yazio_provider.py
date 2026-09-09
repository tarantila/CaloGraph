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

ProviderMode = Literal["legacy", "sdk"]
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
    """SDK-v22-only food diary reads; legacy providers need not implement this."""

    @property
    def mode(self) -> Literal["sdk"]:
        ...

    def fetch_food_diary(
        self, email: str, password: str, start_day: date, end_day: date
    ) -> YazioFoodDiary:
        """Fetch and normalize consumed items, profiles, and daily summaries."""



class YazioProviderError(RuntimeError):
    """Base class for safe, typed provider failures."""

    kind = "provider"

    def __init__(self, message: str | None = None, *, retry_after: int | None = None) -> None:
        super().__init__(message or "YAZIO provider request failed")
        self.retry_after = retry_after


class YazioProviderAuthenticationError(YazioProviderError):
    kind = "authentication"

    def __init__(self) -> None:
        super().__init__("YAZIO authentication failed")


class YazioProviderVersionBlockedError(YazioProviderError):
    kind = "version_blocked"

    def __init__(self) -> None:
        super().__init__("YAZIO API client version is blocked")


class YazioProviderRateLimitedError(YazioProviderError):
    kind = "rate_limited"

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("YAZIO provider rate limit exceeded", retry_after=retry_after)


class YazioProviderUnavailableError(YazioProviderError):
    kind = "unavailable"

    def __init__(self) -> None:
        super().__init__("YAZIO provider is temporarily unavailable")


class YazioProviderNetworkTimeoutError(YazioProviderError):
    kind = "network_timeout"

    def __init__(self) -> None:
        super().__init__("YAZIO provider request timed out")


class YazioProviderDeadlineError(YazioProviderError):
    kind = "deadline"

    def __init__(self) -> None:
        super().__init__("YAZIO provider operation exceeded its deadline")


class YazioProviderInvalidResponseError(YazioProviderError):
    kind = "invalid_response"

    def __init__(self) -> None:
        super().__init__("YAZIO provider returned an invalid response")


@dataclass(frozen=True, slots=True)
class YazioProviderMetadata:
    """Metadata needed by synchronization without leaking provider models."""

    micronutrient_complete: bool
    provider_mode: ProviderMode


@dataclass(frozen=True, slots=True)
class YazioProviderResult:
    """Normalized provider payload and its synchronization metadata."""

    payload: dict[str, Any]
    metadata: YazioProviderMetadata


class YazioProvider(Protocol):
    """Provider operations executed inside the isolated transport worker."""

    @property
    def mode(self) -> ProviderMode:
        ...
    def validate_credentials(self, email: str, password: str) -> None:
        """Validate credentials without returning or persisting provider tokens."""

    def fetch(
        self,
        email: str,
        password: str,
        start_day: date,
        end_day: date,
        include_micronutrients: bool,
    ) -> YazioProviderResult:
        """Fetch a CaloGraph-owned normalized envelope."""


def provider_mode_from_settings() -> ProviderMode:
    """Read and validate the deployment's internal provider mode."""

    from app.config import settings
    mode = cast(ProviderMode, settings.yazio_provider)
    if mode not in {"legacy", "sdk"}:
        # Settings validation normally makes this unreachable; keeping this
        # boundary fail-closed is useful for tests and direct callers.
        raise ValueError("YAZIO provider mode is invalid")
    return mode


def get_yazio_provider(mode: ProviderMode | None = None) -> YazioProvider:
    """Construct the configured provider, importing SDK code lazily.

    Lazy import keeps generated SDK modules out of the legacy parser and all
    non-worker application paths.
    """

    selected = mode or provider_mode_from_settings()
    if selected == "sdk":
        from app.services.yazio_sdk_provider import YazioSdkProvider

        return YazioSdkProvider()
    if selected == "legacy":
        return LegacyYazioProvider()
    raise ValueError("YAZIO provider mode is invalid")

def get_yazio_food_diary_provider() -> YazioFoodDiaryProvider:
    """Construct the SDK-v22 food diary provider without importing it in legacy mode."""

    from app.services.yazio_sdk_provider import YazioSdkProvider

    provider: YazioFoodDiaryProvider = YazioSdkProvider()
    return provider


class LegacyYazioProvider:
    """Adapter preserving the existing yazio-exporter transport behavior."""
    mode: Literal["legacy"] = "legacy"

    def validate_credentials(self, email: str, password: str) -> None:
        from app.services.yazio_transport import validate_yazio_credentials_transport

        validate_yazio_credentials_transport(email, password, provider_mode="legacy")

    def fetch(
        self,
        email: str,
        password: str,
        start_day: date,
        end_day: date,
        include_micronutrients: bool,
    ) -> YazioProviderResult:
        from app.services.yazio_transport import fetch_yazio_payload_transport

        payload = fetch_yazio_payload_transport(
            email,
            password,
            start_day,
            end_day,
            include_micronutrients,
            provider_mode="legacy",
        )
        return YazioProviderResult(
            payload=payload,
            metadata=YazioProviderMetadata(
                micronutrient_complete=include_micronutrients,
                provider_mode="legacy",
            ),
        )


def provider_metadata(mode: ProviderMode, *, include_micronutrients: bool) -> YazioProviderMetadata:
    """Return the metadata contract for a provider result."""

    return YazioProviderMetadata(
        micronutrient_complete=mode == "legacy" and include_micronutrients,
        provider_mode=mode,
    )
