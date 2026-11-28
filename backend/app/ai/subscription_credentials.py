"""Private encrypted credential lifecycle for the experimental subscription provider."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.chatgpt_codex_device import ProtocolError, refresh_tokens, revoke_refresh_token
from app.ai.connections_contracts import MAX_CONNECTIONS
from app.ai.subscription_device_contracts import (
    DeviceGrant,
    RefreshedSubscriptionGrant,
    SubscriptionGrant,
)
from app.models import (
    AIActiveConnection,
    AIConnectionRetirement,
    AIProviderConnection,
    SubscriptionCredential,
    SubscriptionDeviceAuthorization,
    User,
)
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    exclusive_user_lifecycle_operation,
)

ISSUER = "https://auth.openai.com"
REFRESH_SKEW = timedelta(minutes=5)
MAX_ENVELOPE_BYTES = 64 * 1024


class SubscriptionCredentialProblem(RuntimeError):
    """Static safe error; never includes provider data or credential material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SubscriptionCredentialMetadata:
    state: str
    expires_at: datetime | None
    credential_version: int
    has_credential: bool


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateSubscriptionGrant:
    grant: DeviceGrant = field(repr=False)
    user_id: UUID
    connection_id: UUID
    credential_version: int

    def __repr__(self) -> str:
        return "_PrivateSubscriptionGrant(<redacted>)"


