"""Account-local selection; activation never invokes a provider."""

from contextlib import nullcontext
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.connections_contracts import ConnectionContract
from app.ai.contracts import ProviderKind
from app.models import AIActiveConnection, User
from app.services.user_operation_lock import exclusive_user_lifecycle_operation


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

    def get(self) -> AISettingsView:
        self._connections._active()
        record = self._db.get(AIActiveConnection, self._owner.id, populate_existing=True)
        if record is None:
            return AISettingsView(active_connection_id=None)
        connection = self._connections.get(record.connection_id)
        return AISettingsView(active_connection_id=connection.id if connection.enabled else None)

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
            row = self._db.get(AIActiveConnection, self._owner.id, populate_existing=True)
            if payload.active_connection_id is None:
                if row is not None:
                    self._db.delete(row)
            elif row is None:
                self._db.add(AIActiveConnection(user_id=self._owner.id, connection_id=payload.active_connection_id))
            else:
                row.connection_id = payload.active_connection_id
            self._db.commit()
            return self.get()
