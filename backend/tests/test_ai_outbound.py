from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

import app.ai.outbound as outbound
from app.ai.outbound import (
    OutboundError,
    _Address,
    _Endpoint,
    _PinnedConnection,
    _validate_headers,
    fetch_provider_json,
)
from app.config import Settings, settings


class _ResponseHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_headers: ClassVar[dict[str, str]] = {"Content-Type": "application/json"}
    response_body = b'{"data":[{"id":"synthetic-model"}]}'
    seen_headers: ClassVar[dict[str, str]] = {}
    requests_seen = 0
    slow_header_delay = 0.0

    def end_headers(self) -> None:
        if self.slow_header_delay <= 0:
            super().end_headers()
            return
        self.wfile.write(b"".join(self._headers_buffer))
        self.wfile.flush()
        for char in b"X-Trickle:abcdefghij\r\n\r\n":
            self.wfile.write(bytes((char,)))
            self.wfile.flush()
            time.sleep(self.slow_header_delay)

    def do_GET(self) -> None:
        type(self).requests_seen += 1
        type(self).seen_headers = {key.lower(): value for key, value in self.headers.items()}
        self.send_response(type(self).response_status)
        for key, value in type(self).response_headers.items():
            self.send_header(key, value)
        if not any(key.lower() == "content-length" for key in type(self).response_headers):
            self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def local_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(_ResponseHandler, "response_status", 200)
    monkeypatch.setattr(_ResponseHandler, "response_headers", {"Content-Type": "application/json"})
    monkeypatch.setattr(_ResponseHandler, "response_body", b'{"data":[{"id":"synthetic-model"}]}')
    monkeypatch.setattr(_ResponseHandler, "seen_headers", {})
    monkeypatch.setattr(_ResponseHandler, "requests_seen", 0)
    monkeypatch.setattr(_ResponseHandler, "slow_header_delay", 0.0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResponseHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.ai_allowed_endpoints = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield settings.ai_allowed_endpoints
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _call(
    base_url: str,
    path: str = "/models",
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 3,
    native: bool = False,
) -> object:
    return fetch_provider_json(
        base_url,
        path,
        headers=headers if headers is not None else {"Authorization": "Bearer synthetic-test-key"},
        timeout_seconds=timeout_seconds,
        native=native,
    )


def test_settings_allowlist_requires_valid_explicit_base_urls() -> None:
    for value in (
        "http://user:secret@localhost:11434/v1",
        "http://localhost:11434/v1?token=synthetic",
        "http://localhost:11434/v1#section",
        "ftp://localhost/v1",
        "http://localhost/v1/../admin",
        "http://localhost/v1%2fadmin",
        "http://localhost/v1\\admin",
        "http://localhost:0/v1",
        "http://localhost/v1,,http://localhost/v2",
        "http://localhost/v1,http://LOCALHOST:80/v1/",
        ",http://localhost/v1",
    ):
        with pytest.raises(ValueError):
            Settings(environment="test", ai_allowed_endpoints=value)
    assert Settings(environment="test").ai_allowed_endpoints == ""


def test_only_allowlisted_local_destination_gets_one_bounded_json_request(local_provider: str) -> None:
    result = _call(local_provider, headers={"x-api-key": "synthetic-key"})
    assert result == {"data": [{"id": "synthetic-model"}]}
    assert _ResponseHandler.requests_seen == 1
    assert _ResponseHandler.seen_headers["x-api-key"] == "synthetic-key"
    assert _ResponseHandler.seen_headers["accept-encoding"] == "identity"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://169.254.169.254/latest",
        "http://127.0.0.1:11434/v1?token=synthetic",
        "http://user:secret@127.0.0.1:11434/v1",
        "http://127.0.0.1:0/v1",
    ],
)
def test_unallowlisted_or_malformed_destination_is_rejected_without_connecting(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_allowed_endpoints", "")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: pytest.fail("unexpected DNS"))
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: pytest.fail("unexpected connect"))
    with pytest.raises(OutboundError) as error:
        _call(base_url)
    assert error.value.code == "destination_blocked"


@pytest.mark.parametrize("path", ["//evil.invalid/models", "/models?key=secret", "/a/../models", "/models%2fsecret", "/models#fragment", "/models\\x"])
def test_request_path_cannot_change_destination_or_add_query(path: str, local_provider: str) -> None:
    with pytest.raises(OutboundError) as error:
        _call(local_provider, path)
    assert error.value.code == "destination_blocked"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "secret\nInjected: yes"},
        {"Host": "169.254.169.254"},
        {"Proxy-Authorization": "synthetic-secret"},
        {"Authorization": "synthetic", "Content-Length": "1"},
    ],
)
def test_untrusted_header_values_and_transport_headers_are_rejected(headers: dict[str, str], local_provider: str) -> None:
    with pytest.raises(OutboundError) as error:
        fetch_provider_json(local_provider, "/models", headers=headers, timeout_seconds=3, native=False)
    assert error.value.code == "destination_blocked"


def test_header_bound_accepts_maximum_configured_bearer_key() -> None:
    _validate_headers({"Authorization": f"Bearer {'x' * 8_192}"})
    with pytest.raises(OutboundError) as error:
        _validate_headers({"Authorization": "x" * 16_385})
    assert error.value.code == "destination_blocked"


