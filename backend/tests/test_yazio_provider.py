from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import pytest

from app.config import settings
from app.services import yazio_sdk_provider
from app.services.yazio_provider import (
    YazioProviderAuthenticationError,
    YazioProviderInvalidResponseError,
    YazioProviderNetworkTimeoutError,
    YazioProviderRateLimitedError,
    YazioProviderUnavailableError,
    YazioProviderVersionBlockedError,
)


@dataclass
class _Response:
    status_code: int = 200
    parsed: Any = None
    headers: dict[str, str] | None = None
    content: bytes = b"{}"

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


class _Stream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"12"
        yield b"34"

    def close(self) -> None:
        pass


class _Client:
    def __init__(self) -> None:
        self.closed = False
        self.kwargs: dict[str, Any] = {}

    def get_httpx_client(self) -> _Client:
        return self

    def close(self) -> None:
        self.closed = True


def _patch_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[_Client] = []

    def new_client() -> _Client:
        client = _Client()
        clients.append(client)
        return client

    monkeypatch.setattr(yazio_sdk_provider, "_new_client", new_client)
    monkeypatch.setattr(
        yazio_sdk_provider, "_new_authenticated_client", lambda _token: new_client()
    )

    def token(**_: Any) -> _Response:
        return _Response(parsed={"access_token": "offline-token"})

    monkeypatch.setattr(yazio_sdk_provider.create_token, "sync_detailed", token)


def test_sdk_maps_aggregate_and_activity_with_requested_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch)
    daily_calls: list[dict[str, Any]] = []
    widget_calls: list[dict[str, Any]] = []

    def daily(**kwargs: Any) -> _Response:
        daily_calls.append(kwargs)
        return _Response(
            parsed=[
                {"date": "2026-08-01", "energy": 1_900, "protein": 120, "carb": 220, "fat": 60},
                {"date": "2026-08-03", "energy": 2_000, "protein": 100, "carb": 240, "fat": 70},
            ]
        )

    active_requests = 0
    max_active_requests = 0

    def widget(**kwargs: Any) -> _Response:
        nonlocal active_requests, max_active_requests
        widget_calls.append(kwargs)
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        try:
            time.sleep(0.005)
            value = 300 if kwargs["date"] == "2026-08-01" else None
            return _Response(parsed={"activity_energy": value})
        finally:
            active_requests -= 1

    monkeypatch.setattr(yazio_sdk_provider.get_daily_nutrients, "sync_detailed", daily)
    monkeypatch.setattr(yazio_sdk_provider.get_daily_summary_widget, "sync_detailed", widget)

    result = yazio_sdk_provider.YazioSdkProvider().fetch(
        "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 3), True
    )

    assert result.payload == {
        "days": {
            "2026-08-01": {
                "energy": 1900.0,
                "protein": 120.0,
                "carb": 220.0,
                "fat": 60.0,
                "activity_energy": 300.0,
            },
            "2026-08-02": {},
            "2026-08-03": {
                "energy": 2000.0,
                "protein": 100.0,
                "carb": 240.0,
                "fat": 70.0,
            },
        }
    }
    assert result.metadata.micronutrient_complete is False
    assert daily_calls[0]["start"] == "2026-08-01"
    assert daily_calls[0]["end"] == "2026-08-03"
    assert sorted(call["date"] for call in widget_calls) == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]
    assert 1 < max_active_requests <= settings.yazio_request_workers


def test_sdk_omits_missing_and_null_values_and_supports_empty_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_clients(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(parsed=[]),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_summary_widget,
        "sync_detailed",
        lambda **_: _Response(parsed={"activity_energy": None}),
    )

    result = yazio_sdk_provider.YazioSdkProvider().fetch(
        "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 2), False
    )

    assert result.payload == {"days": {"2026-08-01": {}, "2026-08-02": {}}}


def test_sdk_rejects_out_of_range_dates_and_invalid_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch)
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(parsed=[{"date": "2026-08-03", "energy": 10}]),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_summary_widget,
        "sync_detailed",
        lambda **_: _Response(parsed={}),
    )

    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 2), False
        )

    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(parsed=[{"date": "2026-08-01", "energy": -1}]),
    )
    with pytest.raises(YazioProviderInvalidResponseError):
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 1), False
        )


def test_sdk_classifies_statuses_and_bounds_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch)

    for status_code in (400, 401, 403):
        monkeypatch.setattr(
            yazio_sdk_provider.create_token,
            "sync_detailed",
            lambda status_code=status_code, **_: _Response(status_code=status_code),
        )
        with pytest.raises(YazioProviderAuthenticationError):
            yazio_sdk_provider.YazioSdkProvider().validate_credentials(
                "owner@example.com", "private-password"
            )

    monkeypatch.setattr(
        yazio_sdk_provider.create_token,
        "sync_detailed",
        lambda **_: _Response(parsed={"access_token": "offline-token"}),
    )
    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(
            status_code=403, content=b'{"error":"version_blocked"}'
        ),
    )
    with pytest.raises(YazioProviderVersionBlockedError):
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 1), False
        )

    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(status_code=403),
    )
    with pytest.raises(YazioProviderAuthenticationError):
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 1), False
        )


    monkeypatch.setattr(
        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(status_code=429, headers={"Retry-After": "999999"}),
    )
    with pytest.raises(YazioProviderRateLimitedError) as error:
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 1), False
        )
    assert error.value.retry_after == 3600

    monkeypatch.setattr(

        yazio_sdk_provider.get_daily_nutrients,
        "sync_detailed",
        lambda **_: _Response(status_code=503),
    )
    with pytest.raises(YazioProviderUnavailableError):
        yazio_sdk_provider.YazioSdkProvider().fetch(
            "owner@example.com", "private-password", date(2026, 8, 1), date(2026, 8, 1), False
        )

def test_sdk_bounds_streamed_response_before_json_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yazio_sdk_provider, "MAX_PROVIDER_RESPONSE_BYTES", 3)
    with pytest.raises(yazio_sdk_provider._ResponseTooLargeError):
        list(yazio_sdk_provider._BoundedResponseStream(_Stream()))


def test_sdk_translates_network_timeout_and_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch)

    def timeout(**_: Any) -> _Response:
        raise httpx.ReadTimeout("private-password must not leak")

    monkeypatch.setattr(yazio_sdk_provider.create_token, "sync_detailed", timeout)
    with pytest.raises(YazioProviderNetworkTimeoutError) as error:
        yazio_sdk_provider.YazioSdkProvider().validate_credentials(
            "owner@example.com", "private-password"
        )
    assert "private-password" not in str(error.value)

    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "private-app-secret")
    assert "private-app-secret" not in repr(settings)
