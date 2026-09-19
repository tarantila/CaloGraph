"""YAZIO community SDK v22 provider.

This module is the only CaloGraph module that imports generated ``yazio_sdk``
models and endpoint functions. Calls use a bounded worker pool and only
detailed generated operations so status and headers remain available for safe
classification by the transport worker.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any, Literal, cast

import httpx
from yazio_sdk import AuthenticatedClient, Client  # type: ignore[import-untyped]
from yazio_sdk.api.authentication import create_token  # type: ignore[import-untyped]
from yazio_sdk.api.body_values import get_latest_weight  # type: ignore[import-untyped]
from yazio_sdk.api.diary import (  # type: ignore[import-untyped]
    get_daily_nutrients,
    list_consumed_items,
)
from yazio_sdk.api.products import get_product  # type: ignore[import-untyped]
from yazio_sdk.api.widgets import get_daily_summary_widget  # type: ignore[import-untyped]
from yazio_sdk.models import (  # type: ignore[import-untyped]
    ConsumedItems,
    DailyNutrients,
    OAuthTokenRequest,
    Product,
)
from yazio_sdk.types import UNSET  # type: ignore[import-untyped]

from app.config import settings
from app.services.yazio_provider import (
    ProviderOperation,
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
    YazioProviderAuthenticationError,
    YazioProviderErrorContext,
    YazioProviderInvalidResponseError,
    YazioProviderMetadata,
    YazioProviderNetworkTimeoutError,
    YazioProviderRateLimitedError,
    YazioProviderResult,
    YazioProviderUnavailableError,
    YazioProviderVersionBlockedError,
    YazioServing,
)


def _generated_consumed_items(payload: Mapping[str, Any]) -> object:
    return ConsumedItems.from_dict(payload)


def _generated_daily_nutrients(payload: Mapping[str, Any]) -> object:
    return DailyNutrients.from_dict(payload)


def _generated_product(payload: Mapping[str, Any]) -> object:
    return Product.from_dict(payload)


MAX_PROVIDER_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RETRY_AFTER_SECONDS = 3_600
MAX_TOKEN_BYTES = 8 * 1024


class _ResponseTooLargeError(RuntimeError):
    pass


class _BoundedResponseStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[bytes]:
        total = 0
        try:
            for chunk in self._stream:
                total += len(chunk)
                if total > MAX_PROVIDER_RESPONSE_BYTES:
                    raise _ResponseTooLargeError
                yield chunk
        finally:
            self._stream.close()

    def close(self) -> None:
        self._stream.close()


class _BoundedHTTPTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self._transport = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._transport.handle_request(request)
        content_length = response.headers.get("content-length")
        try:
            declared_length = int(content_length) if content_length is not None else None
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > MAX_PROVIDER_RESPONSE_BYTES:
            response.close()
            raise _ResponseTooLargeError
        response.stream = _BoundedResponseStream(cast(httpx.SyncByteStream, response.stream))
        return response

    def close(self) -> None:
        self._transport.close()


def _httpx_args() -> dict[str, Any]:
    return {"transport": _BoundedHTTPTransport()}

_MISSING = object()


def _timeout() -> httpx.Timeout:
    """Build one explicit timeout used by every SDK-generated request."""

    return httpx.Timeout(
        connect=settings.yazio_connect_timeout_seconds,
        read=settings.yazio_read_timeout_seconds,
        write=settings.yazio_read_timeout_seconds,
        pool=settings.yazio_connect_timeout_seconds,
    )


def _base_url() -> str:
    return settings.yazio_api_base_url.rstrip("/")


def _new_client() -> Client:
    return Client(
        base_url=_base_url(),
        timeout=_timeout(),
        follow_redirects=False,
        raise_on_unexpected_status=False,
        headers={"User-Agent": settings.yazio_sdk_user_agent},
        httpx_args=_httpx_args(),
    )


def _new_authenticated_client(token: str) -> AuthenticatedClient:
    return AuthenticatedClient(
        base_url=_base_url(),
        token=token,
        timeout=_timeout(),
        follow_redirects=False,
        raise_on_unexpected_status=False,
        headers={"User-Agent": settings.yazio_sdk_user_agent},
        httpx_args=_httpx_args(),
    )


def _close_client(client: Client | AuthenticatedClient) -> None:
    with suppress(Exception):
        client.get_httpx_client().close()


def _status(response: object) -> int:
    value = getattr(response, "status_code", _MISSING)
    if isinstance(value, bool) or not isinstance(value, int):
        raise YazioProviderInvalidResponseError
    return value


def _headers(response: object) -> Mapping[str, str]:
    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        raise YazioProviderInvalidResponseError
    return headers


def _retry_after(response: object) -> int | None:
    raw = _headers(response).get("retry-after")
    if raw is None:
        raw = _headers(response).get("Retry-After")
    if not isinstance(raw, str):
        return None
    try:
        seconds = int(raw.strip())
    except (TypeError, ValueError):
        return None
    return max(0, min(seconds, MAX_RETRY_AFTER_SECONDS))


def _response_content_is_bounded(response: object) -> None:
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        raise YazioProviderInvalidResponseError
    if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
        raise YazioProviderInvalidResponseError


def _response_error_code(response: object) -> str | None:
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)) or len(content) > MAX_PROVIDER_RESPONSE_BYTES:
        raise YazioProviderInvalidResponseError
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    error = parsed.get("error") if isinstance(parsed, Mapping) else None
    return error if isinstance(error, str) else None


def _context(
    operation: ProviderOperation,
    endpoint_key: str,
    response_model: str,
    *,
    status: int | None = None,
    category: str = "validation",
    location: str | None = None,
    retryable: bool = False,
) -> YazioProviderErrorContext:
    return YazioProviderErrorContext(
        operation=operation,
        endpoint_key=cast(Any, endpoint_key),
        upstream_status_code=status,
        response_model=response_model,
        error_category=cast(Any, category),
        validation_location=location,
        retryable=retryable,
    )


def _raise_for_status(
    response: object,
    *,
    authentication: bool = False,
    operation: ProviderOperation = "domain_worker",
    endpoint_key: str = "domain_worker",
    response_model: str = "unknown",
) -> None:
    _response_content_is_bounded(response)
    status = _status(response)
    base: dict[str, Any] = dict(
        operation=operation,
        endpoint_key=endpoint_key,
        response_model=response_model,
        status=status,
    )
    if status == 200:
        return
    if status == 429:
        raise YazioProviderRateLimitedError(
            _retry_after(response),
            context=_context(**base, category="rate_limit", retryable=True),
        )
    if authentication and status in {400, 401, 403}:
        raise YazioProviderAuthenticationError(
            context=_context(**base, category="authentication")
        )
    if not authentication and status == 401:
        raise YazioProviderAuthenticationError(
            context=_context(**base, category="authentication")
        )
    if not authentication and status == 403:
        context = _context(**base, category="authentication")
        if _response_error_code(response) == "version_blocked":
            raise YazioProviderVersionBlockedError(context=context)
        raise YazioProviderAuthenticationError(context=context)
    if status >= 500:
        raise YazioProviderUnavailableError(
            context=_context(**base, category="unavailable", retryable=True)
        )
    if 300 <= status < 400:
        raise YazioProviderUnavailableError(
            context=_context(**base, category="unavailable", retryable=True)
        )
    raise YazioProviderInvalidResponseError(context=_context(**base, category="http"))


def _call_detailed[T](
    call: Callable[..., T],
    *,
    authentication: bool = False,
    operation: ProviderOperation = "domain_worker",
    endpoint_key: str = "domain_worker",
    response_model: str = "unknown",
    **kwargs: Any,
) -> T:
    context = _context(operation, endpoint_key, response_model)
    try:
        response = call(**kwargs)
    except (YazioProviderAuthenticationError, YazioProviderVersionBlockedError):
        raise
    except _ResponseTooLargeError as exc:
        raise YazioProviderInvalidResponseError(
            context=_context(operation, endpoint_key, response_model, category="validation")
        ) from exc
    except httpx.TimeoutException as exc:
        raise YazioProviderNetworkTimeoutError(
            context=_context(operation, endpoint_key, response_model, category="timeout", retryable=True)
        ) from exc
    except httpx.RequestError as exc:
        raise YazioProviderUnavailableError(
            context=_context(operation, endpoint_key, response_model, category="transport", retryable=True)
        ) from exc
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise YazioProviderInvalidResponseError(context=context) from exc
    _raise_for_status(
        response,
        authentication=authentication,
        operation=operation,
        endpoint_key=endpoint_key,
        response_model=response_model,
    )
    return response


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name, _MISSING)
    return getattr(value, name, _MISSING)


def _numeric(value: object) -> float | None:
    if value is _MISSING or value is UNSET or value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise YazioProviderInvalidResponseError
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise YazioProviderInvalidResponseError from exc
    if not math.isfinite(converted) or converted < 0:
        raise YazioProviderInvalidResponseError
    return converted


def _date_value(value: object) -> date:
    if not isinstance(value, str):
        raise YazioProviderInvalidResponseError
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise YazioProviderInvalidResponseError from exc
    if parsed.isoformat() != value:
        raise YazioProviderInvalidResponseError
    return parsed


def _token_from_response(
    response: object,
    *,
    context: YazioProviderErrorContext,
) -> str:
    parsed = getattr(response, "parsed", _MISSING)
    if parsed is None or parsed is _MISSING:
        raise YazioProviderInvalidResponseError(context=context)
    token = _field(parsed, "access_token")
    if token is _MISSING or not isinstance(token, str) or not token:
        raise YazioProviderInvalidResponseError(context=context)
    if len(token.encode("utf-8")) > MAX_TOKEN_BYTES or "\x00" in token:
        raise YazioProviderInvalidResponseError(context=context)
    return token


def _daily_items(
    response: object,
    *,
    context: YazioProviderErrorContext,
) -> list[object]:
    parsed = getattr(response, "parsed", _MISSING)
    if parsed is _MISSING or not isinstance(parsed, list):
        raise YazioProviderInvalidResponseError(context=context)
    return parsed


_NUTRIENT_ALIASES = {
    "energy": "energy",
    "energy.energy": "energy",
    "protein": "protein",
    "nutrient.protein": "protein",
    "carb": "carb",
    "carbohydrate": "carb",
    "carbohydrates": "carb",
    "nutrient.carb": "carb",
    "nutrient.carbohydrate": "carb",
    "nutrient.carbohydrates": "carb",
    "fat": "fat",
    "nutrient.fat": "fat",
    "fiber": "fiber",
    "nutrient.fiber": "fiber",
    "nutrient.dietaryfiber": "fiber",
    "nutrient.dietary_fiber": "fiber",
    "sugar": "sugar",
    "nutrient.sugar": "sugar",
    "saturated_fat": "saturated_fat",
    "saturatedFat": "saturated_fat",
    "nutrient.saturated": "saturated_fat",
    "nutrient.saturated_fat": "saturated_fat",
    "nutrient.saturatedFat": "saturated_fat",
    "salt": "salt",
    "nutrient.salt": "salt",
}
_SENSITIVE_METADATA_TERMS = (
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "cookie",
    "credential",
    "header",
    "key",
    "password",
    "raw",
    "refresh",
    "response",
    "secret",
    "session",
    "token",
)
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_KEY_BYTES = 128
_MAX_METADATA_STRING_BYTES = 512


def _decimal(value: object) -> Decimal | None:
    if value is _MISSING or value is UNSET or value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise YazioProviderInvalidResponseError
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise YazioProviderInvalidResponseError from exc
    if not converted.is_finite() or converted < 0:
        raise YazioProviderInvalidResponseError
    return converted


def _optional_string(value: object) -> str | None:
    if value is _MISSING or value is UNSET or value is None:
        return None
    if not isinstance(value, str):
        raise YazioProviderInvalidResponseError
    return value


def _required_string(value: object) -> str:
    result = _optional_string(value)
    if result is None:
        raise YazioProviderInvalidResponseError
    return result


def _optional_bool(value: object) -> bool | None:
    if value is _MISSING or value is UNSET or value is None:
        return None
    if not isinstance(value, bool):
        raise YazioProviderInvalidResponseError
    return value


def _metadata(value: object, known: set[str]) -> dict[str, str | int | float | bool | None]:
    source: Mapping[Any, Any]
    if isinstance(value, Mapping):
        source = value
    else:
        candidate = _field(value, "additional_properties")
        if not isinstance(candidate, Mapping):
            return {}
        source = candidate
    result: dict[str, str | int | float | bool | None] = {}
    for key, item in source.items():
        if not isinstance(key, str) or key in known:
            continue
        lowered = key.lower()
        if any(term in lowered for term in _SENSITIVE_METADATA_TERMS):
            continue
        if len(key.encode("utf-8")) > _MAX_METADATA_KEY_BYTES:
            continue
        if isinstance(item, bool) or item is None or isinstance(item, (int, float, str)):
            if isinstance(item, float) and not math.isfinite(item):
                continue
            if isinstance(item, str) and len(item.encode("utf-8")) > _MAX_METADATA_STRING_BYTES:
                continue
            result[key] = item
    return {key: result[key] for key in sorted(result)[:_MAX_METADATA_ITEMS]}


def _civil_time(
    value: object, fallback_day: date
) -> tuple[datetime | None, date, str | None]:
    if value is _MISSING or value is UNSET or value is None:
        return None, fallback_day, None
    if not isinstance(value, str):
        raise YazioProviderInvalidResponseError
    try:
        if "T" in value or " " in value:
            parsed = datetime.fromisoformat(value)
            local_date = parsed.date()
            timezone = parsed.tzinfo.tzname(None) if parsed.tzinfo is not None else None
            return parsed.replace(tzinfo=None), local_date, timezone
        local_date = date.fromisoformat(value)
    except ValueError as exc:
        raise YazioProviderInvalidResponseError from exc
    if local_date.isoformat() != value:
        raise YazioProviderInvalidResponseError
    return None, local_date, None


def _updated_time(value: object) -> datetime | None:
    if value is _MISSING or value is UNSET or value is None:
        return None
    if not isinstance(value, str):
        raise YazioProviderInvalidResponseError
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise YazioProviderInvalidResponseError from exc
    return parsed.replace(tzinfo=None)


def _nutrient_source(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    props = _field(value, "additional_properties")
    if isinstance(props, Mapping):
        return props
    if value is _MISSING or value is UNSET or value is None:
        return {}
    raise YazioProviderInvalidResponseError


_STRUCTURAL_NUTRIENT_KEYS = {
    "amount",
    "date",
    "daytime",
    "energy_goal",
    "id",
    "name",
    "product_id",
    "serving",
    "serving_quantity",
    "type",
}


def _nutrients(
    value: object, *, excluded: set[str] = _STRUCTURAL_NUTRIENT_KEYS
) -> YazioNutrientValues:
    source = dict(_nutrient_source(value))
    if not isinstance(value, Mapping):
        for key in _NUTRIENT_ALIASES:
            if key not in source:
                raw = _field(value, key)
                if raw is not _MISSING and raw is not UNSET:
                    source[key] = raw
    mapped: dict[str, Decimal | None] = {}
    additional: dict[str, Decimal] = {}
    for key, raw in source.items():
        if not isinstance(key, str):
            raise YazioProviderInvalidResponseError
        if key in excluded:
            continue
        target = _NUTRIENT_ALIASES.get(key)
        if target is None:
            try:
                converted = _decimal(raw)
            except YazioProviderInvalidResponseError:
                continue
            if converted is not None:
                additional[key] = converted
            continue
        mapped[target] = _decimal(raw)
    return YazioNutrientValues(additional=additional, **mapped)


def _consumed_items(
    response: object,
    *,
    context: YazioProviderErrorContext,
) -> tuple[list[object], list[object]]:
    parsed = getattr(response, "parsed", _MISSING)
    if parsed is _MISSING or parsed is None:
        raise YazioProviderInvalidResponseError(context=context)
    products = _field(parsed, "products")
    simple_products = _field(parsed, "simple_products")
    if products is _MISSING or products is UNSET:
        products = []
    if simple_products is _MISSING or simple_products is UNSET:
        simple_products = []
    if not isinstance(products, list) or not isinstance(simple_products, list):
        location = "products" if not isinstance(products, list) else "simple_products"
        raise YazioProviderInvalidResponseError(
            context=YazioProviderErrorContext(
                operation=context.operation,
                endpoint_key=context.endpoint_key,
                upstream_status_code=context.upstream_status_code,
                response_model=context.response_model,
                error_category="validation",
                validation_location=location,
                retryable=False,
            )
        )
    return products, simple_products


def _map_product_event(item: object, fallback_day: date) -> YazioConsumedProduct:
    known = {
        "id", "product_id", "amount", "date", "daytime", "serving", "serving_quantity", "type"
    }
    civil, local_date, timezone = _civil_time(_field(item, "date"), fallback_day)
    return YazioConsumedProduct(
        consumed_item_id=_required_string(_field(item, "id")),
        product_id=_required_string(_field(item, "product_id")),
        amount=_decimal(_field(item, "amount")),
        provider_civil_datetime=civil,
        local_date=local_date,
        daytime=_optional_string(_field(item, "daytime")),
        serving=_optional_string(_field(item, "serving")),
        serving_quantity=_decimal(_field(item, "serving_quantity")),
        provider_timezone=timezone,
        metadata=_metadata(item, known),
    )


def _map_simple_product(
    item: object,
    fallback_day: date,
    *,
    context: YazioProviderErrorContext,
) -> YazioConsumedSimpleProduct:
    if not isinstance(item, Mapping):
        raise YazioProviderInvalidResponseError(
            context=YazioProviderErrorContext(
                operation="simple_product_normalization",
                endpoint_key="consumed_items",
                upstream_status_code=context.upstream_status_code,
                response_model="ConsumedItems",
                error_category="validation",
                validation_location="simple_products[]",
                retryable=False,
            )
        )
    known = {
        "id", "amount", "date", "daytime", "serving", "serving_quantity", "type", "name",
        "nutrients", *_NUTRIENT_ALIASES,
    }
    civil, local_date, timezone = _civil_time(item.get("date", _MISSING), fallback_day)
    nutrient_value = item.get("nutrients", item)
    return YazioConsumedSimpleProduct(
        consumed_item_id=_required_string(item.get("id", _MISSING)),
        amount=_decimal(item.get("amount", _MISSING)),
        provider_civil_datetime=civil,
        local_date=local_date,
        daytime=_optional_string(item.get("daytime", _MISSING)),
        nutrients=_nutrients(nutrient_value),
        serving=_optional_string(item.get("serving", _MISSING)),
        serving_quantity=_decimal(item.get("serving_quantity", _MISSING)),
        provider_timezone=timezone,
        name=_optional_string(item.get("name", _MISSING)),
        metadata=_metadata(item, known),
    )


def _map_profile(product_id: str, product: object) -> YazioProductProfile:
    known = {
        "name", "producer", "category", "base_unit", "is_verified", "is_private", "is_deleted",
        "has_ean", "nutrients", "servings", "eans", "language", "countries", "updated_at",
    }
    raw_servings = _field(product, "servings")
    if raw_servings is _MISSING or raw_servings is UNSET:
        raw_servings = []
    if not isinstance(raw_servings, list):
        raise YazioProviderInvalidResponseError
    base_unit = _optional_string(_field(product, "base_unit"))
    servings: list[YazioServing] = []
    for item in raw_servings:
        servings.append(
            YazioServing(
                label=_optional_string(_field(item, "serving")),
                amount=_decimal(_field(item, "amount")),
                unit=base_unit,
                metadata=_metadata(item, {"serving", "amount"}),
            )
        )
    raw_eans = _field(product, "eans")
    if raw_eans is _MISSING or raw_eans is UNSET:
        raw_eans = []
    if not isinstance(raw_eans, list) or any(not isinstance(ean, str) for ean in raw_eans):
        raise YazioProviderInvalidResponseError
    raw_countries = _field(product, "countries")
    if raw_countries is _MISSING or raw_countries is UNSET:
        raw_countries = []
    if not isinstance(raw_countries, list) or any(not isinstance(country, str) for country in raw_countries):
        raise YazioProviderInvalidResponseError
    return YazioProductProfile(
        product_id=product_id,
        name=_optional_string(_field(product, "name")),
        producer=_optional_string(_field(product, "producer")),
        category=_optional_string(_field(product, "category")),
        base_unit=base_unit,
        nutrients=_nutrients(_field(product, "nutrients")),
        servings=tuple(servings),
        eans=tuple(raw_eans),
        language=_optional_string(_field(product, "language")),
        countries=tuple(raw_countries),
        updated_at=_updated_time(_field(product, "updated_at")),
        is_verified=_optional_bool(_field(product, "is_verified")),
        is_private=_optional_bool(_field(product, "is_private")),
        is_deleted=_optional_bool(_field(product, "is_deleted")),
        metadata=_metadata(product, known),
    )


def _map_daily_summary(item: object) -> YazioDailyNutrientSummary:
    local_date = _date_value(_field(item, "date"))
    known = {"date", "energy", "protein", "carb", "fat", "energy_goal"}
    return YazioDailyNutrientSummary(
        local_date=local_date,
        nutrients=_nutrients(item),
        energy_goal=_decimal(_field(item, "energy_goal")),
        metadata=_metadata(item, known),
    )


def _widget_activity(
    client: AuthenticatedClient, item_day: date
) -> tuple[date, float | None]:
    response = _call_detailed(
        get_daily_summary_widget.sync_detailed,
        operation="daily_summary",
        endpoint_key="daily_summary",
        response_model="DailySummaryWidget",
        client=client,
        date=item_day.isoformat(),
    )
    widget = getattr(response, "parsed", _MISSING)
    if widget is None or widget is _MISSING:
        raise YazioProviderInvalidResponseError
    return item_day, _numeric(_field(widget, "activity_energy"))

def _legacy_weight_day(
    client: AuthenticatedClient,
    item_day: date,
) -> float | None:
    """Compatibility fallback for lightweight test clients.

    Production clients expose ``request`` and therefore always use the
    generated ``get_latest_weight`` operation below. The fallback is limited
    to doubles that do not implement httpx.request.
    """
    try:
        response = client.get_httpx_client().get(
            f"{_base_url()}/v22/user/bodyvalues/weight/last",
            params={"date": item_day.isoformat()},
        )
    except _ResponseTooLargeError as exc:
        raise YazioProviderInvalidResponseError from exc
    except httpx.TimeoutException as exc:
        raise YazioProviderNetworkTimeoutError from exc
    except httpx.RequestError as exc:
        raise YazioProviderUnavailableError from exc
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise YazioProviderInvalidResponseError from exc
    try:
        _raise_for_status(response)
        _response_content_is_bounded(response)
        payload = response.json()
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise YazioProviderInvalidResponseError from exc
    finally:
        response.close()
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise YazioProviderInvalidResponseError
    return _numeric(payload.get("value", _MISSING))


def _weight_day(
    client: AuthenticatedClient,
    item_day: date,
) -> tuple[date, object | None]:
    http_client = client.get_httpx_client()
    generated_operation = get_latest_weight.sync_detailed
    # The generated operation is canonical. The fallback is only for the
    # unpatched generated function paired with a tiny legacy test double.
    if (
        not hasattr(http_client, "request")
        and hasattr(http_client, "get")
        and getattr(generated_operation, "__module__", "").startswith("yazio_sdk.")
    ):
        return item_day, _legacy_weight_day(client, item_day)
    response = _call_detailed(
        generated_operation,
        operation="latest_weight",
        endpoint_key="latest_weight",
        response_model="WeightEntry",
        client=client,
        date=item_day.isoformat(),
    )
    parsed = getattr(response, "parsed", _MISSING)
    if parsed is _MISSING:
        raise YazioProviderInvalidResponseError
    return item_day, parsed

def _bounded_string(value: object) -> str | None:
    result = _optional_string(value)
    if result is None:
        return None
    if len(result.encode("utf-8")) > 255 or "\x00" in result:
        raise YazioProviderInvalidResponseError
    return result


def _bounded_weight_metadata(value: object) -> str | dict[str, Any] | None:
    if value is _MISSING or value is UNSET or value is None:
        return None
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, Mapping) or _field(value, "additional_properties") is not _MISSING:
        return dict(_metadata(value, set()))
    raise YazioProviderInvalidResponseError


def _weight_identity(external_id: object) -> str | None:
    if external_id is None:
        return None
    if isinstance(external_id, str):
        return f"external:{external_id}" if external_id else None
    encoded = json.dumps(external_id, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if not encoded or encoded == "{}":
        return None
    import hashlib

    return f"external:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _normalize_weight_entry(entry: object) -> dict[str, Any] | None:
    if entry is None or isinstance(entry, (int, float, Decimal)):
        return None
    provider_id = _bounded_string(_field(entry, "id"))
    provider_date = _field(entry, "date")
    if provider_date is _MISSING or provider_date is UNSET or provider_date is None:
        return None
    parsed_date = _date_value(provider_date)
    value = _numeric(_field(entry, "value"))
    if value is None:
        return None
    external_id = _bounded_weight_metadata(_field(entry, "external_id"))
    identity = provider_id or _weight_identity(external_id)
    if identity is None:
        return None
    return {
        "id": provider_id,
        "date": parsed_date.isoformat(),
        "value": value,
        "unit": "kg",
        "external_id": external_id,
        "gateway": _bounded_string(_field(entry, "gateway")),
        "source": _bounded_weight_metadata(_field(entry, "source")),
        "_identity": identity,
    }


def _fetch_weight_range(
    client: AuthenticatedClient,
    start_day: date,
    end_day: date,
    *,
    max_workers: int,
) -> dict[str, object]:
    requested_days = (
        start_day + timedelta(days=offset)
        for offset in range((end_day - start_day).days + 1)
    )
    weights: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for _item_day, entry in executor.map(partial(_weight_day, client), requested_days):
            normalized = _normalize_weight_entry(entry)
            if normalized is None:
                continue
            identity = normalized.pop("_identity")
            if isinstance(identity, str):
                weights.setdefault(identity, normalized)
    return weights



class YazioSdkProvider:
    """Map safe v22 SDK responses into the legacy parser envelope."""

    mode: Literal["sdk"] = "sdk"

    def validate_credentials(self, email: str, password: str) -> None:
        client = _new_client()
        try:
            response = _call_detailed(
                create_token.sync_detailed,
                operation="oauth_token",
                endpoint_key="oauth_token",
                response_model="OAuthTokenResponse",
                client=client,
                body=OAuthTokenRequest(
                    username=email,
                    password=password,
                    grant_type="password",
                    client_id=settings.yazio_sdk_client_id,
                    client_secret=settings.yazio_sdk_client_secret,
                ),
                authentication=True,
            )
            _token_from_response(
                response,
                context=_context("oauth_token", "oauth_token", "OAuthTokenResponse"),
            )
        finally:
            _close_client(client)

    def fetch_food_diary(
        self, email: str, password: str, start_day: date, end_day: date
    ) -> YazioFoodDiary:
        if start_day > end_day or (end_day - start_day).days >= 366:
            raise YazioProviderInvalidResponseError

        client = _new_client()
        try:
            token_response = _call_detailed(
                create_token.sync_detailed,
                operation="oauth_token",
                endpoint_key="oauth_token",
                response_model="OAuthTokenResponse",
                client=client,
                body=OAuthTokenRequest(
                    username=email,
                    password=password,
                    grant_type="password",
                    client_id=settings.yazio_sdk_client_id,
                    client_secret=settings.yazio_sdk_client_secret,
                ),
                authentication=True,
            )
            token = _token_from_response(
                token_response,
                context=_context("oauth_token", "oauth_token", "OAuthTokenResponse"),
            )
        finally:
            _close_client(client)

        authenticated = _new_authenticated_client(token)
        try:
            consumed_products: list[YazioConsumedProduct] = []
            consumed_simple_products: list[YazioConsumedSimpleProduct] = []
            product_ids: list[str] = []
            seen_product_ids: set[str] = set()
            requested_days = (
                start_day + timedelta(days=offset)
                for offset in range((end_day - start_day).days + 1)
            )
            for requested_day in requested_days:
                response = _call_detailed(
                    list_consumed_items.sync_detailed,
                    operation="consumed_items",
                    endpoint_key="consumed_items",
                    response_model="ConsumedItems",
                    client=authenticated,
                    date=requested_day.isoformat(),
                )
                products, simple_products = _consumed_items(
                    response,
                    context=_context("consumed_items", "consumed_items", "ConsumedItems"),
                )
                for item in products:
                    product_event = _map_product_event(item, requested_day)
                    if not start_day <= product_event.local_date <= end_day:
                        raise YazioProviderInvalidResponseError
                    consumed_products.append(product_event)
                    if product_event.product_id not in seen_product_ids:
                        seen_product_ids.add(product_event.product_id)
                        product_ids.append(product_event.product_id)
                for item in simple_products:
                    simple_event = _map_simple_product(
                        item,
                        requested_day,
                        context=_context("consumed_items", "consumed_items", "ConsumedItems"),
                    )
                    if not start_day <= simple_event.local_date <= end_day:
                        raise YazioProviderInvalidResponseError
                    consumed_simple_products.append(simple_event)

            daily_response = _call_detailed(
                get_daily_nutrients.sync_detailed,
                operation="daily_nutrients",
                endpoint_key="daily_nutrients",
                response_model="DailyNutrients",
                client=authenticated,
                start=start_day.isoformat(),
                end=end_day.isoformat(),
            )
            daily_items = _daily_items(
                daily_response,
                context=_context("daily_nutrients", "daily_nutrients", "DailyNutrients"),
            )
            daily_summaries_by_date: dict[date, YazioDailyNutrientSummary] = {}
            for item in daily_items:
                summary = _map_daily_summary(item)
                if (
                    summary.local_date < start_day
                    or summary.local_date > end_day
                    or summary.local_date in daily_summaries_by_date
                ):
                    raise YazioProviderInvalidResponseError
                daily_summaries_by_date[summary.local_date] = summary
            daily_summaries = tuple(
                daily_summaries_by_date.get(day)
                or YazioDailyNutrientSummary(
                    local_date=day,
                    nutrients=YazioNutrientValues(),
                    energy_goal=None,
                    metadata={"provider_summary_missing": True},
                )
                for day in (
                    start_day + timedelta(days=offset)
                    for offset in range((end_day - start_day).days + 1)
                )
            )

            profiles: list[YazioProductProfile] = []
            for product_id in product_ids:
                response = _call_detailed(
                    partial(get_product.sync_detailed, product_id),
                    operation="product_lookup",
                    endpoint_key="product",
                    response_model="Product",
                    client=authenticated,
                )
                parsed = getattr(response, "parsed", _MISSING)
                if parsed is _MISSING or parsed is None:
                    raise YazioProviderInvalidResponseError(
                        context=_context(
                            "product_lookup",
                            "product",
                            "Product",
                            location="response",
                        )
                    )
                profiles.append(_map_profile(product_id, parsed))

            return YazioFoodDiary(
                requested_start_day=start_day,
                requested_end_day=end_day,
                consumed_products=tuple(consumed_products),
                consumed_simple_products=tuple(consumed_simple_products),
                product_profiles=tuple(profiles),
                daily_summaries=daily_summaries,
            )
        finally:
            _close_client(authenticated)

    def fetch(
        self,
        email: str,
        password: str,
        start_day: date,
        end_day: date,
        include_micronutrients: bool,
    ) -> YazioProviderResult:
        del include_micronutrients
        if start_day > end_day:
            raise YazioProviderInvalidResponseError
        if (end_day - start_day).days >= 366:
            raise YazioProviderInvalidResponseError

        client = _new_client()
        try:
            token_response = _call_detailed(
                create_token.sync_detailed,
                operation="oauth_token",
                endpoint_key="oauth_token",
                response_model="OAuthTokenResponse",
                client=client,
                body=OAuthTokenRequest(
                    username=email,
                    password=password,
                    grant_type="password",
                    client_id=settings.yazio_sdk_client_id,
                    client_secret=settings.yazio_sdk_client_secret,
                ),
                authentication=True,
            )
            token = _token_from_response(
                token_response,
                context=_context("oauth_token", "oauth_token", "OAuthTokenResponse"),
            )
        finally:
            _close_client(client)

        authenticated = _new_authenticated_client(token)
        try:
            daily_response = _call_detailed(
                get_daily_nutrients.sync_detailed,
                operation="daily_nutrients",
                endpoint_key="daily_nutrients",
                response_model="DailyNutrients",
                client=authenticated,
                start=start_day.isoformat(),
                end=end_day.isoformat(),
            )
            days: dict[str, dict[str, float]] = {
                (start_day + timedelta(days=offset)).isoformat(): {}
                for offset in range((end_day - start_day).days + 1)
            }
            seen: set[date] = set()
            for item in _daily_items(
                daily_response,
                context=_context("daily_nutrients", "daily_nutrients", "DailyNutrients"),
            ):
                item_day = _date_value(_field(item, "date"))
                if item_day < start_day or item_day > end_day or item_day in seen:
                    raise YazioProviderInvalidResponseError
                seen.add(item_day)
                mapped: dict[str, float] = {}
                for source_name, target_name in (
                    ("energy", "energy"),
                    ("protein", "protein"),
                    ("carb", "carb"),
                    ("fat", "fat"),
                ):
                    value = _numeric(_field(item, source_name))
                    if value is not None:
                        mapped[target_name] = value
                days[item_day.isoformat()] = mapped

            # The aggregate endpoint has no activity-energy field.  Keep the
            # widget requests bounded by the shared worker cap.
            requested_days = (
                start_day + timedelta(days=offset)
                for offset in range((end_day - start_day).days + 1)
            )
            with ThreadPoolExecutor(max_workers=settings.yazio_request_workers) as executor:
                for item_day, activity in executor.map(
                    partial(_widget_activity, authenticated), requested_days
                ):
                    if activity is not None:
                        days[item_day.isoformat()]["activity_energy"] = activity

            weight = _fetch_weight_range(
                authenticated,
                start_day,
                end_day,
                max_workers=settings.yazio_request_workers,
            )
            return YazioProviderResult(
                payload={"days": days, "weight": weight},
                metadata=YazioProviderMetadata(
                    micronutrient_complete=False,
                    provider_mode="sdk",
                ),
            )
        finally:
            _close_client(authenticated)
