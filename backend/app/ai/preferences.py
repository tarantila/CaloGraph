"""Account-local selection; activation never invokes a provider."""

from contextlib import nullcontext
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.connections_contracts import ConnectionContract
from app.ai.contracts import ProviderKind
from app.ai.routing import AIRoutingService
from app.ai.routing_contracts import AIRoutingEntryPut, AIRoutingPut
from app.models import AIActiveConnection, AIProviderConnection, AIRoutingConfiguration, User
from app.services.user_operation_lock import (
    exclusive_user_lifecycle_operation,
    shared_user_operation,
)


class AISettingsPut(ConnectionContract):
    active_connection_id: UUID | None
    configuration_version: int | None = Field(ge=1)

    @model_validator(mode="after")
    def matching_selection(self) -> AISettingsPut:
        if (self.active_connection_id is None) != (self.configuration_version is None):
            raise ValueError("selection and configuration version must be provided together")
        return self


class AISettingsView(ConnectionContract):
    active_connection_id: UUID | None


class AISettingsService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self._db, self._owner = db, owner
        self._connections = ProviderConnectionService(db, owner=owner)

    def get(self, *, _operation_locked: bool = False) -> AISettingsView:
        context = nullcontext() if _operation_locked else shared_user_operation(self._db, self._owner.id)
        with context:
            self._connections._active()
            if self._db.get(AIRoutingConfiguration, self._owner.id, populate_existing=True) is not None:
                routing = AIRoutingService(self._db, owner=self._owner).get(_operation_locked=True)
                return AISettingsView(active_connection_id=routing.primary_connection_id)
            return self._get_legacy()

    def _get_legacy(self) -> AISettingsView:
        connection_id = self._db.scalar(
            select(AIActiveConnection.connection_id)
            .join(
                AIProviderConnection,
                (AIProviderConnection.user_id == AIActiveConnection.user_id)
                & (AIProviderConnection.id == AIActiveConnection.connection_id),
            )
            .where(
                AIActiveConnection.user_id == self._owner.id,
                AIProviderConnection.enabled.is_(True),
            )
        )
        return AISettingsView(active_connection_id=connection_id)

    def put(self, payload: AISettingsPut, *, _operation_locked: bool = False) -> AISettingsView:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            self._connections._active()
            if payload.active_connection_id is not None:
                connection = self._connections.get(payload.active_connection_id)
                if not connection.enabled or connection.provider_kind == ProviderKind.CHATGPT_PLAN:
                    raise ConnectionProblem("ai-connection-not-enabled", 422, "Bitte eine aktivierte verfügbare Verbindung wählen.")
                if connection.configuration_version != payload.configuration_version:
                    raise ConnectionProblem("ai-configuration-changed", 409, "Die Verbindung wurde geändert. Bitte neu laden.")
            if self._db.get(AIRoutingConfiguration, self._owner.id, populate_existing=True) is not None:
                current = AIRoutingService(self._db, owner=self._owner).get(_operation_locked=True)
                ordered = list(current.entries)
                if payload.active_connection_id is None:
                    changed = [AIRoutingEntryPut(connection_id=item.connection_id, enabled=False) for item in ordered]
                else:
                    chosen = next((item for item in ordered if item.connection_id == payload.active_connection_id), None)
                    if chosen is None:
                        raise ConnectionProblem("ai-routing-connections-changed", 409, "Die Verbindungen haben sich geändert. Bitte neu laden.")
                    ordered.remove(chosen)
                    ordered.insert(0, chosen)
                    changed = [
                        AIRoutingEntryPut(
                            connection_id=item.connection_id,
                            enabled=item.connection_id == payload.active_connection_id,
                        ) for item in ordered
                    ]
                AIRoutingService(self._db, owner=self._owner).put(
                    AIRoutingPut(
                        expected_configuration_version=current.configuration_version,
                        entries=tuple(changed),
                    ),
                    _operation_locked=True,
                )
                return self.get(_operation_locked=True)
            row = self._db.get(AIActiveConnection, self._owner.id, populate_existing=True)
            if payload.active_connection_id is None:
                if row is not None:
                    self._db.delete(row)
            elif row is None:
                self._db.add(AIActiveConnection(user_id=self._owner.id, connection_id=payload.active_connection_id))
            else:
                row.connection_id = payload.active_connection_id
            self._db.commit()
            return self.get(_operation_locked=True)
