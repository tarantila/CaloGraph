from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, Protocol, cast

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

GOOGLE_HEALTH_NUTRITION_LOG_PATH = "/users/me/nutritionLog"
GOOGLE_HEALTH_MAX_PAGE_SIZE = 100
GOOGLE_HEALTH_MAX_PAGE_TOKEN_LENGTH = 512
GOOGLE_HEALTH_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NutritionLogPage:
    """The unparsed page envelope consumed by the later Nutrition DTO task."""

    payload: Mapping[str, object]


class GoogleHealthResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> object: ...


class GoogleHealthTransport(Protocol):
    def get_nutrition_log(
        self,
        *,
        access_token: str,
        page_size: int,
        page_token: str | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> GoogleHealthResponse: ...


class _HTTPClient(Protocol):
    def get(self, url: str, **kwargs: object) -> GoogleHealthResponse: ...

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
    ) -> GoogleHealthResponse:
        """Perform exactly one GET against the fixed Nutrition Log resource."""
        _validate_page_size(page_size)
        _validate_page_token(page_token)
        _validate_time_bounds(start_time, end_time)
        if not access_token or any(ord(char) < 0x20 for char in access_token):
            raise GoogleHealthAuthenticationError("Google Health credentials are unavailable")

        params: dict[str, str] = {"pageSize": str(page_size)}
        if page_token is not None:
            params["pageToken"] = page_token
        if start_time is not None:
            params["startTime"] = start_time.isoformat()
        if end_time is not None:
            params["endTime"] = end_time.isoformat()

        # The path is a module constant and is never accepted from a caller.
        url = f"{GOOGLE_HEALTH_API_BASE_URL}{GOOGLE_HEALTH_NUTRITION_LOG_PATH}"
        return self._http_client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self._timeout,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http_client.close()


class GoogleHealthClient:
    """Read-only Google Health client with in-memory credentials only."""

    def __init__(
        self,
        transport: GoogleHealthTransport | None,
        credentials: object,
    ) -> None:
        self._transport = transport or GoogleHealthHTTPTransport()
        self._credentials = credentials

    def get_nutrition_log_page(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> NutritionLogPage:
        _validate_page_size(page_size)
        _validate_page_token(page_token)
        _validate_time_bounds(start_time, end_time)
        access_token = self._access_token()
        try:
            response = self._transport.get_nutrition_log(
                access_token=access_token,
                page_size=page_size,
                page_token=page_token,
                start_time=start_time,
                end_time=end_time,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError, TimeoutError, ConnectionError, OSError) as exc:
            raise GoogleHealthTransientError("Google Health transport failed temporarily") from exc
        return self._parse_response(response)

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
            except Exception as exc:
                raise GoogleHealthAuthenticationError(
                    "Google Health credentials require reauthentication"
                ) from exc
            token = getattr(credentials, "token", None)
        if not isinstance(token, str) or not token or any(ord(char) < 0x20 for char in token):
            raise GoogleHealthAuthenticationError("Google Health credentials require reauthentication")
        return token

    @staticmethod
    def _parse_response(response: GoogleHealthResponse) -> NutritionLogPage:
        try:
            status_code = int(response.status_code)
        except (AttributeError, TypeError, ValueError) as exc:
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response") from exc

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
        except Exception as exc:
            raise GoogleHealthInvalidResponseError("Google Health returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise GoogleHealthInvalidResponseError("Google Health returned an invalid response")
        return NutritionLogPage(MappingProxyType(cast(dict[str, object], payload)))


def _validate_page_size(page_size: int) -> None:
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise ValueError("page_size must be an integer")
    if not 1 <= page_size <= GOOGLE_HEALTH_MAX_PAGE_SIZE:
        raise ValueError("page_size is outside the allowed range")


def _validate_page_token(page_token: str | None) -> None:
    if page_token is None:
        return
    if not isinstance(page_token, str) or not page_token or len(page_token) > GOOGLE_HEALTH_MAX_PAGE_TOKEN_LENGTH:
        raise ValueError("page_token is invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in page_token):
        raise ValueError("page_token is invalid")


def _validate_time_bounds(start_time: datetime | None, end_time: datetime | None) -> None:
    for value in (start_time, end_time):
        if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
            raise ValueError("time bounds must be timezone-aware datetimes")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise ValueError("start_time must not be after end_time")


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
    "NutritionLogPage",
]
