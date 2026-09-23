from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Protocol, cast

import httpx

from app.google_health.constants import (
    GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH as _GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH,
)
from app.google_health.constants import GOOGLE_HEALTH_API_BASE_URL
from app.google_health.constants import (
    GOOGLE_HEALTH_WEIGHT_PATH as _GOOGLE_HEALTH_WEIGHT_PATH,
)
from app.google_health.errors import (
    GoogleHealthAuthenticationError,
    GoogleHealthClientError,
    GoogleHealthInvalidResponseError,
    GoogleHealthProviderUnavailableError,
    GoogleHealthRateLimitedError,
    GoogleHealthScopeError,
    GoogleHealthTransientError,
)
from app.importers.common import decimal_value

GOOGLE_HEALTH_NUTRITION_LOG_PATH = "/users/me/dataTypes/nutrition-log/dataPoints"
GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH = _GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH
GOOGLE_HEALTH_WEIGHT_PATH = _GOOGLE_HEALTH_WEIGHT_PATH
GOOGLE_HEALTH_MAX_PAGE_SIZE = 100
GOOGLE_HEALTH_MAX_PAGES = 100
GOOGLE_HEALTH_MAX_PAGE_TOKEN_LENGTH = 512
GOOGLE_HEALTH_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_DATA_TYPE_PATHS = {
    "nutrition-log": GOOGLE_HEALTH_NUTRITION_LOG_PATH,
    "active-energy-burned": GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH,
    "weight": GOOGLE_HEALTH_WEIGHT_PATH,
}
_DATA_TYPE_FILTER_FIELDS = {
    "active-energy-burned": "active_energy_burned.interval.start_time",
    "weight": "weight.sample_time.physical_time",
}


@dataclass(frozen=True, slots=True)
class NutritionQuantity:
    value: Decimal | float
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class NutritionNutrient:
    nutrient: str
    quantity: NutritionQuantity


@dataclass(frozen=True, slots=True)
class NutritionServing:
    food_measurement_unit: str | None = None
    food_measurement_unit_display_name: str | None = None
    amount: Decimal | float | None = None


@dataclass(frozen=True, slots=True)
class NutritionDataSourceDevice:
    form_factor: str | None = None
    manufacturer: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class NutritionDataSourceApplication:
    package_name: str | None = None
    web_client_id: str | None = None
    google_web_client_id: str | None = None


@dataclass(frozen=True, slots=True)
class NutritionDataSource:
    recording_method: str | None = None
    platform: str | None = None
    device: NutritionDataSourceDevice | None = None
    application: NutritionDataSourceApplication | None = None


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
    data_source: NutritionDataSource | None = None


@dataclass(frozen=True, slots=True)
class GoogleHealthDataPoint:
    name: str
    start_time: datetime
    end_time: datetime
    value: Decimal
    unit: str
    data_source: NutritionDataSource | None = None
    start_utc_offset: str | None = None
    end_utc_offset: str | None = None


@dataclass(frozen=True, slots=True)
class ActiveEnergyBurnedDataPoint(GoogleHealthDataPoint):
    pass


@dataclass(frozen=True, slots=True)
class WeightDataPoint(GoogleHealthDataPoint):
    pass


GoogleHealthActivityDataPoint = ActiveEnergyBurnedDataPoint
GoogleHealthWeightDataPoint = WeightDataPoint


@dataclass(frozen=True, slots=True)
class _PreciseTime:
    value: datetime
    nanos: int


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