@pytest.mark.parametrize(
    ("status", "code"),
    [(301, "http_error"), (401, "authentication"), (403, "authentication"), (404, "http_error"), (429, "rate_limited"), (503, "unavailable")],
)
def test_status_errors_are_classified_without_echoing_body_or_credentials(
    status: int, code: str, local_provider: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_status", status)
    monkeypatch.setattr(_ResponseHandler, "response_body", b"synthetic-upstream-secret-response")
    with pytest.raises(OutboundError) as error:
        fetch_provider_json(
            local_provider, "/models", headers={"Authorization": "Bearer synthetic-test-key"},
            timeout_seconds=3, native=False,
        )
    assert error.value.code == code
    assert error.value.status == status
    assert "synthetic-upstream-secret-response" not in str(error.value)
    assert "synthetic-test-key" not in repr(error.value)
    assert local_provider not in repr(error.value)


@pytest.mark.parametrize(
    ("content_type", "encoding", "body", "expected"),
    [
        ("text/plain", "identity", b"{}", "invalid_response"),
        ("application/json", "gzip", b"{}", "invalid_response"),
        ("application/json", "identity", b"not json", "invalid_response"),
    ],
)
def test_response_must_be_small_utf8_json_without_compression(
    content_type: str, encoding: str, body: bytes, expected: str, local_provider: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_headers", {"Content-Type": content_type, "Content-Encoding": encoding})
    monkeypatch.setattr(_ResponseHandler, "response_body", body)
    with pytest.raises(OutboundError) as error:
        _call(local_provider)
    assert error.value.code == expected


def test_oversize_response_is_rejected_before_json_parsing(local_provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_body", json.dumps({"value": "x" * outbound.MAX_PROVIDER_RESPONSE_BYTES}).encode())
    with pytest.raises(OutboundError) as error:
        _call(local_provider)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("answers", "native", "expected"),
    [
        (["127.0.0.1"], True, "destination_blocked"),
        (["169.254.169.254"], False, "destination_blocked"),
        (["8.8.8.8", "169.254.1.2"], False, "destination_blocked"),
        (["100.64.0.2"], False, "destination_blocked"),
        (["224.0.0.1"], False, "destination_blocked"),
    ],
)
def test_all_dns_answers_are_validated_before_any_connection(
    answers: list[str], native: bool, expected: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_allowed_endpoints", "http://resolver.test/v1")
    rows = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443 if native else 80))
        for address in answers
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: pytest.fail("unsafe DNS result connected"))
    base = "https://resolver.test/v1" if native else "http://resolver.test/v1"
    with pytest.raises(OutboundError) as error:
        fetch_provider_json(base, "/models", headers={}, timeout_seconds=3, native=native)
    assert error.value.code == expected


def test_cleartext_custom_transport_is_restricted_to_private_destinations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_allowed_endpoints", "http://provider.example/v1")
    rows = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: pytest.fail("cleartext public destination connected"))
    with pytest.raises(OutboundError) as error:
        fetch_provider_json("http://provider.example/v1", "/models", headers={}, timeout_seconds=3, native=False)
    assert error.value.code == "destination_blocked"


def test_native_requests_require_https_default_port_and_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
    ])
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: pytest.fail("no response mocked"))
    for base in ("http://api.example/v1", "https://api.example:444/v1"):
        with pytest.raises(OutboundError) as error:
            fetch_provider_json(base, "/models", headers={}, timeout_seconds=3, native=True)
        assert error.value.code == "destination_blocked"


def test_socket_connect_uses_numeric_ip_and_tls_keeps_original_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeSocket:
        def __init__(self, *_args: object) -> None:
            pass

        def settimeout(self, _value: float) -> None:
            pass

        def connect(self, address: tuple[object, ...]) -> None:
            calls.append(address)

        def getpeername(self) -> tuple[str, int]:
            return "93.184.216.34", 443

        def close(self) -> None:
            pass

    class FakeTLSContext:
        def wrap_socket(self, sock: FakeSocket, *, server_hostname: str) -> FakeSocket:
            calls.append(server_hostname)
            return sock

    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(ssl, "create_default_context", lambda: FakeTLSContext())
    connection = _PinnedConnection(
        _Endpoint("https", "provider.example", 443, "/v1"),
        _Address(socket.AF_INET, ipaddress.ip_address("93.184.216.34")),
        outbound.time.monotonic() + 5,
    )
    connection.connect(3)
    assert calls == [("93.184.216.34", 443), "provider.example"]


def test_resolver_timeout_and_saturation_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: gate.wait(1))
    monkeypatch.setattr(settings, "ai_allowed_endpoints", "http://slow.test/v1")
    with pytest.raises(OutboundError) as error:
        fetch_provider_json("http://slow.test/v1", "/models", headers={}, timeout_seconds=1, native=False)
    assert error.value.code == "timeout"
    gate.set()


def test_http_timeout_is_classified_without_upstream_detail(local_provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    class TimedOutConnection:
        sock = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def putrequest(self, *_args: object, **_kwargs: object) -> None:
            raise TimeoutError("synthetic provider secret")

        def close(self) -> None:
            pass

    monkeypatch.setattr(outbound, "_PinnedHTTPConnection", TimedOutConnection)
    with pytest.raises(OutboundError) as error:
        _call(local_provider)
    assert error.value.code == "timeout"
    assert "synthetic provider secret" not in repr(error.value)


def test_slowly_trickled_response_header_cannot_extend_absolute_deadline(
    local_provider: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ResponseHandler, "slow_header_delay", 0.2)
    started = time.monotonic()
    with pytest.raises(OutboundError) as error:
        fetch_provider_json(
            local_provider, "/models", headers={}, timeout_seconds=1, native=False,
        )
    elapsed = time.monotonic() - started
    assert error.value.code == "timeout"
    assert elapsed < 1.8
