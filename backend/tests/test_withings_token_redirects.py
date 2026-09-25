from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import BaseHandler

import pytest
import requests
from app.config import settings
from app.models import WithingsConnection
from app.services.credential_crypto import encrypt_credential
from app.withings.constants import WITHINGS_TOKEN_URL
from app.withings.errors import WithingsTokenExchangeError
from app.withings.service import complete_withings_oauth, start_withings_oauth
from app.withings.token_service import _WithingsOAuthAdapter


_UNTRUSTED_LOCATION = "https://untrusted.example.test/collect"
_SENTINELS = (
    "sentinel-code",
    "sentinel-secret",
    "sentinel-upstream-body",
    "sentinel-upstream-text",
    _UNTRUSTED_LOCATION,
)


class _MemoryResponse(BytesIO):
    def __init__(self, status: int, body: bytes, url: str, location: str | None = None):
        super().__init__(body)
        self.status = status
        self.code = status
        self.msg = "sentinel-upstream-text"
        self.url = url
        self.headers = Message()
        if location is not None:
            self.headers["Location"] = location
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return super().read(size)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def info(self) -> Message:
        return self.headers


class _MemoryTransport(BaseHandler):
    handler_order = 100

    def __init__(self, replies: dict[str, tuple[int, bytes, str | None]]):
        self.replies = replies
        self.seen = []

    def https_open(self, request):
        self.seen.append(request)
        status, body, location = self.replies[request.full_url]
        return _MemoryResponse(status, body, request.full_url, location)


class _RecordingOpener:
    def __init__(self, opener, calls):
        self.opener = opener
        self.calls = calls

    def open(self, request, *, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout))
        return self.opener.open(request, timeout=timeout)


def _install_memory_transport(monkeypatch, replies):
    import urllib.request

    transport = _MemoryTransport(replies)
    calls = []
    build_opener = urllib.request.build_opener

    def build_memory_opener(*handlers):
        return _RecordingOpener(build_opener(transport, *handlers), calls)

    monkeypatch.setattr(urllib.request, "build_opener", build_memory_opener)
    return transport, calls


def _block_requests_transport(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("the token exchange attempted the requests transport")

    monkeypatch.setattr(requests, "post", blocked)


def _payload_response() -> bytes:
    return json.dumps(
        {"status": 0, "body": {"access_token": "sentinel-access-token"}}
    ).encode()


@pytest.mark.parametrize("status", [302, 307, 308])
def test_token_redirect_rejects_redirect_without_contacting_location(monkeypatch, status):
    _block_requests_transport(monkeypatch)
    transport, calls = _install_memory_transport(
        monkeypatch,
        {
            WITHINGS_TOKEN_URL: (status, b"sentinel-upstream-body", _UNTRUSTED_LOCATION),
            _UNTRUSTED_LOCATION: (200, _payload_response(), None),
        },
    )

    with pytest.raises(WithingsTokenExchangeError) as caught:
        _WithingsOAuthAdapter().exchange(
            code="sentinel-code",
            redirect_uri="https://example.test/withings/callback",
            client_id="synthetic-client-id",
            client_secret="sentinel-secret",
        )

    assert caught.value.code == "invalid_response"
    assert str(caught.value) == "invalid_response"
    assert caught.value.__cause__ is None
    assert [(request.full_url, request.get_method()) for request in transport.seen] == [
        (WITHINGS_TOKEN_URL, "POST")
    ]
    assert calls == [(WITHINGS_TOKEN_URL, "POST", 10)]
    posted_fields = parse_qs(transport.seen[0].data.decode())
    assert posted_fields == {
        "action": ["requesttoken"],
        "grant_type": ["authorization_code"],
        "code": ["sentinel-code"],
        "redirect_uri": ["https://example.test/withings/callback"],
        "client_id": ["synthetic-client-id"],
        "client_secret": ["sentinel-secret"],
    }
    assert not any(value in repr(caught.value) for value in _SENTINELS)


def test_token_exchange_accepts_json_200_once(monkeypatch):
    _block_requests_transport(monkeypatch)
    transport, calls = _install_memory_transport(
        monkeypatch,
        {WITHINGS_TOKEN_URL: (200, _payload_response(), None)},
    )

    result = _WithingsOAuthAdapter().exchange(
        code="sentinel-code",
        redirect_uri="https://example.test/withings/callback",
        client_id="synthetic-client-id",
        client_secret="sentinel-secret",
    )

    assert result == {"access_token": "sentinel-access-token"}
    assert [(request.full_url, request.get_method()) for request in transport.seen] == [
        (WITHINGS_TOKEN_URL, "POST")
    ]
    assert calls == [(WITHINGS_TOKEN_URL, "POST", 10)]

@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (302, "invalid_response"),
        (403, "scope_missing"),
        (429, "rate_limited"),
        (500, "provider_error"),
    ],
)
def test_token_exchange_translates_http_errors_safely(
    monkeypatch, status, expected_code
):
    _block_requests_transport(monkeypatch)
    response = _MemoryResponse(
        200, b"sentinel-upstream-body", WITHINGS_TOKEN_URL
    )
    http_error = HTTPError(
        WITHINGS_TOKEN_URL,
        status,
        "sentinel-upstream-text",
        Message(),
        response,
    )
    calls = []

    class DirectOpener:
        def open(self, request, *, timeout):
            calls.append((request.full_url, request.get_method(), timeout))
            raise http_error

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: DirectOpener())

    with pytest.raises(WithingsTokenExchangeError) as caught:
        _WithingsOAuthAdapter().exchange(
            code="sentinel-code",
            redirect_uri="https://example.test/withings/callback",
            client_id="synthetic-client-id",
            client_secret="sentinel-secret",
        )

    assert caught.value.code == expected_code
    assert str(caught.value) == expected_code
    assert caught.value.__cause__ is None
    assert response.closed
    assert response.read_calls == 0
    assert calls == [(WITHINGS_TOKEN_URL, "POST", 10)]
    assert not any(value in repr(caught.value) for value in _SENTINELS)