@dataclass(frozen=True, slots=True)
class GoogleHealthDataPointPage:
    data_points: tuple[GoogleHealthDataPoint | NutritionLogDataPoint, ...]
    next_page_token: str | None
    page_size: int
    page_token: str | None
    data_type: str
    start_time: datetime | None
    end_time: datetime | None


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
        return cast(object, json.loads(self.content, parse_float=Decimal))


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

    def get_data_points(
        self,
        *,
        data_type: str,
        access_token: str,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> GoogleHealthResponse: ...

    def close(self) -> None: ...


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
        _reject_physical_bounds(start_time, end_time)
        _validate_civil_bounds(civil_start_time, civil_end_time)
        if not access_token or any(ord(char) < 0x20 for char in access_token):
            raise GoogleHealthAuthenticationError("Google Health credentials are unavailable")

        params: dict[str, str] = {"pageSize": str(page_size)}
        if page_token is not None:
            params["pageToken"] = page_token
        filters: list[str] = []
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
                if (
                    not isinstance(chunk, bytes)
                    or len(body) + len(chunk) > GOOGLE_HEALTH_MAX_RESPONSE_BYTES
                ):
                    raise GoogleHealthInvalidResponseError(
                        "Google Health response is too large or invalid"
                    ) from None
                body.extend(chunk)
            return cast(
                GoogleHealthResponse,
                _BufferedResponse(response.status_code, dict(response.headers), bytes(body)),
            )

    def get_data_points(
        self,
        *,
        data_type: str,
        access_token: str,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> GoogleHealthResponse:
        """Perform one bounded GET against an allowlisted scalar data type."""
        path = _DATA_TYPE_PATHS.get(data_type)
        if path is None:
            raise ValueError("Google Health data type is not supported")
        _validate_page_size(page_size)
        _validate_page_token(page_token)
        _validate_physical_bounds(start_time, end_time)
        if not access_token or any(ord(char) < 0x20 for char in access_token):
            raise GoogleHealthAuthenticationError("Google Health credentials are unavailable")
        params: dict[str, str] = {"pageSize": str(page_size)}
        if page_token is not None:
            params["pageToken"] = page_token
        filter_field = _DATA_TYPE_FILTER_FIELDS.get(data_type)
        if (start_time is not None or end_time is not None) and filter_field is None:
            raise ValueError("physical time bounds are unsupported for this data type")
        filters: list[str] = []
        if start_time is not None and filter_field is not None:
            filters.append(f'{filter_field} >= "{_physical_time_text(start_time)}"')
        if end_time is not None and filter_field is not None:
            filters.append(f'{filter_field} < "{_physical_time_text(end_time)}"')
        if filters:
            params["filter"] = " AND ".join(filters)
        url = f"{GOOGLE_HEALTH_API_BASE_URL}{path}"
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
                if (
                    not isinstance(chunk, bytes)
                    or len(body) + len(chunk) > GOOGLE_HEALTH_MAX_RESPONSE_BYTES
                ):
                    raise GoogleHealthInvalidResponseError(
                        "Google Health response is too large or invalid"
                    ) from None
                body.extend(chunk)
            return cast(
                GoogleHealthResponse,
                _BufferedResponse(response.status_code, dict(response.headers), bytes(body)),
            )

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
        _reject_physical_bounds(start_time, end_time)
        _validate_civil_bounds(civil_start_time, civil_end_time)
        access_token = self._access_token()
        try:
            get_nutrition_log = getattr(self._transport, "get_nutrition_log", None)
            if callable(get_nutrition_log):
                response = get_nutrition_log(
                    access_token=access_token,
                    page_size=page_size,
                    page_token=page_token,
                    start_time=start_time,
                    end_time=end_time,
                    civil_start_time=civil_start_time,
                    civil_end_time=civil_end_time,
                )
            else:
                response = self._transport.get_data_points(
                    data_type="nutrition-log",
                    access_token=access_token,
                    page_size=page_size,
                    page_token=page_token,
                    start_time=start_time,
                    end_time=end_time,
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

    def get_data_points_page(
        self,
        data_type: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page_token: str | None = None,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
    ) -> GoogleHealthDataPointPage:
        if data_type not in _DATA_TYPE_PATHS:
            raise ValueError("Google Health data type is not supported")
        _validate_page_size(page_size, maximum=self._max_page_size)
        _validate_page_token(page_token)
        if data_type == "nutrition-log":
            _reject_physical_bounds(start_time, end_time)
        else:
            _validate_physical_bounds(start_time, end_time)
        access_token = self._access_token()
        try:
            response = self._transport.get_data_points(
                data_type=data_type,
                access_token=access_token,
                page_size=page_size,
                page_token=page_token,
                start_time=start_time,
                end_time=end_time,
            )
        except GoogleHealthClientError:
            raise
        except Exception:
            raise GoogleHealthTransientError("Google Health transport failed temporarily") from None
        return self._parse_data_points_response(
            response,
            data_type=data_type,
            page_size=page_size,
            page_token=page_token,
            start_time=start_time,
            end_time=end_time,
        )

    def iter_data_points_pages(
        self,
        data_type: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
        page_token: str | None = None,
        max_pages: int = GOOGLE_HEALTH_MAX_PAGES,
    ) -> Iterator[GoogleHealthDataPointPage]:
        _validate_page_budget(max_pages)
        _validate_page_token(page_token)
        token = page_token
        seen_tokens: set[str] = {page_token} if page_token is not None else set()
        for _ in range(max_pages):
            page = self.get_data_points_page(
                data_type,
                start_time=start_time,
                end_time=end_time,
                page_token=token,
                page_size=page_size,
            )
            yield page
            next_token = page.next_page_token
            if next_token is None:
                return
            if next_token in seen_tokens:
                raise GoogleHealthInvalidResponseError(
                    "Google Health pagination token repeated"
                ) from None
            seen_tokens.add(next_token)
            token = next_token
        raise GoogleHealthInvalidResponseError("Google Health pagination limit exceeded") from None

    def iter_data_points(
        self,
        data_type: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
        page_token: str | None = None,
        max_pages: int = GOOGLE_HEALTH_MAX_PAGES,
    ) -> Iterator[GoogleHealthDataPoint | NutritionLogDataPoint]:
        for page in self.iter_data_points_pages(
            data_type,
            start_time=start_time,
            end_time=end_time,
            page_size=page_size,
            page_token=page_token,
            max_pages=max_pages,
        ):
            yield from page.data_points

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()

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
                raise GoogleHealthAuthenticationError(
                    "Google Health credentials require reauthentication"
                )
            try:
                from google.auth.transport.requests import Request

                refresh(Request())
            except Exception:
                raise GoogleHealthAuthenticationError(
                    "Google Health credentials require reauthentication"
                ) from None
            token = getattr(credentials, "token", None)
        if not isinstance(token, str) or not token or any(ord(char) < 0x20 for char in token):
            raise GoogleHealthAuthenticationError(
                "Google Health credentials require reauthentication"
            )
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
        except AttributeError, TypeError, ValueError:
            raise GoogleHealthInvalidResponseError(
                "Google Health returned an invalid response"
            ) from None

        if status_code == 401:
            raise GoogleHealthAuthenticationError(
                "Google Health credentials require reauthentication"
            )
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
            if (
                content_length is not None
                and int(content_length) > GOOGLE_HEALTH_MAX_RESPONSE_BYTES
            ):
                raise ValueError
            payload = response.json()
        except Exception:
            raise GoogleHealthInvalidResponseError(
                "Google Health returned malformed JSON"
            ) from None
        if not isinstance(payload, dict):
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response")
        points_payload: object = payload.get("dataPoints", [])

        try:
            if not isinstance(points_payload, list) or len(points_payload) > page_size:
                raise ValueError
            next_page_token = _parse_response_page_token(payload)
            data_points = tuple(_parse_data_point(item) for item in points_payload)
            data_points = tuple(
                item
                for item in data_points
                if _matches_civil_bounds(item, civil_start_time, civil_end_time)
            )
        except GoogleHealthInvalidResponseError, ValueError, TypeError, KeyError:
            raise GoogleHealthInvalidResponseError(
                "Google Health returned an invalid Nutrition Log page"
            ) from None

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

    @staticmethod
    def _parse_data_points_response(
        response: GoogleHealthResponse,
        *,
        data_type: str,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> GoogleHealthDataPointPage:
        try:
            status_code = int(response.status_code)
        except (AttributeError, TypeError, ValueError):
            raise GoogleHealthInvalidResponseError(
                "Google Health returned an invalid response"
            ) from None
        if status_code == 401:
            raise GoogleHealthAuthenticationError(
                "Google Health credentials require reauthentication"
            )
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
            raise GoogleHealthInvalidResponseError(
                "Google Health returned malformed JSON"
            ) from None
        if not isinstance(payload, dict):
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response")
        points_payload = payload.get("dataPoints", [])
        try:
            if not isinstance(points_payload, list) or len(points_payload) > page_size:
                raise ValueError
            next_page_token = _parse_response_page_token(payload)
            if data_type == "nutrition-log":
                data_points: tuple[GoogleHealthDataPoint | NutritionLogDataPoint, ...] = tuple(
                    _parse_data_point(item) for item in points_payload
                )
            else:
                data_points = tuple(
                    _parse_scalar_data_point(item, data_type) for item in points_payload
                )
            data_points = tuple(
                item
                for item in data_points
                if _matches_physical_bounds(item, start_time, end_time)
            )
        except (GoogleHealthInvalidResponseError, TypeError, ValueError, KeyError):
            raise GoogleHealthInvalidResponseError(
                f"Google Health returned an invalid {data_type} page"
            ) from None
        return GoogleHealthDataPointPage(
            data_points=data_points,
            next_page_token=next_page_token,
            page_size=page_size,
            page_token=page_token,
            data_type=data_type,
            start_time=start_time,
            end_time=end_time,
        )


_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,9}))?(?:Z|[+-]\d{2}:\d{2})$"
)
_DURATION_RE = re.compile(r"^(-?)(\d+)(?:\.(\d{1,9}))?s$")
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
_WEIGHT_UNITS = {
    "WEIGHT_UNIT_UNSPECIFIED",
    "GRAM",
    "KILOGRAM",
    "OUNCE",
    "POUND",
    "STONE",
    "MILLIGRAM",
    "MICROGRAM",
    "NANOGRAM",
}
_ENERGY_UNITS = {
    "ENERGY_UNIT_UNSPECIFIED",
    "JOULE",
    "KILOJOULE",
    "KILOCALORIE",
    "SMALL_CALORIE",
    "CALORIE",
}
_NUTRIENTS = {
    "NUTRIENT_UNSPECIFIED",
    "BIOTIN",
    "CAFFEINE",
    "CALCIUM",
    "CHLORIDE",
    "CARBOHYDRATES",
    "CHOLESTEROL",
    "CHROMIUM",
    "COPPER",
    "DIETARY_FIBER",
    "FOLATE",
    "FOLIC_ACID",
    "IODINE",
    "IRON",
    "MAGNESIUM",
    "MANGANESE",
    "MOLYBDENUM",
    "MONOUNSATURATED_FAT",
    "NIACIN",
    "PANTOTHENIC_ACID",
    "PHOSPHORUS",
    "POLYUNSATURATED_FAT",
    "POTASSIUM",
    "PROTEIN",
    "RIBOFLAVIN",
    "SATURATED_FAT",
    "SELENIUM",
    "SODIUM",
    "SUGAR",
    "THIAMIN",
    "TRANS_FAT",
    "UNSATURATED_FAT",
    "VITAMIN_A",
    "VITAMIN_B12",
    "VITAMIN_B6",
    "VITAMIN_C",
    "VITAMIN_D",
    "VITAMIN_E",
    "VITAMIN_K",
    "ZINC",
}


