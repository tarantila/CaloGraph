from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, time
import json
import math
import re
from typing import Protocol, cast

import httpx

from app.google_health.constants import GOOGLE_HEALTH_API_BASE_URL
from app.google_health.errors import (
    GoogleHealthAuthenticationError,
    GoogleHealthClientError,
    GoogleHealthInvalidResponseError,
    GoogleHealthProviderUnavailableError,
    GoogleHealthRateLimitedError,
    GoogleHealthScopeError,
    GoogleHealthTransientError,
)

GOOGLE_HEALTH_NUTRITION_LOG_PATH = "/users/me/dataTypes/nutrition-log/dataPoints"
GOOGLE_HEALTH_MAX_PAGE_SIZE = 100
GOOGLE_HEALTH_MAX_PAGE_TOKEN_LENGTH = 512
GOOGLE_HEALTH_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NutritionQuantity:
    value: float
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class NutritionNutrient:
    nutrient: str
    quantity: NutritionQuantity


@dataclass(frozen=True, slots=True)
class NutritionServing:
    food_measurement_unit: str
    food_measurement_unit_display_name: str | None = None
    amount: float | None = None


@dataclass(frozen=True, slots=True)
class NutritionLogInterval:
    start_time: datetime
    end_time: datetime
    start_utc_offset: str
    end_utc_offset: str
    civil_start_time: datetime | None = None
    civil_end_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class NutritionLog:
    interval: NutritionLogInterval
    nutrients: tuple[NutritionNutrient, ...] = ()
    energy: NutritionQuantity | None = None
    energy_from_fat: NutritionQuantity | None = None
    total_carbohydrate: NutritionQuantity | None = None
    total_fat: NutritionQuantity | None = None
    meal_type: str | None = None
    serving: NutritionServing | None = None
    food: str | None = None
    food_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class NutritionLogDataPoint:
    name: str | None
    nutrition_log: NutritionLog


@dataclass(frozen=True, slots=True)
class NutritionLogPageAggregate:
    count: int
    next_page_token: str | None
    page_size: int
    page_token: str | None
    start_time: datetime | None
    end_time: datetime | None
    civil_start_time: date | datetime | None
    civil_end_time: date | datetime | None


@dataclass(frozen=True, slots=True)
class NutritionLogPage:
    data_points: tuple[NutritionLogDataPoint, ...]
    next_page_token: str | None
    page_size: int
    page_token: str | None
    start_time: datetime | None
    end_time: datetime | None
    civil_start_time: date | datetime | None
    civil_end_time: date | datetime | None

    def aggregate(self) -> NutritionLogPageAggregate:
        return NutritionLogPageAggregate(
            count=len(self.data_points),
            next_page_token=self.next_page_token,
            page_size=self.page_size,
            page_token=self.page_token,
            start_time=self.start_time,
            end_time=self.end_time,
            civil_start_time=self.civil_start_time,
            civil_end_time=self.civil_end_time,
        )

class GoogleHealthResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> object: ...


class GoogleHealthStreamResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_bytes(self) -> Iterator[bytes]: ...


@dataclass(frozen=True, slots=True)
class _BufferedResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def json(self) -> object:
        return cast(object, json.loads(self.content))


class GoogleHealthTransport(Protocol):
    def get_nutrition_log(
        self,
        *,
        access_token: str,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
        civil_start_time: date | datetime | None = None,
        civil_end_time: date | datetime | None = None,
    ) -> GoogleHealthResponse: ...


class _HTTPClient(Protocol):
    def stream(
        self, method: str, url: str, **kwargs: object
    ) -> AbstractContextManager[GoogleHealthStreamResponse]: ...

    def close(self) -> None: ...

