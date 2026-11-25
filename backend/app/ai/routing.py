"""Account-local provider order and activation settings."""

from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.generation import GENERATION_PROVIDERS
from app.ai.routing_contracts import (
    AIRoutingEntryPut,
    AIRoutingEntryView,
    AIRoutingPut,
    AIRoutingView,
)
from app.models import (
    AIActiveConnection,
    AIProviderConnection,
    AIRoutingConfiguration,
    AIRoutingEntry,
    User,
)
from app.services.user_operation_lock import (
    exclusive_user_lifecycle_operation,
    shared_user_operation,
)


class AIRoutingService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self._db, self._owner = db, owner

    @staticmethod
    def _ready(connection: AIProviderConnection) -> bool:
        return bool(
            connection.enabled
            and connection.model_id
            and connection.provider_kind in GENERATION_PROVIDERS
        )

    def _current(self) -> tuple[AIRoutingConfiguration | None, list[tuple[UUID, bool]]]:
        root = self._db.get(AIRoutingConfiguration, self._owner.id, populate_existing=True)
        connections = list(self._db.scalars(
            select(AIProviderConnection).where(AIProviderConnection.user_id == self._owner.id)
        ))
        by_id = {connection.id: connection for connection in connections}
        if root is None:
            active = self._db.get(AIActiveConnection, self._owner.id, populate_existing=True)
            preferred = active.connection_id if active and active.connection_id in by_id else None
            if preferred is not None and not by_id[preferred].enabled:
                preferred = None
            ordered = sorted(by_id, key=lambda value: value.bytes)
            if preferred is not None:
                ordered.remove(preferred)
                ordered.insert(0, preferred)
            return None, [(item, item == preferred) for item in ordered]
        persisted = list(self._db.scalars(
            select(AIRoutingEntry).where(AIRoutingEntry.user_id == self._owner.id)
            .order_by(AIRoutingEntry.position)
        ))
        listed = {entry.connection_id for entry in persisted}
        rows = [(entry.connection_id, entry.enabled) for entry in persisted if entry.connection_id in by_id]
        rows.extend((item, False) for item in sorted(set(by_id) - listed, key=lambda value: value.bytes))
        return root, rows

    def get(self, *, _operation_locked: bool = False) -> AIRoutingView:
        context = nullcontext() if _operation_locked else shared_user_operation(self._db, self._owner.id)
        with context:
            ProviderConnectionService(self._db, owner=self._owner)._active()
            root, rows = self._current()
            connections = {item.id: item for item in self._db.scalars(
                select(AIProviderConnection).where(AIProviderConnection.user_id == self._owner.id)
            )}
            entries: list[AIRoutingEntryView] = []
            first_enabled = next((identifier for identifier, enabled in rows if enabled), None)
            primary = (
                first_enabled
                if first_enabled is not None and self._ready(connections[first_enabled])
                else None
            )
            for position, (identifier, enabled) in enumerate(rows):
                connection = connections[identifier]
                entries.append(AIRoutingEntryView(
                    connection_id=identifier,
                    position=position,
                    enabled=enabled,
                    provider_kind=connection.provider_kind,
                    label=connection.label,
                    model_id=connection.model_id,
                    ready=self._ready(connection),
                ))
            return AIRoutingView(
                configuration_version=root.configuration_version if root is not None else 0,
                entries=tuple(entries),
                primary_connection_id=primary,
            )

    def _validate(self, entries: tuple[AIRoutingEntryPut, ...]) -> list[tuple[AIProviderConnection, bool]]:
        if len({entry.connection_id for entry in entries}) != len(entries):
            raise ConnectionProblem("ai-routing-duplicate-connection", 422, "Eine Verbindung darf nur einmal vorkommen.")
        current = {
            connection.id: connection for connection in self._db.scalars(
                select(AIProviderConnection).where(AIProviderConnection.user_id == self._owner.id)
            )
        }
        if set(current) != {entry.connection_id for entry in entries}:
            raise ConnectionProblem("ai-routing-connections-changed", 409, "Die Verbindungen haben sich geändert. Bitte neu laden.")
        validated = [(current[entry.connection_id], entry.enabled) for entry in entries]
        for connection, enabled in validated:
            if enabled and not self._ready(connection):
                raise ConnectionProblem("ai-routing-connection-not-ready", 422, "Nur verfügbare Verbindungen können aktiviert werden.")
        return validated

    def put(self, payload: AIRoutingPut, *, _operation_locked: bool = False) -> AIRoutingView:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            ProviderConnectionService(self._db, owner=self._owner)._active()
            self._db.execute(select(User.id).where(User.id == self._owner.id).with_for_update())
            validated = self._validate(payload.entries)
            desired = [(connection.id, enabled) for connection, enabled in validated]
            root = self._db.get(AIRoutingConfiguration, self._owner.id, populate_existing=True)
            version = root.configuration_version if root is not None else 0
            persisted = [] if root is None else [
                (entry.connection_id, entry.enabled)
                for entry in self._db.scalars(
                    select(AIRoutingEntry).where(AIRoutingEntry.user_id == self._owner.id)
                    .order_by(AIRoutingEntry.position)
                )
            ]
            persisted_matches = desired == persisted
            if version == payload.expected_configuration_version + 1 and persisted_matches:
                return self.get(_operation_locked=True)
            if version != payload.expected_configuration_version:
                raise ConnectionProblem("ai-routing-version-conflict", 409, "Die Anbieterreihenfolge wurde geändert. Bitte neu laden.")
            if persisted_matches and root is not None:
                return self.get(_operation_locked=True)
            if root is None:
                root = AIRoutingConfiguration(user_id=self._owner.id, configuration_version=1)
                self._db.add(root)
                next_version = 1
            else:
                root.configuration_version += 1
                root.updated_at = datetime.now(UTC)
                next_version = root.configuration_version
            self._db.execute(delete(AIRoutingEntry).where(AIRoutingEntry.user_id == self._owner.id))
            self._db.flush()
            self._db.add_all(AIRoutingEntry(
                user_id=self._owner.id,
                connection_id=connection.id,
                position=position,
                enabled=enabled,
            ) for position, (connection, enabled) in enumerate(validated))
            primary = next((connection.id for connection, enabled in validated if enabled), None)
            legacy = self._db.get(AIActiveConnection, self._owner.id, populate_existing=True)
            if primary is None:
                if legacy is not None:
                    self._db.delete(legacy)
            elif legacy is None:
                self._db.add(AIActiveConnection(user_id=self._owner.id, connection_id=primary))
            else:
                legacy.connection_id = primary
            self._db.commit()
            result = self.get(_operation_locked=True)
            assert result.configuration_version == next_version
            return result
