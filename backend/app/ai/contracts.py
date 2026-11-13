"""Provider-neutral, bounded contracts for AI generation."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ProviderKind(StrEnum):
    OPENAI_API = "openai_api"
    CHATGPT_PLAN = "chatgpt_plan"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    LOCAL = "local"
    GATEWAY = "gateway"


class SchemaMode(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"


class FinishReason(StrEnum):
    SUCCESS = "success"
    LENGTH = "length"
    REFUSAL = "refusal"


class ProviderErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    MODEL_UNAVAILABLE = "model_unavailable"
    UPSTREAM_FAILURE = "upstream_failure"


_PUBLIC_ERROR_MESSAGES = {
    ProviderErrorCode.UNAVAILABLE: "The AI provider is unavailable.",
    ProviderErrorCode.RATE_LIMITED: "The AI provider is temporarily rate limited.",
    ProviderErrorCode.INVALID_REQUEST: "The AI request could not be processed.",
    ProviderErrorCode.MODEL_UNAVAILABLE: "The requested AI model is unavailable.",
    ProviderErrorCode.UPSTREAM_FAILURE: "The AI provider could not complete the request.",
}


class ProviderError(Exception):
    """Safe public error classification; never stores an upstream error body."""

    def __init__(self, code: ProviderErrorCode) -> None:
        self.code = code
        self.public_message = _PUBLIC_ERROR_MESSAGES[code]
        super().__init__(self.public_message)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ModelCapabilities(_Contract):
    """Explicit supported flags and nullable limits where provider data is unknown."""

    streaming: bool
    tool_calls: bool
    model_discovery: bool
    schema_mode: SchemaMode
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ModelInfo(_Contract):
    provider: ProviderKind
    model_id: str = Field(min_length=1, max_length=200)
    capabilities: ModelCapabilities


class AIMessage(_Contract):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8_000, repr=False)


class GenerationRequest(_Contract):
    system_instruction: str = Field(min_length=1, max_length=8_000, repr=False)
    messages: tuple[AIMessage, ...] = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=200)
    max_output_tokens: int = Field(default=1_024, ge=1, le=16_384)


class ProviderUsage(_Contract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class GenerationResult(_Contract):
    content: str = Field(min_length=1, max_length=32_000, repr=False)
    model_id: str = Field(min_length=1, max_length=200)
    usage: ProviderUsage | None = None
    finish_reason: FinishReason


class AIProvider(Protocol):
    """Adapter contract. Implementations own transport and provider-specific policy."""

    kind: ProviderKind

    async def discover_models(self) -> tuple[ModelInfo, ...]: ...

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
