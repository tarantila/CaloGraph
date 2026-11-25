from __future__ import annotations

import io
import json
import socket

import pytest
from pydantic import SecretStr

import app.ai.generation as generation
import app.ai.outbound as outbound
from app.ai.contracts import FinishReason, GenerationRequest, ProviderKind
from app.ai.outbound import OutboundError

SECRET = "synthetic-generation-secret"


def request() -> GenerationRequest:
    from app.ai.contracts import AIMessage

    return GenerationRequest(
        system_instruction="synthetic system", messages=(AIMessage(role="user", content="hello"),),
        model_id="synthetic-model", max_output_tokens=123,
    )


@pytest.mark.parametrize(("error", "fallback_safe", "status"), [
    (OutboundError("unavailable", fallback_safe=True), True, None),
    (OutboundError("unavailable", status=502), False, 502),
    (OutboundError("destination_blocked"), False, None),
])
def test_generation_failure_preserves_only_explicit_safe_transport_metadata(
    monkeypatch: pytest.MonkeyPatch,
    error: OutboundError,
    fallback_safe: bool,
    status: int | None,
) -> None:
    def fail_transport(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(generation, "fetch_provider_json", fail_transport)
    with pytest.raises(generation.GenerationFailure) as failure:
        generation.generate(
            ProviderKind.LOCAL,
            base_url="http://localhost:11434/v1",
            key=None,
            request=request(),
            timeout_seconds=3,
        )
    assert failure.value.fallback_safe is fallback_safe
    assert failure.value.status == status


def test_native_generate_content_reaches_the_validated_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({
        "candidates": [{"content": {"parts": [{"text": "synthetic response"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
    }).encode()
    calls: dict[str, object] = {}

    class Response(io.BytesIO):
        status = 200

        def getheader(self, name, default=None):
            return {"Content-Type": "application/json", "Content-Length": str(len(body))}.get(name, default)

    class Connection:
        sock = None

        def __init__(self, endpoint, address, deadline, timeout_seconds):
            assert endpoint.base_url == "https://generativelanguage.googleapis.com/v1beta"
            assert str(address.ip) == "1.1.1.1"
            assert timeout_seconds == 60
            calls["headers"] = {}

        def putrequest(self, method, path, **kwargs):
            calls["request"] = (method, path)

        def putheader(self, name, value):
            calls["headers"][name.lower()] = value

        def endheaders(self, request_body):
            calls["body"] = json.loads(request_body)

        def getresponse(self):
            return Response(body)

        def close(self):
            calls["closed"] = True

    # Keep the adapter, path policy, header validation and response parser real;
    # replace only DNS and the final HTTPS connection, with no external traffic.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
    ])
    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection", Connection)
    result = generation.generate(
        ProviderKind.GEMINI, base_url=None, key=SecretStr(SECRET),
        request=request(), timeout_seconds=90,
    )
    assert calls["request"] == ("POST", "/v1beta/models/synthetic-model:generateContent")
    assert calls["headers"]["x-goog-api-key"] == SECRET
    assert "store" not in calls["body"]
    assert calls["closed"] is True
    assert result.content == "synthetic response"
    assert result.usage.input_tokens == 5 and result.usage.output_tokens == 2


@pytest.mark.parametrize(
    ("kind", "base_url", "expected_url", "expected_path", "header", "body_check"),
    [
        (ProviderKind.OPENAI_API, "https://ignored.example/v1", "https://api.openai.com/v1", "/chat/completions", "authorization", lambda p: p["max_completion_tokens"] == 123 and p["store"] is False),
        (ProviderKind.ANTHROPIC, None, "https://api.anthropic.com/v1", "/messages", "x-api-key", lambda p: p["max_tokens"] == 123 and p["system"] == "synthetic system"),
        (ProviderKind.GEMINI, None, "https://generativelanguage.googleapis.com/v1beta", "/models/synthetic-model:generateContent", "x-goog-api-key", lambda p: p["generationConfig"]["maxOutputTokens"] == 123 and "store" not in p),
        (ProviderKind.LOCAL, "http://localhost:11434/v1", "http://localhost:11434/v1", "/chat/completions", "authorization", lambda p: p["max_tokens"] == 123),
        (ProviderKind.GATEWAY, "https://gateway.example/v1", "https://gateway.example/v1", "/chat/completions", "authorization", lambda p: p["max_tokens"] == 123),
    ],
)
def test_generation_provider_mapping_is_single_bounded_json_post(
    monkeypatch: pytest.MonkeyPatch, kind: ProviderKind, base_url: str | None,
    expected_url: str, expected_path: str, header: str, body_check,
) -> None:
    calls: list[tuple[str, str, dict[str, str], dict[str, object], int, bool]] = []

    def fake(url, path, *, headers, timeout_seconds, native, request_body):
        calls.append((url, path, headers, json.loads(request_body), timeout_seconds, native))
        if kind == ProviderKind.ANTHROPIC:
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}
        if kind == ProviderKind.GEMINI:
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}], "usageMetadata": {}}
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}

    monkeypatch.setattr(generation, "fetch_provider_json", fake)
    result = generation.generate(
        kind, base_url=base_url, key=SecretStr(SECRET), request=request(), timeout_seconds=90,
    )
    assert result.content == "ok" and result.finish_reason == FinishReason.SUCCESS
    assert len(calls) == 1
    url, path, headers, payload, timeout, native = calls[0]
    assert (url, path) == (expected_url, expected_path)
    assert any(name.lower() == header and value for name, value in headers.items())
    assert body_check(payload)
    assert timeout == 60 and native is (kind not in {ProviderKind.LOCAL, ProviderKind.GATEWAY})
    assert SECRET not in repr(result)


