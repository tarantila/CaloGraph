import asyncio

import pytest
from pydantic import ValidationError

from app.ai.contracts import (
    AIMessage,
    FinishReason,
    GenerationRequest,
    GenerationResult,
    ModelCapabilities,
    ModelInfo,
    ProviderError,
    ProviderErrorCode,
    ProviderKind,
    ProviderUsage,
    SchemaMode,
)
from app.ai.fake import FakeProvider


def request(content: str = "question") -> GenerationRequest:
    return GenerationRequest(
        system_instruction="Answer briefly.",
        messages=(AIMessage(role="user", content=content),),
        model_id="fake-model",
    )


def result(content: str) -> GenerationResult:
    return GenerationResult(
        content=content,
        model_id="fake-model",
        finish_reason=FinishReason.SUCCESS,
    )


@pytest.mark.asyncio
async def test_fake_returns_scripted_results_in_order_and_discovery_is_in_memory() -> None:
    fake = FakeProvider([result("first"), result("second")])

    models = await fake.discover_models()
    assert models[0].model_id == "fake-model"
    assert (await fake.generate(request("sensitive one"))).content == "first"
    assert (await fake.generate(request("sensitive two"))).content == "second"
    with pytest.raises(ProviderError) as exc:
        await fake.generate(request())
    assert exc.value.code is ProviderErrorCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_fake_raises_safe_classification_without_upstream_details() -> None:
    fake = FakeProvider([ProviderError(ProviderErrorCode.RATE_LIMITED)])

    with pytest.raises(ProviderError) as exc:
        await fake.generate(request())
    assert exc.value.code is ProviderErrorCode.RATE_LIMITED
    assert "upstream" not in str(exc.value).lower()


@pytest.mark.asyncio
async def test_fake_does_not_intercept_task_cancellation() -> None:
    fake = FakeProvider([result("unused")], gate=asyncio.Event())
    task = asyncio.create_task(fake.generate(request()))
    asyncio.get_running_loop().call_soon(task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await task


def test_usage_unknown_is_distinct_from_zero_and_capabilities_allow_unknown_limits() -> None:
    unknown = ProviderUsage()
    zero = ProviderUsage(input_tokens=0, output_tokens=0)
    caps = ModelCapabilities(
        streaming=False,
        tool_calls=False,
        model_discovery=False,
        schema_mode=SchemaMode.UNKNOWN,
    )

    assert unknown.input_tokens is None
    assert zero.input_tokens == 0
    assert caps.context_window is None
    assert caps.max_output_tokens is None


def test_contracts_reject_extra_fields_and_invalid_message_role() -> None:
    with pytest.raises(ValidationError):
        GenerationRequest(
            system_instruction="system",
            messages=(AIMessage(role="user", content="hello"),),
            model_id="m",
            temperature=0.2,
        )
    with pytest.raises(ValidationError):
        AIMessage(role="system", content="injected")


def test_prompt_and_completion_content_are_hidden_from_repr() -> None:
    prompt = "private nutrition details"
    completion = "private response details"
    request_repr = repr(request(prompt))
    result_repr = repr(result(completion))
    fake = FakeProvider()

    assert prompt not in request_repr
    assert completion not in result_repr
    assert prompt not in repr(vars(fake))


def test_model_info_preserves_provider_kind_and_explicit_unknown_schema_support() -> None:
    model = ModelInfo(
        provider=ProviderKind.CHATGPT_PLAN,
        model_id="configured-model",
        capabilities=ModelCapabilities(
            streaming=False,
            tool_calls=False,
            model_discovery=False,
            schema_mode=SchemaMode.UNKNOWN,
        ),
    )

    assert model.provider is ProviderKind.CHATGPT_PLAN
    assert model.capabilities.schema_mode is SchemaMode.UNKNOWN