class GoogleHealthHTTPTransport:
    """A deliberately narrow, fixed-host GET transport for Nutrition Log."""

    def __init__(
        self,
        *,
        http_client: _HTTPClient | None = None,
        connect_timeout: float = 3.05,
        read_timeout: float = 15.0,
    ) -> None:
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("Google Health timeouts must be positive")
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._http_client = http_client or httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
        )

    def get_nutrition_log(
        self,
        *,
        access_token: str,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
        civil_start_time: date | datetime | None = None,
        civil_end_time: date | datetime | None = None,
    ) -> GoogleHealthResponse:
        """Perform exactly one GET against the fixed Nutrition Log resource."""
        _validate_page_size(page_size)
        _validate_page_token(page_token)
        _validate_time_bounds(start_time, end_time)
        _validate_civil_bounds(civil_start_time, civil_end_time)
        if not access_token or any(ord(char) < 0x20 for char in access_token):
            raise GoogleHealthAuthenticationError("Google Health credentials are unavailable")

        params: dict[str, str] = {"pageSize": str(page_size)}
        if page_token is not None:
            params["pageToken"] = page_token
        filters: list[str] = []
        if start_time is not None:
            filters.append(f'nutrition_log.interval.start_time >= "{start_time.isoformat()}"')
        if end_time is not None:
            filters.append(f'nutrition_log.interval.start_time < "{end_time.isoformat()}"')
        if civil_start_time is not None:
            filters.append(
                f'nutrition_log.interval.civil_start_time >= "{_civil_time_text(civil_start_time)}"'
            )
        if civil_end_time is not None:
            filters.append(
                f'nutrition_log.interval.civil_start_time < "{_civil_time_text(civil_end_time)}"'
            )
        if filters:
            params["filter"] = " AND ".join(filters)
        url = f"{GOOGLE_HEALTH_API_BASE_URL}{GOOGLE_HEALTH_NUTRITION_LOG_PATH}"
        with self._http_client.stream(
            "GET",
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self._timeout,
            follow_redirects=False,
        ) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                if not isinstance(chunk, bytes) or len(body) + len(chunk) > GOOGLE_HEALTH_MAX_RESPONSE_BYTES:
                    raise GoogleHealthInvalidResponseError(
                        "Google Health response is too large or invalid"
                    ) from None
                body.extend(chunk)
            return _BufferedResponse(response.status_code, dict(response.headers), bytes(body))

    def close(self) -> None:
        self._http_client.close()


class GoogleHealthClient:
    """Read-only Google Health client with in-memory credentials only."""

    def __init__(
        self,
        transport: GoogleHealthTransport | None,
        credentials: object,
        *,
        max_page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
    ) -> None:
        _validate_page_size_limit(max_page_size)
        self._transport = transport or GoogleHealthHTTPTransport()
        self._credentials = credentials
        self._max_page_size = max_page_size

    def get_nutrition_log_page(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        civil_start_time: date | datetime | None = None,
        civil_end_time: date | datetime | None = None,
    ) -> NutritionLogPage:
        _validate_page_size(page_size, maximum=self._max_page_size)
        _validate_page_token(page_token)
        _validate_time_bounds(start_time, end_time)
        _validate_civil_bounds(civil_start_time, civil_end_time)
        access_token = self._access_token()
        try:
            response = self._transport.get_nutrition_log(
                access_token=access_token,
                page_size=page_size,
                page_token=page_token,
                start_time=start_time,
                end_time=end_time,
                civil_start_time=civil_start_time,
                civil_end_time=civil_end_time,
            )
        except GoogleHealthClientError:
            raise
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RequestError,
            TimeoutError,
            ConnectionError,
            OSError,
        ):
            raise GoogleHealthTransientError("Google Health transport failed temporarily") from None
        except Exception:
            raise GoogleHealthTransientError("Google Health transport failed temporarily") from None
        return self._parse_response(
            response,
            page_size=page_size,
            page_token=page_token,
            start_time=start_time,
            end_time=end_time,
            civil_start_time=civil_start_time,
            civil_end_time=civil_end_time,
        )

    def _access_token(self) -> str:
        credentials = self._credentials
        token = getattr(credentials, "token", None)
        needs_refresh = (
            bool(getattr(credentials, "expired", False))
            or getattr(credentials, "valid", True) is False
            or not isinstance(token, str)
            or not token
        )
        if needs_refresh:
            refresh = getattr(credentials, "refresh", None)
            if not callable(refresh):
                raise GoogleHealthAuthenticationError("Google Health credentials require reauthentication")
            try:
                from google.auth.transport.requests import Request

                refresh(Request())
            except Exception:
                raise GoogleHealthAuthenticationError(
                    "Google Health credentials require reauthentication"
                ) from None
            token = getattr(credentials, "token", None)
        if not isinstance(token, str) or not token or any(ord(char) < 0x20 for char in token):
            raise GoogleHealthAuthenticationError("Google Health credentials require reauthentication")
        return token

    @staticmethod
    def _parse_response(
        response: GoogleHealthResponse,
        *,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
        civil_start_time: date | datetime | None,
        civil_end_time: date | datetime | None,
    ) -> NutritionLogPage:
        try:
            status_code = int(response.status_code)
        except (AttributeError, TypeError, ValueError):
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response") from None

        if status_code == 401:
            raise GoogleHealthAuthenticationError("Google Health credentials require reauthentication")
        if status_code == 403:
            raise GoogleHealthScopeError("Google Health read permission is unavailable")
        if status_code == 429:
            raise GoogleHealthRateLimitedError(_retry_after(response.headers))
        if 500 <= status_code <= 599:
            raise GoogleHealthProviderUnavailableError("Google Health is temporarily unavailable")
        if status_code < 200 or status_code >= 300:
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response")

        try:
            content_length = response.headers.get("content-length")
            if content_length is not None and int(content_length) > GOOGLE_HEALTH_MAX_RESPONSE_BYTES:
                raise ValueError
            payload = response.json()
        except Exception:
            raise GoogleHealthInvalidResponseError("Google Health returned malformed JSON") from None
        if not isinstance(payload, dict):
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response")
        if set(payload) - {"dataPoints", "nextPageToken", "nutritionLog"}:
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response")

        try:
            points_payload = payload.get("dataPoints")
            # Keep the old empty fixture useful while the transport contract changes.
            if points_payload is None and payload == {"nutritionLog": []}:
                points_payload = []
            if not isinstance(points_payload, list) or len(points_payload) > page_size:
                raise ValueError
            next_page_token = _parse_response_page_token(payload)
            data_points = tuple(_parse_data_point(item) for item in points_payload)
            data_points = tuple(
                item
                for item in data_points
                if _matches_civil_bounds(item, civil_start_time, civil_end_time)
            )
        except (GoogleHealthInvalidResponseError, ValueError, TypeError, KeyError):
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid Nutrition Log page") from None

        return NutritionLogPage(
            data_points=data_points,
            next_page_token=next_page_token,
            page_size=page_size,
            page_token=page_token,
            start_time=start_time,
            end_time=end_time,
            civil_start_time=civil_start_time,
            civil_end_time=civil_end_time,
        )


