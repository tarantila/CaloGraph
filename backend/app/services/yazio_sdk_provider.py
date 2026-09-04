"""YAZIO community SDK v22 provider.

This module is the only CaloGraph module that imports generated ``yazio_sdk``
models and endpoint functions.  Calls are intentionally sequential and use
only detailed generated operations so status and headers remain available for
safe classification by the transport worker.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import date, timedelta
from decimal import Decimal
from functools import partial
from typing import Any, cast

import httpx
from yazio_sdk import AuthenticatedClient, Client  # type: ignore[import-untyped]
from yazio_sdk.api.authentication import create_token  # type: ignore[import-untyped]
from yazio_sdk.api.diary import get_daily_nutrients  # type: ignore[import-untyped]
from yazio_sdk.api.widgets import get_daily_summary_widget  # type: ignore[import-untyped]
from yazio_sdk.models import OAuthTokenRequest  # type: ignore[import-untyped]
from yazio_sdk.types import UNSET  # type: ignore[import-untyped]

from app.config import settings
from app.services.yazio_provider import (
    ProviderMode,
    YazioProviderAuthenticationError,
    YazioProviderInvalidResponseError,
    YazioProviderMetadata,
    YazioProviderNetworkTimeoutError,
    YazioProviderRateLimitedError,
    YazioProviderResult,
    YazioProviderUnavailableError,
    YazioProviderVersionBlockedError,
)

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


def _raise_for_status(response: object, *, authentication: bool = False) -> None:
    _response_content_is_bounded(response)
    status = _status(response)
    if status == 200:
        return
    if status == 429:
        raise YazioProviderRateLimitedError(_retry_after(response))
    if authentication and status in {400, 401, 403}:
        raise YazioProviderAuthenticationError
    if not authentication and status == 401:
        raise YazioProviderAuthenticationError
    if not authentication and status == 403:
        if _response_error_code(response) == "version_blocked":
            raise YazioProviderVersionBlockedError
        raise YazioProviderAuthenticationError
    if status >= 500:
        raise YazioProviderUnavailableError
    if 300 <= status < 400:
        raise YazioProviderUnavailableError
    raise YazioProviderInvalidResponseError


def _call_detailed[T](
    call: Callable[..., T],
    *,
    authentication: bool = False,
    **kwargs: Any,
) -> T:
    try:
        response = call(**kwargs)
    except (YazioProviderAuthenticationError, YazioProviderVersionBlockedError):
        raise
    except _ResponseTooLargeError as exc:
        raise YazioProviderInvalidResponseError from exc
    except httpx.TimeoutException as exc:
        raise YazioProviderNetworkTimeoutError from exc
    except httpx.RequestError as exc:
        raise YazioProviderUnavailableError from exc
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise YazioProviderInvalidResponseError from exc
    _raise_for_status(response, authentication=authentication)
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


def _token_from_response(response: object) -> str:
    parsed = getattr(response, "parsed", _MISSING)
    if parsed is None or parsed is _MISSING:
        raise YazioProviderInvalidResponseError
    token = _field(parsed, "access_token")
    if token is _MISSING or not isinstance(token, str) or not token:
        raise YazioProviderInvalidResponseError
    if len(token.encode("utf-8")) > MAX_TOKEN_BYTES or "\x00" in token:
        raise YazioProviderInvalidResponseError
    return token


def _daily_items(response: object) -> list[object]:
    parsed = getattr(response, "parsed", _MISSING)
    if parsed is _MISSING or not isinstance(parsed, list):
        raise YazioProviderInvalidResponseError
    return parsed


def _widget_activity(
    client: AuthenticatedClient, item_day: date
) -> tuple[date, float | None]:
    response = _call_detailed(
        get_daily_summary_widget.sync_detailed,
        client=client,
        date=item_day.isoformat(),
    )
    widget = getattr(response, "parsed", _MISSING)
    if widget is None or widget is _MISSING:
        raise YazioProviderInvalidResponseError
    return item_day, _numeric(_field(widget, "activity_energy"))


class YazioSdkProvider:
    """Map safe v22 SDK responses into the legacy parser envelope."""

    mode: ProviderMode = "sdk"

    def validate_credentials(self, email: str, password: str) -> None:
        client = _new_client()
        try:
            response = _call_detailed(
                create_token.sync_detailed,
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
            _token_from_response(response)
        finally:
            _close_client(client)

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
            token = _token_from_response(token_response)
        finally:
            _close_client(client)

        authenticated = _new_authenticated_client(token)
        try:
            daily_response = _call_detailed(
                get_daily_nutrients.sync_detailed,
                client=authenticated,
                start=start_day.isoformat(),
                end=end_day.isoformat(),
            )
            days: dict[str, dict[str, float]] = {
                (start_day + timedelta(days=offset)).isoformat(): {}
                for offset in range((end_day - start_day).days + 1)
            }
            seen: set[date] = set()
            for item in _daily_items(daily_response):
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

            # The aggregate endpoint has no activity-energy field.  Widgets
            # use the existing legacy request-worker cap, never unbounded
            # parallelism, so long historical chunks remain within the parent
            # operation deadline under normal latency.
            requested_days = [
                start_day + timedelta(days=offset)
                for offset in range((end_day - start_day).days + 1)
            ]
            with ThreadPoolExecutor(max_workers=settings.yazio_request_workers) as executor:
                for item_day, activity in executor.map(
                    partial(_widget_activity, authenticated), requested_days
                ):
                    if activity is not None:
                        days[item_day.isoformat()]["activity_energy"] = activity

            return YazioProviderResult(
                payload={"days": days},
                metadata=YazioProviderMetadata(
                    micronutrient_complete=False,
                    provider_mode="sdk",
                ),
            )
        finally:
            _close_client(authenticated)