@pytest.mark.parametrize("kind", [ProviderKind.OPENAI_API, ProviderKind.ANTHROPIC, ProviderKind.GEMINI])
def test_native_provider_requires_key_without_echoing_values(kind: ProviderKind, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "fetch_provider_json", lambda *_args, **_kwargs: pytest.fail("must not call transport"))
    with pytest.raises(generation.GenerationFailure) as error:
        generation.generate(kind, base_url=None, key=None, request=request(), timeout_seconds=10)
    assert error.value.code == "authentication"
    assert SECRET not in str(error.value)


def test_generation_maps_transport_failure_once_without_exposing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise OutboundError("authentication", status=401)

    monkeypatch.setattr(generation, "fetch_provider_json", fail)
    with pytest.raises(generation.GenerationFailure) as error:
        generation.generate(ProviderKind.LOCAL, base_url="http://localhost:11434/v1", key=SecretStr(SECRET), request=request(), timeout_seconds=10)
    assert calls == 1 and error.value.code == "authentication"
    assert SECRET not in repr(error.value) and "401" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        {"choices": [{"message": {"content": SECRET}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]},
        {"unexpected": "shape"},
    ],
)
def test_generation_rejects_unsafe_or_malformed_provider_responses(response, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "fetch_provider_json", lambda *_args, **_kwargs: response)
    with pytest.raises(generation.GenerationFailure) as error:
        generation.generate(ProviderKind.LOCAL, base_url="http://localhost:11434/v1", key=SecretStr(SECRET), request=request(), timeout_seconds=10)
    assert error.value.code == "invalid_response" and SECRET not in repr(error.value)


def test_invalid_provider_usage_count_becomes_unknown_not_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "fetch_provider_json", lambda *_args, **_kwargs: {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": True, "completion_tokens": -1},
    })
    result = generation.generate(ProviderKind.LOCAL, base_url="http://localhost:11434/v1", key=None, request=request(), timeout_seconds=10)
    assert result.usage is not None
    assert result.usage.input_tokens is None and result.usage.output_tokens is None


def test_chatgpt_plan_is_not_an_unsupported_private_generation_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "fetch_provider_json", lambda *_args, **_kwargs: pytest.fail("no transport"))
    with pytest.raises(generation.GenerationFailure) as error:
        generation.generate(ProviderKind.CHATGPT_PLAN, base_url=None, key=None, request=request(), timeout_seconds=10)
    assert error.value.code == "unsupported_provider"
