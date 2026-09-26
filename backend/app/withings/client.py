from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, cast

from app.withings.constants import (
    WITHINGS_ACTIVITY_URL,
    WITHINGS_MAX_PAGE_SIZE,
    WITHINGS_MAX_PAGES,
    WITHINGS_MAX_RECORDS,
    WITHINGS_MEASURE_URL,
)
from app.withings.errors import (
    WithingsAuthenticationError,
    WithingsInvalidResponseError,
    WithingsProviderUnavailableError,
    WithingsRateLimitedError,
    WithingsTransientError,
)
from app.withings.parsers import (
    ActivityPage,
    MeasurePage,
    parse_activity_response,
    parse_measure_response,
)

WITHINGS_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT = 3.05
DEFAULT_READ_TIMEOUT = 15.0


class WithingsHTTPResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_bytes(self) -> Iterator[bytes]: ...
    def json(self) -> object: ...


class WithingsHTTPClientAdapter(Protocol):
    def stream(
        self, method: str, url: str, **kwargs: object
    ) -> AbstractContextManager[WithingsHTTPResponse]: ...

    def close(self) -> None: ...


class WithingsTransport(Protocol):
    def get_measure(
        self, *, access_token: str, startdate: int, enddate: int, offset: int = 0
    ) -> WithingsHTTPResponse: ...

    def get_activity(
        self,
        *,
        access_token: str,
        startdateymd: date,
        enddateymd: date,
        offset: int = 0,
    ) -> WithingsHTTPResponse: ...

    def close(self) -> None: ...


# Alias used by callers that want to inject a transport without depending on the
# concrete HTTP implementation.
WithingsHTTPTransportProtocol = WithingsTransport


@dataclass(slots=True)
class BufferedWithingsResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.content

    def json(self) -> object:
        try:
            return json.loads(self.content)
        except TypeError, ValueError:
            raise WithingsInvalidResponseError("Withings response is not valid JSON") from None


def _safe_credential(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 0x20 for char in value):
        raise WithingsAuthenticationError("Withings credentials are unavailable")
    return value


def _safe_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} is invalid")
    return value


def _timeout(connect_timeout: float, read_timeout: float) -> Any:
    try:
        import httpx
    except ImportError:
        return _FallbackTimeout(connect_timeout, read_timeout)
    return httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )


@dataclass(frozen=True, slots=True)
class _FallbackTimeout:
    connect: float
    read: float
    write: float | None = None
    pool: float | None = None


