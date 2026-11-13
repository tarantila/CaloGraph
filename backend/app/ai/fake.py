"""Deterministic in-memory provider for tests; never use as a production adapter."""

import asyncio
from collections import deque
from collections.abc import Iterable

from app.ai.contracts import (
    GenerationRequest,
    GenerationResult,
    ModelCapabilities,
    ModelInfo,
    ProviderError,
    ProviderErrorCode,
    ProviderKind,
    SchemaMode,
)


class FakeProvider:
    """Returns scripted responses in order without retaining or logging requests."""

    kind = ProviderKind.LOCAL

    def __init__(
        self,
        scripted: Iterable[GenerationResult | ProviderError] = (),
        *,
        models: Iterable[ModelInfo] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._scripted = deque(scripted)
        self._gate = gate
        self._models = tuple(models) if models is not None else (
            ModelInfo(
                provider=self.kind,
                model_id="fake-model",
                capabilities=ModelCapabilities(
                    streaming=False,
                    tool_calls=False,
                    model_discovery=True,
                    schema_mode=SchemaMode.UNKNOWN,
                ),
            ),
        )

    async def discover_models(self) -> tuple[ModelInfo, ...]:
        return self._models

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        del request  # Deliberately do not retain or inspect prompt content.
        if self._gate is not None:
            await self._gate.wait()
        if not self._scripted:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE)
        scripted = self._scripted.popleft()
        if isinstance(scripted, ProviderError):
            raise ProviderError(scripted.code)
        return scripted