_RESOURCE_NAME_RE = re.compile(
    r"^users/[A-Za-z0-9-]{1,63}/dataTypes/nutrition-log/dataPoints/[a-z0-9-]{4,63}$"
)
_FOOD_RESOURCE_NAME_RE = re.compile(
    r"^users/[A-Za-z0-9-]{1,63}/dataTypes/food/dataPoints/[a-z0-9-]{4,63}$"
)

_SCALAR_RESOURCE_NAME_RE = {
    data_type: re.compile(
        rf"^users/[A-Za-z0-9-]{{1,63}}/dataTypes/{re.escape(data_type)}/dataPoints/[a-z0-9-]{{4,63}}$"
    )
    for data_type in ("active-energy-burned", "weight")
}


def _parse_response_page_token(payload: Mapping[str, object]) -> str | None:
    if "nextPageToken" not in payload:
        return None
    token = payload["nextPageToken"]
    if token == "":
        return None
    if not isinstance(token, str):
        raise ValueError
    _validate_page_token(token)
    return token


def _parse_data_point(value: object) -> NutritionLogDataPoint:
    if not isinstance(value, dict) or "nutritionLog" not in value:
        raise ValueError
    name = value.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError
    if isinstance(name, str) and name and not _RESOURCE_NAME_RE.fullmatch(name):
        raise ValueError
    data_source_value = value.get("dataSource")
    data_source = _parse_data_source(data_source_value) if data_source_value is not None else None
    nutrition_log = value["nutritionLog"]
    if not isinstance(nutrition_log, dict):
        raise ValueError
    return NutritionLogDataPoint(
        name=name if isinstance(name, str) else None,
        nutrition_log=_parse_nutrition_log(nutrition_log),
        data_source=data_source,
    )


