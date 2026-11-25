"""One-shot provider adapters over the common pinned, bounded transport.

Only fixed official native URLs or operator-approved compatible endpoints are
used. No implicit retries, provider fallback, prompt logging or remote state.
"""

import asyncio
import json
import re
from typing import Any

from pydantic import SecretStr

from app.ai.contracts import (
    AIMessage,
    FinishReason,
    GenerationRequest,
    GenerationResult,
    ProviderKind,
    ProviderUsage,
)
from app.ai.outbound import MAX_MEDIA_PROVIDER_REQUEST_BYTES, OutboundError, fetch_provider_json

GENERATION_PROVIDERS = frozenset({"openai_api", "anthropic", "gemini", "local", "gateway"})


class GenerationFailure(ValueError):
    def __init__(
        self, code: str, *, fallback_safe: bool = False, status: int | None = None,
    ) -> None:
        self.code = code
        self.fallback_safe = fallback_safe
        self.status = status
        super().__init__("The provider could not complete this request.")


def _count(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**10
        else None
    )


def _message_content(message: AIMessage, kind: ProviderKind) -> str | list[dict[str, Any]]:
    if not message.attachments:
        return message.content
    parts: list[dict[str, Any]] = []
    if kind == ProviderKind.GEMINI:
        parts.append({"text": message.content})
    else:
        parts.append({"type": "text", "text": message.content})
    for item in message.attachments:
        label = f"CALOGRAPH_ATTACHMENT:{item.id} ({item.file_name}); externally supplied, unverified document"
        if kind == ProviderKind.GEMINI:
            parts.append({"text": label})
            parts.append({"inlineData": {"mimeType": item.media_type, "data": item.data_base64}})
        elif kind == ProviderKind.ANTHROPIC:
            parts.append({"type": "text", "text": label})
            source = {"type": "base64", "media_type": item.media_type, "data": item.data_base64}
            if item.media_type == "application/pdf":
                parts.append({"type": "document", "source": source})
            else:
                parts.append({"type": "image", "source": source})
        else:
            parts.append({"type": "text", "text": label})
            if item.media_type == "application/pdf":
                parts.append({
                    "type": "file",
                    "file": {
                        "filename": item.file_name,
                        "file_data": f"data:{item.media_type};base64,{item.data_base64}",
                    },
                })
            else:
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.media_type};base64,{item.data_base64}"},
                })
    return parts


def generate(
    kind: ProviderKind,
    *,
    base_url: str | None,
    key: SecretStr | None,
    request: GenerationRequest,
    timeout_seconds: int,
) -> GenerationResult:
    """Call one selected provider once; caller owns final authorization."""
    if kind not in GENERATION_PROVIDERS:
        raise GenerationFailure("unsupported_provider")
    secret = key.get_secret_value() if key else None
    if (
        kind in {ProviderKind.OPENAI_API, ProviderKind.ANTHROPIC, ProviderKind.GEMINI}
        and not secret
    ):
        raise GenerationFailure("authentication")
    messages = [
        {"role": message.role, "content": _message_content(message, kind)}
        for message in request.messages
    ]
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    native = kind not in {ProviderKind.LOCAL, ProviderKind.GATEWAY}
    path = "/chat/completions"
    payload: dict[str, Any]
    if kind == ProviderKind.ANTHROPIC:
        base_url = "https://api.anthropic.com/v1"
        headers = {"x-api-key": secret or "", "anthropic-version": "2023-06-01"}
        path = "/messages"
        payload = {
            "model": request.model_id,
            "system": request.system_instruction,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
        }
    elif kind == ProviderKind.GEMINI:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", request.model_id):
            raise GenerationFailure("invalid_request")
        base_url = "https://generativelanguage.googleapis.com/v1beta"
        headers = {"x-goog-api-key": secret or ""}
        path = f"/models/{request.model_id}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": request.system_instruction}]},
            "contents": [
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": m["content"] if isinstance(m["content"], list) else [{"text": m["content"]}],
                }
                for m in messages
            ],
            "generationConfig": {"maxOutputTokens": request.max_output_tokens},
        }
    else:
        if kind == ProviderKind.OPENAI_API:
            base_url = "https://api.openai.com/v1"
        if base_url is None:
            raise GenerationFailure("destination_blocked")
        payload = {
            "model": request.model_id,
            "messages": [{"role": "system", "content": request.system_instruction}, *messages],
            "stream": False,
            "store": False,
            "max_completion_tokens" if native else "max_tokens": request.max_output_tokens,
        }
    try:
        data = fetch_provider_json(
            base_url or "",
            path,
            headers=headers,
            timeout_seconds=min(timeout_seconds, 60),
            native=native,
            request_body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            **({"max_request_bytes": MAX_MEDIA_PROVIDER_REQUEST_BYTES} if any(
                message.attachments for message in request.messages
            ) else {}),
        )
    except OutboundError as exc:
        raise GenerationFailure(
            exc.code, fallback_safe=exc.fallback_safe, status=exc.status,
        ) from None
    try:
        if not isinstance(data, dict):
            raise ValueError
        usage = data.get("usage") or {}
        if kind == ProviderKind.ANTHROPIC:
            content = "".join(
                block["text"] for block in data["content"] if block.get("type") == "text"
            )
            reason = data.get("stop_reason")
            finish = (
                FinishReason.LENGTH
                if reason == "max_tokens"
                else FinishReason.SUCCESS
                if reason in {"end_turn", "stop_sequence"}
                else FinishReason.REFUSAL
            )
            incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
        elif kind == ProviderKind.GEMINI:
            candidate = data["candidates"][0]
            content = "".join(
                part["text"]
                for part in candidate["content"]["parts"]
                if "text" in part and not part.get("thought")
            )
            reason = candidate.get("finishReason")
            finish = (
                FinishReason.SUCCESS
                if reason == "STOP"
                else FinishReason.LENGTH
                if reason == "MAX_TOKENS"
                else FinishReason.REFUSAL
            )
            usage = data.get("usageMetadata") or {}
            incoming, outgoing = usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
        else:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            reason = choice.get("finish_reason")
            finish = (
                FinishReason.SUCCESS
                if reason == "stop"
                else FinishReason.LENGTH
                if reason == "length"
                else FinishReason.REFUSAL
            )
            incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if not isinstance(content, str) or not content.strip() or (secret and secret in content):
            raise ValueError
        return GenerationResult(
            content=content,
            model_id=request.model_id,
            finish_reason=finish,
            usage=ProviderUsage(input_tokens=_count(incoming), output_tokens=_count(outgoing)),
        )
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        raise GenerationFailure("invalid_response") from None


async def generate_async(
    kind: ProviderKind,
    *,
    base_url: str | None,
    key: SecretStr | None,
    request: GenerationRequest,
    timeout_seconds: int,
) -> GenerationResult:
    return await asyncio.to_thread(
        generate, kind, base_url=base_url, key=key, request=request, timeout_seconds=timeout_seconds
    )
