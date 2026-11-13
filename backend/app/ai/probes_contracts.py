"""Bounded settings/discovery contracts; no health-data transmission consent."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.ai.connections_contracts import ConnectionContract
from app.ai.contracts import ProviderKind

CONNECTION_PRIVACY_VERSION = "connection-metadata-2026-11-13"
DiscoveredModelId = Annotated[str, Field(min_length=1, max_length=200)]
CheckStatus = Literal[
    "checking", "interrupted", "available", "model_not_listed", "manual_selection",
    "authentication", "rate_limited", "destination_blocked", "timeout", "unavailable",
    "invalid_response", "unsupported", "credential_unavailable",
]


class ProviderDescriptor(ConnectionContract):
    kind: ProviderKind
    availability: Literal["available", "planned"]
    authentication: Literal["api_key", "optional_api_key", "chatgpt_oauth"]
    custom_endpoint: bool
    model_discovery: bool
    manual_model: bool
    streaming: bool | None = None
    tool_calls: bool | None = None
    structured_output: bool | None = None


class ProviderCatalog(ConnectionContract):
    providers: tuple[ProviderDescriptor, ...] = Field(max_length=6)
    privacy_version: str
    health_data_transmission_enabled: Literal[False] = False


class ConnectionCheckPut(ConnectionContract):
    configuration_version: int = Field(ge=1)
    privacy_version: Literal["connection-metadata-2026-11-13"]


class ConnectionCheckView(ConnectionContract):
    id: UUID
    connection_id: UUID
    configuration_version: int
    privacy_version: str
    started_at: datetime
    completed_at: datetime | None
    status: CheckStatus
    models: tuple[DiscoveredModelId, ...] = Field(max_length=100)
    models_truncated: bool
    selected_model_available: bool | None
    generation_verified: Literal[False] = False