class SubscriptionCredentialStore:
    """Account-bound persistence; callers cannot select or override the owner."""

    def __init__(
        self,
        db: Session,
        *,
        owner: User,
        refresh: Callable[[DeviceGrant], RefreshedSubscriptionGrant] | None = None,
        revoke: Callable[[DeviceGrant], bool] | None = None,
    ) -> None:
        self._db = db
        self._owner_id = owner.id
        self._refresh = refresh or refresh_tokens
        self._revoke = revoke or revoke_refresh_token

    def _commit(self) -> None:
        try:
            self._db.commit()
        except SQLAlchemyError:
            self._db.rollback()
            raise SubscriptionCredentialProblem("credential_persistence_unconfirmed") from None

    def _active_owner(self) -> User:
        owner = self._db.scalar(
            select(User).where(User.id == self._owner_id).execution_options(populate_existing=True)
        )
        if owner is None or not owner.is_active:
            raise InactiveUserOperation("account is inactive")
        return owner

    def _connection(self, connection_id: UUID) -> AIProviderConnection:
        self._active_owner()
        record = self._db.scalar(select(AIProviderConnection).where(
            AIProviderConnection.user_id == self._owner_id,
            AIProviderConnection.id == connection_id,
        ).execution_options(populate_existing=True))
        if record is None:
            raise SubscriptionCredentialProblem("connection_not_found")
        if record.provider_kind != "chatgpt_plan":
            raise SubscriptionCredentialProblem("wrong_provider_kind")
        return record

    def _registration(self, connection_id: UUID) -> SubscriptionCredential | None:
        return self._db.get(
            SubscriptionCredential, (self._owner_id, connection_id), populate_existing=True,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        # SQLite returns timezone-aware SQL values as naive datetimes; persisted
        # domain timestamps are UTC by contract.
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _identity(grant: DeviceGrant) -> dict[str, str]:
        return {
            "issuer": ISSUER,
            "transport": grant.transport,
            "client_id": grant.client_id,
            "subject": grant.subject,
            "workspace_id": grant.workspace_id,
        }

    def _seal(
        self, payload: object, connection_id: UUID, *, version: int | None = None,
    ) -> bytes:
        envelope: dict[str, object] = {
            "owner": str(self._owner_id), "connection": str(connection_id), "payload": payload,
        }
        if version is not None:
            envelope["credential_version"] = version
        try:
            value = json.dumps(envelope, separators=(",", ":"))
            if len(value.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                raise ValueError
            return encrypt_credential(value)
        except (CredentialEncryptionError, TypeError, ValueError, RecursionError):
            raise SubscriptionCredentialProblem("credential_unavailable") from None

    def _open(
        self, blob: bytes, connection_id: UUID, *, version: int | None = None,
    ) -> object:
        try:
            if len(blob) > 100 * 1024:
                raise ValueError
            value = decrypt_credential(blob)
            if len(value.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                raise ValueError
            envelope = json.loads(value)
            fields = {"owner", "connection", "payload"}
            if version is not None:
                fields.add("credential_version")
            if (
                not isinstance(envelope, dict) or set(envelope) != fields
                or envelope.get("owner") != str(self._owner_id)
                or envelope.get("connection") != str(connection_id)
                or (version is not None and (
                    type(envelope.get("credential_version")) is not int
                    or envelope["credential_version"] != version
                ))
            ):
                raise SubscriptionCredentialProblem("identity_mismatch")
            return envelope["payload"]
        except (CredentialEncryptionError, TypeError, ValueError, RecursionError):
            raise SubscriptionCredentialProblem("credential_unavailable") from None

    def _binding(self, row: SubscriptionCredential) -> dict[str, str]:
        payload = self._open(row.encrypted_binding, row.connection_id)
        fields = {"issuer", "transport", "client_id", "subject", "workspace_id"}
        if (
            not isinstance(payload, dict) or set(payload) != fields
            or any(not isinstance(value, str) or not value or len(value) > 512 for value in payload.values())
            or payload["issuer"] != ISSUER or payload["transport"] != "codex_compat"
        ):
            raise SubscriptionCredentialProblem("credential_unavailable")
        return {key: str(payload[key]) for key in fields}

    def ensure_device_encryption(self, connection_id: UUID, authorization_id: UUID) -> None:
        """Fail before an external authorization is started if encryption is unavailable."""
        self._seal({"authorization": str(authorization_id), "probe": True}, connection_id)

    def seal_device_metadata(
        self,
        connection_id: UUID,
        authorization_id: UUID,
        *,
        configuration_version: int,
        credential_generation: int,
        metadata: dict[str, object],
    ) -> bytes:
        return self._seal({
            "authorization": str(authorization_id),
            "configuration_version": configuration_version,
            "credential_generation": credential_generation,
            "metadata": metadata,
        }, connection_id, version=credential_generation)

    def open_device_metadata(
        self,
        row: SubscriptionDeviceAuthorization,
    ) -> dict[str, object]:
        if row.encrypted_device is None:
            raise SubscriptionCredentialProblem("authorization_state_unavailable")
        payload = self._open(
            row.encrypted_device, row.connection_id, version=row.credential_generation,
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"authorization", "configuration_version", "credential_generation", "metadata"}
            or payload.get("authorization") != str(row.id)
            or payload.get("configuration_version") != row.configuration_version
            or payload.get("credential_generation") != row.credential_generation
            or not isinstance(payload.get("metadata"), dict)
        ):
            raise SubscriptionCredentialProblem("authorization_state_binding_invalid")
        return cast(dict[str, object], payload["metadata"])

    def _stored_grant(self, row: SubscriptionCredential, binding: dict[str, str]) -> DeviceGrant:
        if row.encrypted_tokens is None:
            raise SubscriptionCredentialProblem("credential_unavailable")
        payload = self._open(row.encrypted_tokens, row.connection_id, version=row.credential_version)
        if not isinstance(payload, str):
            raise SubscriptionCredentialProblem("credential_unavailable")
        return self._deserialize_grant(payload, binding)

    @classmethod
    def _serialize_grant(cls, grant: DeviceGrant) -> str:
        return json.dumps({
            **cls._identity(grant),
            "access_token": grant.access_token.get_secret_value(),
            "refresh_token": grant.refresh_token.get_secret_value() if grant.refresh_token else None,
            "id_token": grant.id_token.get_secret_value(),
            "token_type": grant.token_type,
            "expires_in": grant.expires_in,
            "scope": grant.scope,
            "received_at": cls._utc(grant.received_at).isoformat(),
            "expires_at": cls._utc(grant.expires_at).isoformat(),
        }, separators=(",", ":"))

    @staticmethod
    def _deserialize_grant(value: str, identity: dict[str, str]) -> DeviceGrant:
        try:
            raw = json.loads(value)
            if not isinstance(raw, dict):
                raise ValueError
            stored_identity = {
                key: raw.get(key)
                for key in ("issuer", "transport", "client_id", "subject", "workspace_id")
            }
            if stored_identity != identity:
                raise SubscriptionCredentialProblem("identity_mismatch")
            raw.pop("issuer")
            model = RefreshedSubscriptionGrant if raw.get("refresh_token") is not None else SubscriptionGrant
            grant = model.model_validate(raw)
            if not grant.inference_enabled:
                raise ValueError
            return grant
        except (TypeError, ValueError, KeyError, ValidationError, json.JSONDecodeError):
            raise SubscriptionCredentialProblem("credential_unavailable") from None

    def create_draft(self, connection_id: UUID, *, label: str = "ChatGPT (Abonnement)") -> None:
        with exclusive_user_lifecycle_operation(self._db, self._owner_id):
            self._active_owner()
            if self._db.get(AIConnectionRetirement, (self._owner_id, connection_id), populate_existing=True):
                raise SubscriptionCredentialProblem("connection_retired")
            existing = self._db.scalar(select(AIProviderConnection).where(
                AIProviderConnection.user_id == self._owner_id,
                AIProviderConnection.id == connection_id,
            ).execution_options(populate_existing=True))
            if existing is not None:
                if existing.provider_kind != "chatgpt_plan":
                    raise SubscriptionCredentialProblem("wrong_provider_kind")
                return
            if (self._db.scalar(select(func.count()).select_from(AIProviderConnection).where(
                AIProviderConnection.user_id == self._owner_id,
            )) or 0) >= MAX_CONNECTIONS:
                raise SubscriptionCredentialProblem("connection_limit")
            normalized_label = label.strip()
            if (
                not normalized_label or len(normalized_label) > 100
                or any(ord(char) < 32 or ord(char) == 127 for char in normalized_label)
            ):
                raise SubscriptionCredentialProblem("invalid_label")
            self._db.add(AIProviderConnection(
                user_id=self._owner_id, id=connection_id, label=normalized_label,
                provider_kind="chatgpt_plan", model_id=None, base_url=None, enabled=False,
                timeout_seconds=60, configuration_version=1,
            ))
            self._commit()

    def save_verified(
        self,
        connection_id: UUID,
        grant: DeviceGrant,
        *,
        expected_credential_version: int = 0,
        _operation_locked: bool = False,
        _commit: bool = True,
    ) -> SubscriptionCredentialMetadata:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner_id)
        with context:
            if self._db.get(
                AIConnectionRetirement, (self._owner_id, connection_id), populate_existing=True,
            ) is not None:
                raise SubscriptionCredentialProblem("connection_retired")
            connection = self._connection(connection_id)
            if connection.enabled:
                # Authentication never activates an inference provider.
                raise SubscriptionCredentialProblem("connection_must_be_disabled")
            if grant.transport != "codex_compat" or not grant.inference_enabled:
                raise SubscriptionCredentialProblem("unsupported_grant")
            expires_at = self._utc(grant.expires_at)
            if expires_at <= datetime.now(UTC):
                raise SubscriptionCredentialProblem("expired_grant")
            identity = self._identity(grant)
            row = self._registration(connection_id)
            actual_version = row.credential_version if row is not None else 0
            if actual_version != expected_credential_version:
                raise SubscriptionCredentialProblem("credential_version_conflict")
            if row is not None and row.state in {"refresh_claimed", "revocation_pending"}:
                raise SubscriptionCredentialProblem("credential_transition_in_progress")
            if row is not None and self._binding(row) != identity:
                raise SubscriptionCredentialProblem("identity_mismatch")
            next_version = actual_version + 1
            encrypted_binding = self._seal(identity, connection_id)
            encrypted_tokens = self._seal(self._serialize_grant(grant), connection_id, version=next_version)
            if row is None:
                row = SubscriptionCredential(
                    user_id=self._owner_id, connection_id=connection_id, state="ready",
                    credential_version=next_version, encrypted_tokens=encrypted_tokens,
                    encrypted_binding=encrypted_binding, expires_at=expires_at,
                    updated_at=datetime.now(UTC),
                )
                self._db.add(row)
            else:
                row.state = "ready"
                row.credential_version = next_version
                row.encrypted_tokens = encrypted_tokens
                row.encrypted_binding = encrypted_binding
                row.expires_at = expires_at
                row.updated_at = datetime.now(UTC)
            if _commit:
                self._commit()
            return self._metadata(row)

    @staticmethod
    def _metadata(row: SubscriptionCredential) -> SubscriptionCredentialMetadata:
        return SubscriptionCredentialMetadata(
            state=row.state, expires_at=SubscriptionCredentialStore._utc(row.expires_at) if row.expires_at else None,
            credential_version=row.credential_version,
            has_credential=row.state == "ready" and row.encrypted_tokens is not None,
        )

    def get_metadata(self, connection_id: UUID) -> SubscriptionCredentialMetadata | None:
        self._connection(connection_id)
        row = self._registration(connection_id)
        return None if row is None else self._metadata(row)

    def get_grant(self, connection_id: UUID, *, now: datetime | None = None) -> _PrivateSubscriptionGrant:
        current = self._utc(now or datetime.now(UTC))
        with exclusive_user_lifecycle_operation(self._db, self._owner_id):
            connection = self._connection(connection_id)
            row = self._registration(connection_id)
            if row is None or row.state != "ready" or row.encrypted_tokens is None or row.expires_at is None:
                raise SubscriptionCredentialProblem("credential_not_ready")
            try:
                binding = self._binding(row)
                grant = self._stored_grant(row, binding)
            except SubscriptionCredentialProblem:
                self._mark_state(row, connection, "reauth_required", disable=True)
                raise
            if binding != self._identity(grant) or self._utc(row.expires_at) != self._utc(grant.expires_at):
                self._mark_state(row, connection, "reauth_required", disable=True)
                raise SubscriptionCredentialProblem("identity_mismatch")
            if self._utc(row.expires_at) > current + REFRESH_SKEW:
                return _PrivateSubscriptionGrant(grant, self._owner_id, connection_id, row.credential_version)
            row.credential_version += 1
            version = row.credential_version
            row.state = "refresh_claimed"
            row.encrypted_tokens = None
            row.expires_at = None
            row.updated_at = current
            self._commit()  # Durable claim before the one permitted network call.

            try:
                rotated = self._refresh(grant)
            except ProtocolError as exc:
                latest = self._registration(connection_id)
                fresh_connection = self._connection(connection_id)
                if latest is None or latest.state != "refresh_claimed" or latest.credential_version != version:
                    raise SubscriptionCredentialProblem("credential_state_changed") from None
                if exc.code == "refresh_terminal":
                    self._mark_state(latest, fresh_connection, "reauth_required", disable=True)
                    raise SubscriptionCredentialProblem("reauthentication_required") from None
                self._mark_state(latest, fresh_connection, "refresh_unconfirmed", disable=True)
                raise SubscriptionCredentialProblem("refresh_unconfirmed") from None
            except Exception:
                latest = self._registration(connection_id)
                if latest is not None and latest.state == "refresh_claimed" and latest.credential_version == version:
                    self._mark_state(latest, self._connection(connection_id), "refresh_unconfirmed", disable=True)
                raise SubscriptionCredentialProblem("refresh_unconfirmed") from None

            latest = self._registration(connection_id)
            fresh_connection = self._connection(connection_id)
            if latest is None or latest.state != "refresh_claimed" or latest.credential_version != version:
                raise SubscriptionCredentialProblem("credential_state_changed")
            if self._identity(rotated) != binding or rotated.expires_at <= datetime.now(UTC):
                self._mark_state(latest, fresh_connection, "reauth_required", disable=True)
                raise SubscriptionCredentialProblem("identity_mismatch")
            next_version = version + 1
            try:
                encrypted_tokens = self._seal(self._serialize_grant(rotated), connection_id, version=next_version)
            except SubscriptionCredentialProblem:
                self._mark_state(latest, fresh_connection, "refresh_unconfirmed", disable=True)
                raise
            latest.encrypted_tokens = encrypted_tokens
            latest.expires_at = self._utc(rotated.expires_at)
            latest.credential_version = next_version
            latest.state = "ready"
            latest.updated_at = datetime.now(UTC)
            self._commit()
            return _PrivateSubscriptionGrant(rotated, self._owner_id, connection_id, latest.credential_version)

    def disconnect(
        self, connection_id: UUID, *, _operation_locked: bool = False,
    ) -> SubscriptionCredentialMetadata | None:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner_id)
        with context:
            connection = self._connection(connection_id)
            cancelled_ids = self._db.scalars(select(SubscriptionDeviceAuthorization.id).where(
                SubscriptionDeviceAuthorization.user_id == self._owner_id,
                SubscriptionDeviceAuthorization.connection_id == connection_id,
                SubscriptionDeviceAuthorization.state.in_(
                    ("starting", "pending", "polling", "exchanging"),
                ),
            )).all()
            self._db.execute(
                update(SubscriptionDeviceAuthorization)
                .where(
                    SubscriptionDeviceAuthorization.user_id == self._owner_id,
                    SubscriptionDeviceAuthorization.connection_id == connection_id,
                    SubscriptionDeviceAuthorization.state.in_(
                        ("starting", "pending", "polling", "exchanging"),
                    ),
                )
                .values(
                    state="cancelled", safe_error_code="subscription-device-cancelled",
                    encrypted_device=None, next_poll_at=None, updated_at=datetime.now(UTC),
                )
            )
            cancelled = bool(cancelled_ids)
            row = self._registration(connection_id)
            if row is None:
                if cancelled:
                    self._commit()
                return None
            grant: DeviceGrant | None = None
            encrypted_tokens = row.encrypted_tokens
            if row.state == "ready" and encrypted_tokens is not None:
                try:
                    binding = self._binding(row)
                    grant = self._stored_grant(row, binding)
                except SubscriptionCredentialProblem:
                    grant = None
            if row.state == "disconnected":
                if cancelled:
                    self._commit()
                return self._metadata(row)
            if row.state in {
                "revocation_pending", "revocation_unconfirmed", "refresh_claimed",
                "refresh_unconfirmed", "reauth_required",
            }:
                self._disable(connection)
                if row.state == "revocation_pending":
                    row.state = "revocation_unconfirmed"
                    row.credential_version += 1
                elif row.state == "refresh_claimed":
                    row.state = "refresh_unconfirmed"
                    row.credential_version += 1
                row.updated_at = datetime.now(UTC)
                self._commit()
                return self._metadata(row)
            self._disable(connection)
            row.state = "revocation_pending" if grant is not None else "revocation_unconfirmed"
            row.credential_version += 1
            version = row.credential_version
            row.encrypted_tokens = None
            row.expires_at = None
            row.updated_at = datetime.now(UTC)
            self._commit()  # Local access is disabled before revocation is attempted.
            if grant is None:
                return self._metadata(row)
            success = False
            try:
                success = self._revoke(grant)
            except Exception:
                success = False
            row = self._registration(connection_id)
            self._connection(connection_id)
            if row is not None and row.state == "revocation_pending" and row.credential_version == version:
                row.state = "disconnected" if success else "revocation_unconfirmed"
                row.updated_at = datetime.now(UTC)
                self._commit()
            return self._metadata(row) if row is not None else None

    def _disable(self, connection: AIProviderConnection) -> None:
        if connection.enabled:
            connection.enabled = False
            connection.configuration_version += 1
        self._db.execute(delete(AIActiveConnection).where(
            AIActiveConnection.user_id == self._owner_id,
            AIActiveConnection.connection_id == connection.id,
        ))

    def _mark_state(
        self, row: SubscriptionCredential, connection: AIProviderConnection, state: str, *, disable: bool,
    ) -> None:
        if disable:
            self._disable(connection)
        row.state = state
        row.credential_version += 1
        row.encrypted_tokens = None
        row.expires_at = None
        row.updated_at = datetime.now(UTC)
        self._commit()