def _parse_scalar_data_point(value: object, data_type: str) -> GoogleHealthDataPoint:
    if not isinstance(value, dict):
        raise ValueError
    name = value.get("name")
    if not isinstance(name, str) or not _SCALAR_RESOURCE_NAME_RE[data_type].fullmatch(name):
        raise ValueError
    if data_type == "active-energy-burned":
        raw_value = value.get("activeEnergyBurned")
        if not isinstance(raw_value, dict):
            raise ValueError
        interval = raw_value.get("interval")
        if not isinstance(interval, dict):
            raise ValueError
        start = _parse_timestamp(interval.get("startTime"))
        end = _parse_timestamp(interval.get("endTime"))
        if _physical_key(start) >= _physical_key(end):
            raise ValueError
        start_time = start.value
        end_time = end.value
        start_offset = _parse_optional_duration(interval.get("startUtcOffset"))
        end_offset = _parse_optional_duration(interval.get("endUtcOffset"))
        scalar_key = "kcal"
        unit = "kcal"
        dto_type: type[GoogleHealthDataPoint] = ActiveEnergyBurnedDataPoint
    else:
        raw_value = value.get("weight")
        if not isinstance(raw_value, dict):
            raise ValueError
        sample_time = raw_value.get("sampleTime")
        if not isinstance(sample_time, dict):
            raise ValueError
        physical_time = _parse_timestamp(sample_time.get("physicalTime"))
        start_time = physical_time.value
        end_time = physical_time.value
        offset = _parse_optional_duration(sample_time.get("utcOffset"))
        start_offset = offset
        end_offset = offset
        scalar_key = "weightGrams"
        unit = "kilograms"
        dto_type = WeightDataPoint
    if scalar_key not in raw_value:
        raise ValueError
    number = _parse_nonnegative_number(raw_value[scalar_key])
    if data_type == "weight":
        number /= Decimal("1000")
    source_value = value.get("dataSource")
    source = _parse_data_source(source_value) if source_value is not None else None
    return dto_type(
        name=name,
        start_time=start_time,
        end_time=end_time,
        value=number,
        unit=unit,
        data_source=source,
        start_utc_offset=start_offset,
        end_utc_offset=end_offset,
    )


