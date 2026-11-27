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
    _fetch_provider_json,
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
    seen_method: ClassVar[str | None] = None
    seen_body: ClassVar[bytes] = b""
    slow_header_delay = 0.0
    automatic_content_length = True

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

    def _respond(self) -> None:
        type(self).requests_seen += 1
        type(self).seen_method = self.command
        type(self).seen_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).seen_headers = {key.lower(): value for key, value in self.headers.items()}
        self.send_response(type(self).response_status)
        for key, value in type(self).response_headers.items():
            self.send_header(key, value)
        if self.automatic_content_length and not any(key.lower() == "content-length" for key in type(self).response_headers):
            self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def local_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(_ResponseHandler, "response_status", 200)
    monkeypatch.setattr(_ResponseHandler, "response_headers", {"Content-Type": "application/json"})
    monkeypatch.setattr(_ResponseHandler, "response_body", b'{"data":[{"id":"synthetic-model"}]}')
    monkeypatch.setattr(_ResponseHandler, "seen_headers", {})
    monkeypatch.setattr(_ResponseHandler, "requests_seen", 0)
    monkeypatch.setattr(_ResponseHandler, "seen_method", None)
    monkeypatch.setattr(_ResponseHandler, "seen_body", b"")
    monkeypatch.setattr(_ResponseHandler, "slow_header_delay", 0.0)
    monkeypatch.setattr(_ResponseHandler, "automatic_content_length", True)
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
    request_body: bytes | None = None,
    max_request_bytes: int = outbound.MAX_PROVIDER_REQUEST_BYTES,
) -> object:
    return fetch_provider_json(
        base_url,
        path,
        headers=headers if headers is not None else {"Authorization": "Bearer synthetic-test-key"},
        timeout_seconds=timeout_seconds,
        native=native,
        request_body=request_body,
        max_request_bytes=max_request_bytes,
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


@pytest.mark.parametrize(("status", "safe"), [(429, True), (503, True), (502, False), (401, False)])
def test_only_explicit_429_and_503_are_safe_http_fallbacks(
    status: int, safe: bool, local_provider: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_status", status)
    with pytest.raises(OutboundError) as error:
        _call(local_provider)
    assert error.value.status == status
    assert error.value.fallback_safe is safe


def test_connection_failure_before_request_bytes_is_safe_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResponseHandler)
    port = server.server_port
    server.server_close()
    base_url = f"http://127.0.0.1:{port}/v1"
    monkeypatch.setattr(settings, "ai_allowed_endpoints", base_url)
    with pytest.raises(OutboundError) as error:
        _call(base_url, path="/chat/completions", request_body=b"{}")
    assert error.value.code == "unavailable"
    assert error.value.fallback_safe is True


def test_timeout_after_request_reaches_provider_is_not_safe_to_fallback(
    local_provider: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_ResponseHandler, "slow_header_delay", 0.2)
    with pytest.raises(OutboundError) as error:
        _call(local_provider, timeout_seconds=1, request_body=b"{}")
    assert error.value.code == "timeout"
    assert error.value.fallback_safe is False
    assert _ResponseHandler.requests_seen == 1


def test_generation_post_sends_one_bounded_json_body_to_the_pinned_destination(local_provider: str) -> None:
    body = b'{"model":"synthetic-model","messages":[{"role":"user","content":"synthetic"}]}'
    result = _call(local_provider, request_body=body)
    assert result == {"data": [{"id": "synthetic-model"}]}
    assert _ResponseHandler.requests_seen == 1
    assert _ResponseHandler.seen_method == "POST"
    assert _ResponseHandler.seen_body == body
    assert _ResponseHandler.seen_headers["content-type"] == "application/json"
    assert _ResponseHandler.seen_headers["content-length"] == str(len(body))


def test_oauth_form_uses_shared_pinned_transport_and_form_content_type(local_provider: str) -> None:
    body = b"grant_type=refresh_token&refresh_token=synthetic-refresh"
    result = _fetch_provider_json(
        local_provider, "/oauth/token", headers={}, timeout_seconds=3, native=False,
        request_body=body, _request_content_type="application/x-www-form-urlencoded",
    )
    assert result == {"data": [{"id": "synthetic-model"}]}
    assert _ResponseHandler.seen_headers["content-type"] == "application/x-www-form-urlencoded"
    assert _ResponseHandler.seen_body == body


def test_oauth_json_refresh_uses_json_and_safe_empty_revocation_contract(local_provider: str, monkeypatch) -> None:
    # Exercise the shared JSON encoding and response parser on the disposable
    # local provider; the public helper itself remains pinned to OpenAI hosts.
    body = b'{"client_id":"synthetic-client","grant_type":"refresh_token"}'
    result = _fetch_provider_json(
        local_provider, "/oauth/token", headers={}, timeout_seconds=3, native=False,
        request_body=body,
    )
    assert result == {"data": [{"id": "synthetic-model"}]}
    assert _ResponseHandler.seen_headers["content-type"] == "application/json"
    assert _ResponseHandler.seen_body == body
    monkeypatch.setattr(_ResponseHandler, "response_headers", {"Content-Length": "0"})
    monkeypatch.setattr(_ResponseHandler, "response_body", b"")
    assert _fetch_provider_json(
        local_provider, "/oauth/revoke", headers={}, timeout_seconds=3, native=False,
        request_body=b"token=synthetic", _allow_empty_success=True,
    ) is None
    unexpected = b'{"unexpected":true}'
    monkeypatch.setattr(_ResponseHandler, "response_body", unexpected)
    monkeypatch.setattr(_ResponseHandler, "response_headers", {"Content-Length": str(len(unexpected))})
    with pytest.raises(OutboundError) as error:
        _fetch_provider_json(
            local_provider, "/oauth/revoke", headers={}, timeout_seconds=3, native=False,
            request_body=b"token=synthetic", _allow_empty_success=True,
        )
    assert error.value.code == "invalid_response"


def test_default_provider_response_requires_json_content_type(local_provider: str, monkeypatch) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_headers", {"Content-Length": "2"})
    monkeypatch.setattr(_ResponseHandler, "response_body", b"{}")
    with pytest.raises(OutboundError) as error:
        _call(local_provider)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("body", [b"", b"{}"])
def test_revocation_reads_response_without_content_length_before_confirming_success(
    local_provider: str, monkeypatch, chunked: bool, body: bytes,
) -> None:
    monkeypatch.setattr(_ResponseHandler, "automatic_content_length", False)
    headers = {"Transfer-Encoding": "chunked"} if chunked else {}
    wire_body = ((b"2\r\n" + body + b"\r\n") if body else b"") + b"0\r\n\r\n" if chunked else body
    monkeypatch.setattr(_ResponseHandler, "response_headers", headers)
    monkeypatch.setattr(_ResponseHandler, "response_body", wire_body)

    def revoke():
        return _fetch_provider_json(
            local_provider, "/oauth/revoke", headers={}, timeout_seconds=3, native=False,
            request_body=b"token=synthetic", _allow_empty_success=True,
        )

    if not body:
        assert revoke() is None
    else:
        with pytest.raises(OutboundError) as error:
            revoke()
        assert error.value.code == "invalid_response"


@pytest.mark.parametrize("status", [403, 404])
def test_only_explicitly_accepted_protocol_statuses_return_status_tuple(local_provider: str, monkeypatch, status: int) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_status", status)
    monkeypatch.setattr(_ResponseHandler, "response_headers", {})
    monkeypatch.setattr(_ResponseHandler, "response_body", b"")
    assert _fetch_provider_json(
        local_provider, "/oauth/poll", headers={}, timeout_seconds=3, native=False,
        request_body=b"{}", _accepted_statuses=(403, 404), _return_status=True,
    ) == (status, None)


@pytest.mark.parametrize("code", ["invalid_grant", "invalid_refresh_token", "refresh_token_reused", "token_expired"])
def test_oauth_errors_are_allowlisted_and_response_body_is_never_exposed(local_provider: str, monkeypatch, code: str) -> None:
    monkeypatch.setattr(_ResponseHandler, "response_status", 400)
    monkeypatch.setattr(_ResponseHandler, "response_body", json.dumps({"error": code, "error_description": "secret synthetic detail"}).encode())
    with pytest.raises(OutboundError) as error:
        _fetch_provider_json(
            local_provider, "/oauth/token", headers={}, timeout_seconds=3, native=False,
            request_body=b"synthetic-secret", _allow_oauth_error=True,
        )
    assert error.value.oauth_error == code
    assert "synthetic" not in str(error.value)
    monkeypatch.setattr(_ResponseHandler, "response_body", b'{"error":"attacker-value","error_description":"secret"}')
    with pytest.raises(OutboundError) as error:
        _fetch_provider_json(
            local_provider, "/oauth/token", headers={}, timeout_seconds=3, native=False,
            request_body=b"{}", _allow_oauth_error=True,
        )
    assert error.value.oauth_error is None


def test_fixed_auth_wrappers_reject_unreviewed_host_paths_and_oversized_forms() -> None:
    from app.ai.outbound import (
        fetch_chatgpt_auth_json,
        fetch_chatgpt_oauth_form,
        fetch_chatgpt_oauth_json,
    )

    with pytest.raises(OutboundError):
        fetch_chatgpt_auth_json("/admin", b"{}", timeout_seconds=3)
    with pytest.raises(OutboundError):
        fetch_chatgpt_oauth_form("/responses", {"token": "synthetic"}, timeout_seconds=3)
    with pytest.raises(OutboundError):
        fetch_chatgpt_oauth_json("/responses", {"refresh_token": "synthetic"}, timeout_seconds=3)
    with pytest.raises(OutboundError):
        fetch_chatgpt_oauth_form("/oauth/token", {"token": "x" * (64 * 1024)}, timeout_seconds=3)


@pytest.mark.parametrize("body", [b"", b"x" * (outbound.MAX_PROVIDER_REQUEST_BYTES + 1)])
def test_invalid_post_body_is_rejected_before_connecting(
    body: bytes, local_provider: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: pytest.fail("unexpected connect"))
    with pytest.raises(OutboundError) as error:
        _call(local_provider, request_body=body)
    assert error.value.code == "destination_blocked"
    assert _ResponseHandler.requests_seen == 0


def test_media_request_ceiling_is_opt_in_and_still_bounded(local_provider: str) -> None:
    large = b"x" * (outbound.MAX_PROVIDER_REQUEST_BYTES + 1)
    with pytest.raises(OutboundError):
        _call(local_provider, request_body=large)
    assert _ResponseHandler.requests_seen == 0

    result = _call(
        local_provider,
        request_body=large,
        max_request_bytes=outbound.MAX_MEDIA_PROVIDER_REQUEST_BYTES,
    )
    assert result == {"data": [{"id": "synthetic-model"}]}
    assert _ResponseHandler.requests_seen == 1
    assert _ResponseHandler.seen_body == large

    with pytest.raises(OutboundError):
        _call(
            local_provider,
            request_body=b"x" * (outbound.MAX_MEDIA_PROVIDER_REQUEST_BYTES + 1),
            max_request_bytes=outbound.MAX_MEDIA_PROVIDER_REQUEST_BYTES,
        )
    assert _ResponseHandler.requests_seen == 1


def test_generation_media_adapter_opts_into_bounded_large_body(local_provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    from uuid import uuid4

    from app.ai.contracts import AIMedia, AIMessage, GenerationRequest, ProviderKind
    from app.ai.generation import GenerationFailure, generate

    monkeypatch.setattr(
        _ResponseHandler,
        "response_body",
        b'{"choices":[{"message":{"content":"synthetic answer"},"finish_reason":"stop"}]}',
    )
    long_text = "x" * 8_000
    without_media = GenerationRequest(
        system_instruction="s" * 8_000,
        messages=tuple(AIMessage(role="user", content=long_text) for _ in range(32)),
        model_id="synthetic-model",
    )
    with pytest.raises(GenerationFailure, match="could not complete"):
        generate(
            ProviderKind.LOCAL, base_url=local_provider, key=None,
            request=without_media, timeout_seconds=30,
        )
    assert _ResponseHandler.requests_seen == 0

    file_bytes = b"%PDF-1.4\n" + b"synthetic-content " * (13 * 1024)
    media = AIMedia(
        id=str(uuid4()),
        file_name="synthetic.pdf",
        media_type="application/pdf",
        data_base64=base64.b64encode(file_bytes).decode("ascii"),
    )
    with_media = GenerationRequest(
        system_instruction="synthetic",
        messages=(AIMessage(role="user", content="Review this synthetic document.", attachments=(media,)),),
        model_id="synthetic-model",
    )
    result = generate(
        ProviderKind.LOCAL, base_url=local_provider, key=None,
        request=with_media, timeout_seconds=30,
    )
    assert result.content == "synthetic answer"
    assert _ResponseHandler.requests_seen == 1
    assert len(_ResponseHandler.seen_body) > outbound.MAX_PROVIDER_REQUEST_BYTES
    assert len(_ResponseHandler.seen_body) <= outbound.MAX_MEDIA_PROVIDER_REQUEST_BYTES


@pytest.mark.parametrize(("base_url", "native", "body", "path"), [
    ("https://generativelanguage.googleapis.com/v1beta", False, b"{}", "/models/synthetic:generateContent"),
    ("https://generativelanguage.googleapis.com/v1beta", True, None, "/models/synthetic:generateContent"),
    ("https://generativelanguage.googleapis.com/v1", True, b"{}", "/models/synthetic:generateContent"),
    ("https://generativelanguage.googleapis.com:444/v1beta", True, b"{}", "/models/synthetic:generateContent"),
    ("http://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/synthetic:generateContent"),
    ("https://generativelanguage.googleapis.com.evil.invalid/v1beta", True, b"{}", "/models/synthetic:generateContent"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/synthetic:streamGenerateContent"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/synthetic:generateContent?alt=sse"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/../synthetic:generateContent"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/..:generateContent"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/a%2fb:generateContent"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/synthetic:generateContent#fragment"),
    ("https://generativelanguage.googleapis.com/v1beta", True, b"{}", "/models/synthetic:generateContent:extra"),
])
def test_native_action_path_exception_is_restricted_before_dns(
    base_url: str, native: bool, body: bytes | None, path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outbound, "_resolve", lambda *args, **kwargs: pytest.fail("unexpected DNS"))
    with pytest.raises(OutboundError) as error:
        _call(base_url, path, native=native, request_body=body)
    assert error.value.code == "destination_blocked"


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
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: gate.wait(5))
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


def test_expanded_ipv6_allowlist_matches_normalized_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.connections_contracts import ProviderConnectionPut
    from app.ai.outbound import _endpoint
    from app.config import Settings

    expanded = "http://[0:0:0:0:0:0:0:1]:11434/v1"
    compressed = "http://[::1]:11434/v1"
    configured = ProviderConnectionPut(label="Local", provider_kind="local", model_id="synthetic", base_url=expanded)
    assert _endpoint(expanded).base_url == _endpoint(str(configured.base_url)).base_url == compressed
    monkeypatch.setattr(settings, "ai_allowed_endpoints", expanded)

    def resolve(*args, **kwargs):
        # Reaching DNS proves the allowlist accepted HttpUrl's canonical form.
        raise OutboundError("unavailable")
    monkeypatch.setattr("app.ai.outbound._resolve", resolve)
    with pytest.raises(OutboundError) as error:
        fetch_provider_json(str(configured.base_url), "/models", headers={}, timeout_seconds=1, native=False)
    assert error.value.code == "unavailable"
    with pytest.raises(ValueError):
        Settings(environment="test", ai_allowed_endpoints=f"{expanded},{compressed}")