class WithingsHTTPTransport:
    """Narrow fixed-host POST transport for the Withings endpoints."""

    def __init__(
        self,
        *,
        http_client: WithingsHTTPClientAdapter | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_response_bytes: int = WITHINGS_MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            isinstance(connect_timeout, bool)
            or not isinstance(connect_timeout, (int, float))
            or connect_timeout <= 0
            or isinstance(read_timeout, bool)
            or not isinstance(read_timeout, (int, float))
            or read_timeout <= 0
        ):
            raise ValueError("Withings timeouts must be positive")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError("Withings response limit must be positive")
        self._timeout = _timeout(float(connect_timeout), float(read_timeout))
        self._max_response_bytes = max_response_bytes
        if http_client is None:
            try:
                import httpx
            except ImportError:
                raise WithingsProviderUnavailableError(
                    "Withings HTTP transport is unavailable"
                ) from None
            http_client = cast(
                WithingsHTTPClientAdapter,
                httpx.Client(timeout=self._timeout, follow_redirects=False),
            )
        self._http_client = http_client

    def _post(
        self,
        url: str,
        data: Mapping[str, str],
        *,
        access_token: str | None = None,
    ) -> WithingsHTTPResponse:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {_safe_credential(access_token, 'access token')}"
        try:
            with self._http_client.stream(
                "POST",
                url,
                data=dict(data),
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                status_code = response.status_code
                if not isinstance(status_code, int) or not 100 <= status_code <= 599:
                    raise WithingsInvalidResponseError("Withings response status is invalid")
                if status_code == 401:
                    raise WithingsAuthenticationError(upstream_status_code=status_code)
                if status_code == 429:
                    raise WithingsRateLimitedError(upstream_status_code=status_code)
                if status_code >= 500:
                    raise WithingsProviderUnavailableError(upstream_status_code=status_code)
                if not 200 <= status_code <= 299:
                    raise WithingsInvalidResponseError(upstream_status_code=status_code)
                body = bytearray()
                for chunk in response.iter_bytes():
                    if (
                        not isinstance(chunk, bytes)
                        or len(body) + len(chunk) > self._max_response_bytes
                    ):
                        raise WithingsInvalidResponseError(
                            "Withings response is too large"
                        ) from None
                    body.extend(chunk)
                return BufferedWithingsResponse(
                    status_code, dict(response.headers or {}), bytes(body)
                )
        except (
            WithingsAuthenticationError,
            WithingsRateLimitedError,
            WithingsProviderUnavailableError,
            WithingsInvalidResponseError,
        ):
            raise
        except Exception:
            raise WithingsTransientError("Withings request failed") from None

    def get_measure(
        self, *, access_token: str, startdate: int, enddate: int, offset: int = 0
    ) -> WithingsHTTPResponse:
        return self._post(
            WITHINGS_MEASURE_URL,
            {
                "action": "getmeas",
                "startdate": str(_safe_int(startdate, "startdate")),
                "enddate": str(_safe_int(enddate, "enddate")),
                "offset": str(_safe_int(offset, "offset")),
            },
            access_token=access_token,
        )

    def get_activity(
        self,
        *,
        access_token: str,
        startdateymd: date,
        enddateymd: date,
        offset: int = 0,
    ) -> WithingsHTTPResponse:
        if not isinstance(startdateymd, date) or isinstance(startdateymd, datetime):
            raise ValueError("startdateymd is invalid")
        if not isinstance(enddateymd, date) or isinstance(enddateymd, datetime):
            raise ValueError("enddateymd is invalid")
        return self._post(
            WITHINGS_ACTIVITY_URL,
            {
                "action": "getactivity",
                "startdateymd": startdateymd.isoformat(),
                "enddateymd": enddateymd.isoformat(),
                "data_fields": "calories",
                "offset": str(_safe_int(offset, "offset")),
            },
            access_token=access_token,
        )

    def close(self) -> None:
        self._http_client.close()


@dataclass(frozen=True, slots=True)
class WithingsPageResult:
    measure_pages: tuple[MeasurePage, ...] = ()
    activity_pages: tuple[ActivityPage, ...] = ()
    pages_read: int = 0
    records_read: int = 0
    truncated: bool = False


class WithingsHTTPClient:
    """Bounded parser-aware client over an injectable Withings transport."""

    def __init__(
        self,
        *,
        transport: WithingsTransport | None = None,
        max_pages: int = WITHINGS_MAX_PAGES,
        max_records: int = WITHINGS_MAX_RECORDS,
        page_size: int = WITHINGS_MAX_PAGE_SIZE,
    ) -> None:
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= WITHINGS_MAX_PAGES
        ):
            raise ValueError("Withings max_pages is outside the allowed range")
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or not 1 <= max_records <= WITHINGS_MAX_RECORDS
        ):
            raise ValueError("Withings max_records is outside the allowed range")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= WITHINGS_MAX_PAGE_SIZE
        ):
            raise ValueError("Withings page_size is outside the allowed range")
        self._transport = transport or WithingsHTTPTransport()
        self._max_pages = max_pages
        self._max_records = max_records
        self._page_size = page_size

    def fetch_measure_pages(
        self, *, access_token: str, startdate: int, enddate: int
    ) -> WithingsPageResult:
        pages: list[MeasurePage] = []
        offset = 0
        seen_offsets: set[int] = set()
        records = 0
        while len(pages) < self._max_pages:
            if offset in seen_offsets:
                raise WithingsInvalidResponseError("Withings pagination did not progress")
            seen_offsets.add(offset)
            page = parse_measure_response(
                self._transport.get_measure(
                    access_token=access_token,
                    startdate=startdate,
                    enddate=enddate,
                    offset=offset,
                ),
                requested_offset=offset,
            )
            pages.append(page)
            records += len(page.groups)
            if records > self._max_records or not page.more:
                if records > self._max_records:
                    raise WithingsInvalidResponseError("Withings record limit exceeded")
                return WithingsPageResult(tuple(pages), pages_read=len(pages), records_read=records)
            next_offset = page.next_offset
            if next_offset is None or next_offset <= offset:
                raise WithingsInvalidResponseError("Withings pagination did not progress")
            offset = next_offset
        raise WithingsInvalidResponseError("Withings page limit exceeded")

    def fetch_activity_pages(
        self, *, access_token: str, startdateymd: date, enddateymd: date
    ) -> WithingsPageResult:
        pages: list[ActivityPage] = []
        offset = 0
        seen_offsets: set[int] = set()
        records = 0
        while len(pages) < self._max_pages:
            if offset in seen_offsets:
                raise WithingsInvalidResponseError("Withings pagination did not progress")
            seen_offsets.add(offset)
            page = parse_activity_response(
                self._transport.get_activity(
                    access_token=access_token,
                    startdateymd=startdateymd,
                    enddateymd=enddateymd,
                    offset=offset,
                )
            )
            pages.append(page)
            records += len(page.activities)
            if records > self._max_records or not page.more:
                if records > self._max_records:
                    raise WithingsInvalidResponseError("Withings record limit exceeded")
                return WithingsPageResult(
                    activity_pages=tuple(pages), pages_read=len(pages), records_read=records
                )
            next_offset = page.next_offset
            if next_offset is None or next_offset <= offset:
                raise WithingsInvalidResponseError("Withings pagination did not progress")
            offset = next_offset
        raise WithingsInvalidResponseError("Withings page limit exceeded")

    def close(self) -> None:
        self._transport.close()


__all__ = [
    "BufferedWithingsResponse",
    "WithingsHTTPClient",
    "WithingsHTTPClientAdapter",
    "WithingsHTTPResponse",
    "WithingsHTTPTransport",
    "WithingsHTTPTransportProtocol",
    "WithingsPageResult",
    "WithingsTransport",
]