_DURATION_RE = re.compile(r"^-?\d+(?:\.\d+)?s$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)
_MEAL_TYPES = {
    "MEAL_TYPE_UNSPECIFIED",
    "BEFORE_BREAKFAST",
    "BREAKFAST",
    "BEFORE_LUNCH",
    "LUNCH",
    "BEFORE_DINNER",
    "DINNER",
    "AFTER_DINNER",
    "SNACK",
    "ANYTIME",
}


def _parse_response_page_token(payload: Mapping[str, object]) -> str | None:
    if "nextPageToken" not in payload:
        return None
    token = payload["nextPageToken"]
    if token == "":
        return None
    _validate_page_token(token)
    return cast(str, token)


def _parse_data_point(value: object) -> NutritionLogDataPoint:
    if not isinstance(value, dict):
        raise ValueError
    if set(value) - {"name", "dataSource", "nutritionLog"} or "nutritionLog" not in value:
        raise ValueError
    name = value.get("name")
    if name is not None and (not isinstance(name, str) or not name):
        raise ValueError
    data_source = value.get("dataSource")
    if data_source is not None and not isinstance(data_source, dict):
        raise ValueError
    nutrition_log = value["nutritionLog"]
    if not isinstance(nutrition_log, dict):
        raise ValueError
    return NutritionLogDataPoint(
        name=cast(str | None, name),
        nutrition_log=_parse_nutrition_log(nutrition_log),
    )


def _parse_nutrition_log(value: Mapping[str, object]) -> NutritionLog:
    allowed = {
        "interval",
        "nutrients",
        "energy",
        "energyFromFat",
        "totalCarbohydrate",
        "totalFat",
        "mealType",
        "serving",
        "food",
        "foodDisplayName",
    }
    if set(value) - allowed or "interval" not in value:
        raise ValueError
    interval = value["interval"]
    if not isinstance(interval, dict):
        raise ValueError
    nutrients_value = value.get("nutrients", [])
    if not isinstance(nutrients_value, list):
        raise ValueError
    nutrients = tuple(_parse_nutrient(item) for item in nutrients_value)
    meal_type = value.get("mealType")
    if meal_type is not None and (not isinstance(meal_type, str) or meal_type not in _MEAL_TYPES):
        raise ValueError
    food = value.get("food")
    food_display_name = value.get("foodDisplayName")
    if food is not None and (not isinstance(food, str) or not food):
        raise ValueError
    if food_display_name is not None and (
        not isinstance(food_display_name, str) or not food_display_name
    ):
        raise ValueError
    serving_value = value.get("serving")
    serving = _parse_serving(serving_value) if serving_value is not None else None
    return NutritionLog(
        interval=_parse_interval(interval),
        nutrients=nutrients,
        energy=_parse_quantity(value.get("energy"), "kcal") if "energy" in value else None,
        energy_from_fat=(
            _parse_quantity(value.get("energyFromFat"), "kcal")
            if "energyFromFat" in value
            else None
        ),
        total_carbohydrate=(
            _parse_quantity(value.get("totalCarbohydrate"), "grams")
            if "totalCarbohydrate" in value
            else None
        ),
        total_fat=(
            _parse_quantity(value.get("totalFat"), "grams") if "totalFat" in value else None
        ),
        meal_type=meal_type,
        serving=serving,
        food=food,
        food_display_name=food_display_name,
    )


def _parse_interval(value: Mapping[str, object]) -> NutritionLogInterval:
    allowed = {
        "startTime",
        "endTime",
        "startUtcOffset",
        "endUtcOffset",
        "civilStartTime",
        "civilEndTime",
    }
    if set(value) - allowed or not {"startTime", "endTime", "startUtcOffset", "endUtcOffset"} <= set(value):
        raise ValueError
    start_time = _parse_timestamp(value["startTime"])
    end_time = _parse_timestamp(value["endTime"])
    if start_time >= end_time:
        raise ValueError
    start_offset = value["startUtcOffset"]
    end_offset = value["endUtcOffset"]
    if (
        not isinstance(start_offset, str)
        or not isinstance(end_offset, str)
        or not _DURATION_RE.fullmatch(start_offset)
        or not _DURATION_RE.fullmatch(end_offset)
    ):
        raise ValueError
    civil_start = (
        _parse_civil_datetime(value["civilStartTime"]) if "civilStartTime" in value else None
    )
    civil_end = _parse_civil_datetime(value["civilEndTime"]) if "civilEndTime" in value else None
    if civil_start is not None and civil_end is not None and civil_start > civil_end:
        raise ValueError
    return NutritionLogInterval(
        start_time=start_time,
        end_time=end_time,
        start_utc_offset=start_offset,
        end_utc_offset=end_offset,
        civil_start_time=civil_start,
        civil_end_time=civil_end,
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError from None
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


def _parse_civil_datetime(value: object) -> datetime:
    if not isinstance(value, dict) or set(value) - {"date", "time"} or "date" not in value:
        raise ValueError
    date_value = value["date"]
    if not isinstance(date_value, dict) or set(date_value) != {"year", "month", "day"}:
        raise ValueError
    year, month, day = (date_value.get(key) for key in ("year", "month", "day"))
    if (
        any(isinstance(item, bool) or not isinstance(item, int) for item in (year, month, day))
        or year is None
        or year < 1
        or not 1 <= month <= 12
        or not 1 <= day <= 31
    ):
        raise ValueError
    time_value = value.get("time", {})
    if not isinstance(time_value, dict) or set(time_value) - {"hours", "minutes", "seconds", "nanos"}:
        raise ValueError
    hours = time_value.get("hours", 0)
    minutes = time_value.get("minutes", 0)
    seconds = time_value.get("seconds", 0)
    nanos = time_value.get("nanos", 0)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (hours, minutes, seconds, nanos)):
        raise ValueError
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59 or not 0 <= seconds <= 59 or not 0 <= nanos <= 999_999_999:
        raise ValueError
    try:
        return datetime(year, month, day, hours, minutes, seconds, nanos // 1000)
    except ValueError:
        raise ValueError from None


def _parse_quantity(value: object, scalar_key: str) -> NutritionQuantity:
    if not isinstance(value, dict) or set(value) - {"userProvidedUnit", scalar_key} or scalar_key not in value:
        raise ValueError
    number = value[scalar_key]
    unit = value.get("userProvidedUnit")
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        raise ValueError
    if unit is not None and (not isinstance(unit, str) or not re.fullmatch(r"[A-Z_]+", unit)):
        raise ValueError
    return NutritionQuantity(value=float(number), unit=unit)


def _parse_nutrient(value: object) -> NutritionNutrient:
    if not isinstance(value, dict) or set(value) != {"quantity", "nutrient"}:
        raise ValueError
    nutrient = value["nutrient"]
    if not isinstance(nutrient, str) or not re.fullmatch(r"[A-Z][A-Z_]+", nutrient):
        raise ValueError
    quantity = value["quantity"]
    return NutritionNutrient(nutrient=nutrient, quantity=_parse_quantity(quantity, "grams"))


def _parse_serving(value: object) -> NutritionServing:
    if not isinstance(value, dict) or set(value) - {
        "foodMeasurementUnit",
        "foodMeasurementUnitDisplayName",
        "amount",
    } or "foodMeasurementUnit" not in value:
        raise ValueError
    unit = value["foodMeasurementUnit"]
    display_name = value.get("foodMeasurementUnitDisplayName")
    amount = value.get("amount")
    if not isinstance(unit, str) or not unit:
        raise ValueError
    if display_name is not None and (not isinstance(display_name, str) or not display_name):
        raise ValueError
    if amount is not None and (
        isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount)
    ):
        raise ValueError
    return NutritionServing(
        food_measurement_unit=unit,
        food_measurement_unit_display_name=display_name,
        amount=float(amount) if amount is not None else None,
    )


def _civil_boundary(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ValueError("civil time bounds must be timezone-naive")
        return value.replace(microsecond=value.microsecond)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raise ValueError("civil time bounds must be dates or naive datetimes")


def _civil_time_text(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError("civil time bounds must be dates or naive datetimes")


def _matches_civil_bounds(
    point: NutritionLogDataPoint,
    start: date | datetime | None,
    end: date | datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    civil_start = point.nutrition_log.interval.civil_start_time
    if civil_start is None:
        raise ValueError
    return (start is None or civil_start >= _civil_boundary(start)) and (
        end is None or civil_start < _civil_boundary(end)
    )

def _validate_page_size(page_size: int, *, maximum: int = GOOGLE_HEALTH_MAX_PAGE_SIZE) -> None:
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise ValueError("page_size must be an integer")
    if not 1 <= page_size <= maximum:
        raise ValueError("page_size is outside the allowed range")


def _validate_page_size_limit(maximum: int) -> None:
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        raise ValueError("max_page_size must be an integer")
    if not 1 <= maximum <= GOOGLE_HEALTH_MAX_PAGE_SIZE:
        raise ValueError("max_page_size is outside the allowed range")
def _validate_page_token(page_token: str | None) -> None:
    if page_token is None:
        return
    if (
        not isinstance(page_token, str)
        or not page_token
        or len(page_token) > GOOGLE_HEALTH_MAX_PAGE_TOKEN_LENGTH
    ):
        raise ValueError("page_token is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in page_token):
        raise ValueError("page_token is invalid")


def _validate_time_bounds(start_time: datetime | None, end_time: datetime | None) -> None:
    for value in (start_time, end_time):
        if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
            raise ValueError("time bounds must be timezone-aware datetimes")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise ValueError("start_time must not be after end_time")


def _validate_civil_bounds(
    start: date | datetime | None,
    end: date | datetime | None,
) -> None:
    if start is not None:
        _civil_boundary(start)
    if end is not None:
        _civil_boundary(end)
    if start is not None and end is not None and _civil_boundary(start) > _civil_boundary(end):
        raise ValueError("civil_start_time must not be after civil_end_time")

def _retry_after(headers: Mapping[str, str]) -> int:
    try:
        raw_value = headers.get("retry-after") or headers.get("Retry-After") or "0"
        value = int(raw_value)
    except (AttributeError, TypeError, ValueError):
        return 0
    return max(0, min(value, 300))


__all__ = [
    "GOOGLE_HEALTH_API_BASE_URL",
    "GOOGLE_HEALTH_MAX_PAGE_SIZE",
    "GOOGLE_HEALTH_NUTRITION_LOG_PATH",
    "GoogleHealthAuthenticationError",
    "GoogleHealthClient",
    "GoogleHealthClientError",
    "GoogleHealthHTTPTransport",
    "GoogleHealthInvalidResponseError",
    "GoogleHealthProviderUnavailableError",
    "GoogleHealthRateLimitedError",
    "GoogleHealthScopeError",
    "GoogleHealthTransientError",
    "NutritionLog",
    "NutritionLogDataPoint",
    "NutritionLogInterval",
    "NutritionLogPage",
    "NutritionLogPageAggregate",
    "NutritionNutrient",
    "NutritionQuantity",
    "NutritionServing",
]
