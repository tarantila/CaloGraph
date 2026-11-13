"""User-bound provider configuration; no model calls or endpoint probing."""

import hmac
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.connections_contracts import (
    MAX_CONNECTIONS,
    ProviderConnectionListQuery,
    ProviderConnectionPage,
    ProviderConnectionPut,
    ProviderConnectionView,
)
from app.models import AIProviderConnection, AIProviderCredential, User
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    exclusive_user_lifecycle_operation,
)


class ConnectionProblem(ValueError):
    def __init__(self, code: str, status: int, detail: str) -> None:
        self.code, self.status, self.detail = code, status, detail
        super().__init__(detail)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ProviderConnectionService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self._db, self._owner = db, owner

    def _active(self) -> None:
        owner = self._db.scalar(select(User).where(User.id == self._owner.id).execution_options(populate_existing=True))
        if owner is None or not owner.is_active:
            raise InactiveUserOperation("account is inactive")

    def _record(self, connection_id: UUID) -> AIProviderConnection | None:
        return self._db.scalar(select(AIProviderConnection).where(
            AIProviderConnection.user_id == self._owner.id, AIProviderConnection.id == connection_id,
        ).execution_options(populate_existing=True))

    def _credential(self, connection_id: UUID) -> AIProviderCredential | None:
        return self._db.get(AIProviderCredential, (self._owner.id, connection_id), populate_existing=True)

    def _view(self, record: AIProviderConnection) -> ProviderConnectionView:
        present = self._db.scalar(select(AIProviderCredential.connection_id).where(
            AIProviderCredential.user_id == self._owner.id, AIProviderCredential.connection_id == record.id,
        )) is not None
        return ProviderConnectionView(
            id=record.id, label=record.label, provider_kind=record.provider_kind,
            model_id=record.model_id, base_url=record.base_url, enabled=record.enabled,
            timeout_seconds=record.timeout_seconds, configuration_version=record.configuration_version,
            has_credential=present, created_at=_aware(record.created_at), updated_at=_aware(record.updated_at),
        )

    def get(self, connection_id: UUID) -> ProviderConnectionView:
        self._active()
        record = self._record(connection_id)
        if record is None:
            raise ConnectionProblem("ai-connection-not-found", 404, "Provider-Verbindung nicht gefunden.")
        return self._view(record)

    def list(self, query: ProviderConnectionListQuery) -> ProviderConnectionPage:
        self._active()
        statement = select(AIProviderConnection).where(AIProviderConnection.user_id == self._owner.id)
        if query.after is not None:
            statement = statement.where(AIProviderConnection.id > query.after)
        records = list(self._db.scalars(
            statement.order_by(AIProviderConnection.id).limit(query.limit + 1).execution_options(populate_existing=True),
        ))
        return ProviderConnectionPage(
            items=tuple(self._view(record) for record in records[:query.limit]),
            next_after=records[query.limit - 1].id if len(records) > query.limit else None,
        )

    def put(
        self, connection_id: UUID, payload: ProviderConnectionPut, *, _operation_locked: bool = False,
    ) -> tuple[ProviderConnectionView, bool]:
        # REST holds the existing exclusive CSRF/lifecycle dependency; internal
        # callers acquire the same lock here. Neither flag nor owner is model input.
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            self._active()
            record = self._record(connection_id)
            created = record is None
            if created and (self._db.scalar(select(func.count()).select_from(AIProviderConnection).where(
                AIProviderConnection.user_id == self._owner.id,
            )) or 0) >= MAX_CONNECTIONS:
                raise ConnectionProblem("ai-connection-limit", 409, "Maximal 20 Provider-Verbindungen sind erlaubt.")
            if record is not None and record.provider_kind != payload.provider_kind:
                raise ConnectionProblem("ai-connection-kind-immutable", 409, "Der Provider-Typ einer Verbindung kann nicht geändert werden.")
            credential = self._credential(connection_id)
            key_provided = "api_key" in payload.model_fields_set
            url = str(payload.base_url) if payload.base_url is not None else None
            if record is not None and record.base_url != url and credential is not None and not key_provided:
                raise ConnectionProblem("ai-connection-key-confirmation", 422, "Bei einer Zieländerung den Schlüssel ausdrücklich ersetzen oder entfernen.")
            has_key = payload.api_key is not None if key_provided else credential is not None
            if payload.enabled and payload.provider_kind in {"openai_api", "anthropic", "gemini"} and not has_key:
                raise ConnectionProblem("ai-connection-key-required", 422, "Für diesen Provider ist ein API-Schlüssel erforderlich.")
            encrypted: bytes | None = None
            key_changed = key_provided and ((payload.api_key is None) != (credential is None))
            if key_provided and payload.api_key is not None:
                secret = payload.api_key.get_secret_value()
                try:
                    same = credential is not None and hmac.compare_digest(
                        secret.encode(), decrypt_credential(credential.encrypted_api_key).encode(),
                    )
                except CredentialEncryptionError:
                    same = False  # A supplied replacement can recover unreadable credentials.
                key_changed = not same
                if key_changed:
                    try:
                        encrypted = encrypt_credential(secret)
                    except CredentialEncryptionError as exc:
                        raise ConnectionProblem("ai-credential-unavailable", 503, "Zugangsdaten können derzeit nicht sicher gespeichert werden.") from exc
            if record is None:
                record = AIProviderConnection(
                    user_id=self._owner.id, id=connection_id, label=payload.label,
                    provider_kind=payload.provider_kind, model_id=payload.model_id,
                    base_url=url, enabled=payload.enabled, timeout_seconds=payload.timeout_seconds,
                    configuration_version=1,
                )
                self._db.add(record)
                self._db.flush()
            else:
                changed = (
                    record.label != payload.label or record.model_id != payload.model_id
                    or record.base_url != url or record.enabled != payload.enabled
                    or record.timeout_seconds != payload.timeout_seconds or key_changed
                )
                record.label, record.model_id, record.base_url = payload.label, payload.model_id, url
                record.enabled, record.timeout_seconds = payload.enabled, payload.timeout_seconds
                if changed:
                    record.configuration_version += 1
            if key_changed:
                if payload.api_key is None and credential is not None:
                    self._db.delete(credential)
                elif encrypted is not None:
                    if credential is None:
                        self._db.add(AIProviderCredential(
                            user_id=self._owner.id, connection_id=connection_id, encrypted_api_key=encrypted,
                        ))
                    else:
                        credential.encrypted_api_key = encrypted
            self._db.commit()
            return self._view(record), created

    def delete(self, connection_id: UUID, *, _operation_locked: bool = False) -> None:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            self._active()
            record = self._record(connection_id)
            if record is not None:
                self._db.delete(record)
                self._db.commit()