def _parse_optional_duration(value: object) -> str | None:
    if value is None:
        return None
    _parse_duration(value)
    return cast(str, value)


def _matches_physical_bounds(
    point: GoogleHealthDataPoint | NutritionLogDataPoint,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    if not isinstance(point, GoogleHealthDataPoint):
        return True
    if point.start_time.tzinfo is None:
        raise ValueError
    return (start is None or point.start_time >= start) and (
        end is None or point.start_time < end
    )


def _parse_data_source(value: object) -> NutritionDataSource:
    if not isinstance(value, dict):
        raise ValueError
    device_value = value.get("device")
    application_value = value.get("application")
    device = _parse_data_source_device(device_value) if device_value is not None else None
    application = (
        _parse_data_source_application(application_value) if application_value is not None else None
    )
    return NutritionDataSource(
        recording_method=_optional_bounded_string(value.get("recordingMethod")),
        platform=_optional_bounded_string(value.get("platform")),
        device=device,
        application=application,
    )


def _parse_data_source_device(value: object) -> NutritionDataSourceDevice:
    if not isinstance(value, dict):
        raise ValueError
    return NutritionDataSourceDevice(
        form_factor=_optional_bounded_string(value.get("formFactor")),
        manufacturer=_optional_bounded_string(value.get("manufacturer")),
        display_name=_optional_bounded_string(value.get("displayName")),
    )


def _parse_data_source_application(value: object) -> NutritionDataSourceApplication:
    if not isinstance(value, dict):
        raise ValueError
    return NutritionDataSourceApplication(
        package_name=_optional_bounded_string(value.get("packageName")),
        web_client_id=_optional_bounded_string(value.get("webClientId")),
        google_web_client_id=_optional_bounded_string(value.get("googleWebClientId")),
    )


def _bounded_string(value: object, *, max_bytes: int = 512) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError
    return value


def _optional_bounded_string(value: object, *, max_bytes: int = 512) -> str | None:
    return None if value is None else _bounded_string(value, max_bytes=max_bytes)


def _parse_nonnegative_number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError
    try:
        return decimal_value(value)
    except ValueError:
        raise ValueError from None


def _parse_nutrition_log(value: Mapping[str, object]) -> NutritionLog:
    if "interval" not in value:
        raise ValueError
    interval = value["interval"]
    if not isinstance(interval, dict):
        raise ValueError
    nutrients_value = value.get("nutrients", [])
    if not isinstance(nutrients_value, list):
        raise ValueError
    nutrients = tuple(_parse_nutrient(item) for item in nutrients_value)
    meal_type = _optional_bounded_string(value.get("mealType"))
    food = value.get("food")
    food_display_name = _optional_bounded_string(value.get("foodDisplayName"))
    if food is not None and (
        not isinstance(food, str) or not _FOOD_RESOURCE_NAME_RE.fullmatch(food)
    ):
        raise ValueError
    serving_value = value.get("serving")
    serving = _parse_serving(serving_value) if serving_value is not None else None
    return NutritionLog(
        interval=_parse_interval(interval),
        nutrients=nutrients,
        energy=_parse_quantity(value.get("energy"), "kcal", _ENERGY_UNITS)
        if "energy" in value
        else None,
        energy_from_fat=(
            _parse_quantity(value.get("energyFromFat"), "kcal", _ENERGY_UNITS)
            if "energyFromFat" in value
            else None
        ),
        total_carbohydrate=(
            _parse_quantity(value.get("totalCarbohydrate"), "grams", _WEIGHT_UNITS)
            if "totalCarbohydrate" in value
            else None
        ),
        total_fat=(
            _parse_quantity(value.get("totalFat"), "grams", _WEIGHT_UNITS)
            if "totalFat" in value
            else None
        ),
        meal_type=meal_type,
        serving=serving,
        food=food,
        food_display_name=food_display_name,
    )


def _parse_interval(value: Mapping[str, object]) -> NutritionLogInterval:
    required = {"startTime", "endTime", "startUtcOffset", "endUtcOffset"}
    if not required <= set(value):
        raise ValueError
    start_precise = _parse_timestamp(value["startTime"])
    end_precise = _parse_timestamp(value["endTime"])
    if _physical_key(start_precise) >= _physical_key(end_precise):
        raise ValueError
    start_time = start_precise.value
    end_time = end_precise.value
    start_offset_text = value["startUtcOffset"]
    end_offset_text = value["endUtcOffset"]
    start_offset = _parse_duration(start_offset_text)
    end_offset = _parse_duration(end_offset_text)
    civil_start = (
        _parse_civil_datetime(value["civilStartTime"]) if "civilStartTime" in value else None
    )
    civil_end = _parse_civil_datetime(value["civilEndTime"]) if "civilEndTime" in value else None
    if (
        civil_start is not None
        and _civil_key(civil_start) != _physical_key(start_precise) + start_offset
    ):
        raise ValueError
    if civil_end is not None and _civil_key(civil_end) != _physical_key(end_precise) + end_offset:
        raise ValueError
    return NutritionLogInterval(
        start_time=start_time,
        end_time=end_time,
        start_utc_offset=cast(str, start_offset_text),
        end_utc_offset=cast(str, end_offset_text),
        civil_start_time=civil_start.value if civil_start is not None else None,
        civil_end_time=civil_end.value if civil_end is not None else None,
    )


def _parse_duration(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError
    match = _DURATION_RE.fullmatch(value)
    if match is None:
        raise ValueError
    sign, whole, fraction = match.groups()
    nanos = int(whole) * 1_000_000_000
    if fraction:
        nanos += int(fraction.ljust(9, "0"))
    if sign:
        nanos = -nanos
    if abs(nanos) > 86_400 * 1_000_000_000:
        raise ValueError
    return nanos


def _parse_timestamp(value: object) -> _PreciseTime:
    if not isinstance(value, str):
        raise ValueError
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise ValueError
    fraction = match.group(1) or ""
    nanos = int(fraction.ljust(9, "0")) if fraction else 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError from None
    if parsed.tzinfo is None:
        raise ValueError
    return _PreciseTime(parsed.replace(microsecond=nanos // 1000), nanos)


def _physical_key(value: _PreciseTime) -> int:
    try:
        utc = value.value.astimezone(UTC).replace(microsecond=0, tzinfo=None)
    except OverflowError, ValueError:
        raise ValueError from None
    return (
        (utc.toordinal() - 1) * 86_400 + utc.hour * 3_600 + utc.minute * 60 + utc.second
    ) * 1_000_000_000 + value.nanos


def _civil_key(value: _PreciseTime) -> int:
    civil = value.value.replace(tzinfo=None, microsecond=0)
    return (
        (civil.toordinal() - 1) * 86_400 + civil.hour * 3_600 + civil.minute * 60 + civil.second
    ) * 1_000_000_000 + value.nanos


def _parse_civil_datetime(value: object) -> _PreciseTime:
    if not isinstance(value, dict) or "date" not in value:
        raise ValueError
    date_value = value["date"]
    if not isinstance(date_value, dict) or not {"year", "month", "day"} <= set(date_value):
        raise ValueError
    date_parts = tuple(date_value.get(key) for key in ("year", "month", "day"))
    if any(isinstance(item, bool) or not isinstance(item, int) for item in date_parts):
        raise ValueError
    year, month, day = cast(tuple[int, int, int], date_parts)
    if year < 1 or not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError
    time_value = value.get("time", {})
    if not isinstance(time_value, dict):
        raise ValueError
    hours = time_value.get("hours", 0)
    minutes = time_value.get("minutes", 0)
    seconds = time_value.get("seconds", 0)
    nanos = time_value.get("nanos", 0)
    if any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in (hours, minutes, seconds, nanos)
    ):
        raise ValueError
    if (
        not 0 <= hours <= 23
        or not 0 <= minutes <= 59
        or not 0 <= seconds <= 59
        or not 0 <= nanos <= 999_999_999
    ):
        raise ValueError
    try:
        return _PreciseTime(
            datetime(year, month, day, hours, minutes, seconds, nanos // 1000), nanos
        )
    except ValueError:
        raise ValueError from None


def _parse_quantity(
    value: object,
    scalar_key: str,
    units: set[str],
) -> NutritionQuantity:
    if not isinstance(value, dict) or scalar_key not in value:
        raise ValueError
    number_value = _parse_nonnegative_number(value[scalar_key])
    unit = _optional_bounded_string(value.get("userProvidedUnit"), max_bytes=64)
    if unit is not None and unit in (_WEIGHT_UNITS | _ENERGY_UNITS) and unit not in units:
        raise ValueError
    return NutritionQuantity(value=number_value, unit=unit)


def _parse_nutrient(value: object) -> NutritionNutrient:
    if not isinstance(value, dict) or not {"quantity", "nutrient"} <= set(value):
        raise ValueError
    nutrient = _bounded_string(value["nutrient"])
    quantity = value["quantity"]
    return NutritionNutrient(
        nutrient=nutrient, quantity=_parse_quantity(quantity, "grams", _WEIGHT_UNITS)
    )


def _parse_serving(value: object) -> NutritionServing:
    if not isinstance(value, dict) or not value:
        raise ValueError
    unit = _optional_bounded_string(value.get("foodMeasurementUnit"), max_bytes=64)
    display_name = _optional_bounded_string(value.get("foodMeasurementUnitDisplayName"))
    amount = value.get("amount")
    amount_value: Decimal | float | None = (
        None if amount is None else _parse_nonnegative_number(amount)
    )
    return NutritionServing(
        food_measurement_unit=unit,
        food_measurement_unit_display_name=display_name,
        amount=amount_value,
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
        return value.isoformat()
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


def _validate_page_budget(max_pages: int) -> None:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int):
        raise ValueError("max_pages must be an integer")
    if not 1 <= max_pages <= GOOGLE_HEALTH_MAX_PAGES:
        raise ValueError("max_pages is outside the allowed range")


def _validate_page_token(page_token: str | None) -> None:
    if page_token is None:
        return
    if not isinstance(page_token, str) or not page_token:
        raise ValueError("page_token is invalid")
    try:
        _bounded_string(page_token)
    except ValueError:
        raise ValueError("page_token is invalid") from None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in page_token):
        raise ValueError("page_token is invalid")


def _reject_physical_bounds(start_time: datetime | None, end_time: datetime | None) -> None:
    if start_time is not None or end_time is not None:
        raise ValueError("physical time bounds are unsupported; use civil time bounds")


def _validate_physical_bounds(
    start_time: datetime | None, end_time: datetime | None
) -> None:
    for value in (start_time, end_time):
        if value is not None and (
            not isinstance(value, datetime) or value.tzinfo is None
        ):
            raise ValueError("physical time bounds must be timezone-aware datetimes")
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise ValueError("start_time must be before end_time")


def _physical_time_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("physical time bounds must be timezone-aware datetimes")
    return value.isoformat().replace("+00:00", "Z")

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
    except AttributeError, TypeError, ValueError:
        return 0
    return max(0, min(value, 300))


__all__ = [
    "GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH",
    "GOOGLE_HEALTH_API_BASE_URL",
    "GOOGLE_HEALTH_MAX_PAGES",
    "GOOGLE_HEALTH_MAX_PAGE_SIZE",
    "GOOGLE_HEALTH_NUTRITION_LOG_PATH",
    "GOOGLE_HEALTH_WEIGHT_PATH",
    "ActiveEnergyBurnedDataPoint",
    "GoogleHealthActivityDataPoint",
    "GoogleHealthAuthenticationError",
    "GoogleHealthClient",
    "GoogleHealthClientError",
    "GoogleHealthDataPoint",
    "GoogleHealthDataPointPage",
    "GoogleHealthHTTPTransport",
    "GoogleHealthInvalidResponseError",
    "GoogleHealthProviderUnavailableError",
    "GoogleHealthRateLimitedError",
    "GoogleHealthScopeError",
    "GoogleHealthTransientError",
    "GoogleHealthWeightDataPoint",
    "NutritionDataSource",
    "NutritionDataSourceApplication",
    "NutritionDataSourceDevice",
    "NutritionLog",
    "NutritionLogDataPoint",
    "NutritionLogInterval",
    "NutritionLogPage",
    "NutritionLogPageAggregate",
    "NutritionNutrient",
    "NutritionQuantity",
    "NutritionServing",
    "WeightDataPoint",
]