def test_token_exchange_rejects_direct_3xx_without_reading_response(monkeypatch):
    _block_requests_transport(monkeypatch)
    response = _MemoryResponse(
        307,
        b"sentinel-upstream-body",
        WITHINGS_TOKEN_URL,
        _UNTRUSTED_LOCATION,
    )
    calls = []

    class DirectOpener:
        def open(self, request, *, timeout):
            calls.append((request.full_url, request.get_method(), timeout))
            return response

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: DirectOpener())

    with pytest.raises(WithingsTokenExchangeError) as caught:
        _WithingsOAuthAdapter().exchange(
            code="sentinel-code",
            redirect_uri="https://example.test/withings/callback",
            client_id="synthetic-client-id",
            client_secret="sentinel-secret",
        )

    assert caught.value.code == "invalid_response"
    assert calls == [(WITHINGS_TOKEN_URL, "POST", 10)]
    assert response.read_calls == 0
    assert not any(value in repr(caught.value) for value in _SENTINELS)


def test_token_exchange_rejects_response_larger_than_two_mib(monkeypatch):
    _block_requests_transport(monkeypatch)
    transport, _calls = _install_memory_transport(
        monkeypatch,
        {WITHINGS_TOKEN_URL: (200, b" " * (2 * 1024 * 1024 + 1), None)},
    )

    with pytest.raises(WithingsTokenExchangeError) as caught:
        _WithingsOAuthAdapter().exchange(
            code="sentinel-code",
            redirect_uri="https://example.test/withings/callback",
            client_id="synthetic-client-id",
            client_secret="sentinel-secret",
        )

    assert caught.value.code == "invalid_response"
    assert len(transport.seen) == 1


def test_oauth_callback_translates_redirect_failure_without_sensitive_details(
    db, user, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "withings_enabled", True)
    monkeypatch.setattr(settings, "withings_redirect_uri", "https://example.test/withings/callback")
    connection = WithingsConnection(
        user_id=user.id,
        client_id="synthetic-client-id",
        encrypted_client_secret=encrypt_credential("sentinel-secret"),
    )
    db.add(connection)
    db.commit()
    now = datetime(2026, 9, 25, tzinfo=UTC)
    state = parse_qs(
        urlsplit(start_withings_oauth(db, user, now=now)).query
    )["state"][0]
    _block_requests_transport(monkeypatch)
    transport, calls = _install_memory_transport(
        monkeypatch,
        {
            WITHINGS_TOKEN_URL: (307, b"sentinel-upstream-body", _UNTRUSTED_LOCATION),
            _UNTRUSTED_LOCATION: (200, _payload_response(), None),
        },
    )

    with pytest.raises(WithingsTokenExchangeError) as caught:
        complete_withings_oauth(
            db,
            user,
            state=state,
            code="sentinel-code",
            error=None,
            now=now,
        )

    db.refresh(connection)
    assert caught.value.code == "invalid_response"
    assert str(caught.value) == "invalid_response"
    assert caught.value.__cause__ is None
    assert connection.last_error_category == "invalid_response"
    assert connection.last_error == "invalid_response"
    assert not any(value in repr(caught.value) for value in _SENTINELS)
    assert not any(value in repr(connection) for value in _SENTINELS)
    assert not any(value in caplog.text for value in _SENTINELS)
    assert [(request.full_url, request.get_method()) for request in transport.seen] == [
        (WITHINGS_TOKEN_URL, "POST")
    ]
    assert calls == [(WITHINGS_TOKEN_URL, "POST", 10)]
