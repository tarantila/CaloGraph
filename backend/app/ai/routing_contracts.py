"""Public owner-scoped provider routing contracts."""

from uuid import UUID

from pydantic import Field

from app.ai.connections_contracts import MAX_CONNECTIONS, ConnectionContract
from app.ai.contracts import ProviderKind


class AIRoutingEntryPut(ConnectionContract):
    connection_id: UUID
    enabled: bool


class AIRoutingPut(ConnectionContract):
    expected_configuration_version: int = Field(ge=0)
    entries: tuple[AIRoutingEntryPut, ...] = Field(max_length=MAX_CONNECTIONS)


class AIRoutingEntryView(ConnectionContract):
    connection_id: UUID
    position: int = Field(ge=0, lt=MAX_CONNECTIONS)
    enabled: bool
    provider_kind: ProviderKind
    label: str
    model_id: str | None
    ready: bool


class AIRoutingView(ConnectionContract):
    configuration_version: int = Field(ge=0)
    entries: tuple[AIRoutingEntryView, ...] = Field(max_length=MAX_CONNECTIONS)
    primary_connection_id: UUID | None
