"""Owner-scoped server-side device authorization for the experimental plan flow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.chatgpt_codex_device import (
    DEVICE_URI,
    ProtocolError,
    begin_device_authorization,
    exchange_device_code,
    poll_device_authorization,
)
from app.ai.connections_contracts import MAX_CONNECTIONS
from app.ai.subscription_authorization_contracts import (
    SubscriptionAuthorizationStart,
    SubscriptionAuthorizationView,
    SubscriptionStatus,
)
from app.ai.subscription_credentials import (
    SubscriptionCredentialProblem,
    SubscriptionCredentialStore,
)
from app.ai.subscription_device_contracts import DeviceGrant
from app.models import (
    AIConnectionRetirement,
    AIProviderConnection,
    SubscriptionCredential,
    SubscriptionDeviceAuthorization,
    SubscriptionDeviceAuthorizationReceipt,
    User,
    utcnow,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    exclusive_user_lifecycle_operation,
)

MAX_FULL_ATTEMPTS = 40
MAX_STARTS_PER_MINUTE = 10
START_CLAIM_TIMEOUT = timedelta(seconds=60)
IO_CLAIM_TIMEOUT = timedelta(minutes=2)
DEVICE_TTL = timedelta(minutes=15)
MAX_DEVICE_TTL = timedelta(hours=1)
_CANCEL_RECEIPT_HASH = hashlib.sha256(b"cancelled-before-start").hexdigest()

StartCall = Callable[[], dict[str, object]]
PollCall = Callable[[str, str], tuple[str, dict[str, object] | None]]
ExchangeCall = Callable[[str, str, str], DeviceGrant]


class SubscriptionDeviceProblem(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SubscriptionDeviceAuthorizationService:
    def __init__(
        self,
        db: Session,
        *,
        owner: User,
        start_call: StartCall | None = None,
        poll_call: PollCall | None = None,
        exchange_call: ExchangeCall | None = None,
    ) -> None:
        self._db, self._owner_id = db, owner.id
        self._start_call = start_call or begin_device_authorization
        self._poll_call = poll_call or poll_device_authorization
        self._exchange_call = exchange_call or exchange_device_code
        self._store = SubscriptionCredentialStore(db, owner=owner)

    def _commit(self) -> None:
        try:
            self._db.commit()
        except SQLAlchemyError:
            self._db.rollback()
            raise SubscriptionDeviceProblem("subscription-device-state-unconfirmed", 503) from None

    def _active(self) -> User:
        owner = self._db.scalar(
            select(User).where(User.id == self._owner_id).execution_options(populate_existing=True)
        )
        if owner is None or not owner.is_active:
            raise InactiveUserOperation("account is inactive")
        return owner

    def _connection(self, connection_id: UUID) -> AIProviderConnection:
        self._active()
        record = self._db.scalar(select(AIProviderConnection).where(
            AIProviderConnection.user_id == self._owner_id,
            AIProviderConnection.id == connection_id,
        ).execution_options(populate_existing=True))
        if record is None:
            raise SubscriptionDeviceProblem("subscription-connection-not-found", 404)
        if record.provider_kind != "chatgpt_plan":
            raise SubscriptionDeviceProblem("subscription-connection-kind-invalid")
        return record

    def _attempt(self, connection_id: UUID, attempt_id: UUID) -> SubscriptionDeviceAuthorization:
        row = self._db.get(
            SubscriptionDeviceAuthorization, (self._owner_id, attempt_id), populate_existing=True,
        )
        if row is None or row.connection_id != connection_id:
            raise SubscriptionDeviceProblem("subscription-authorization-not-found", 404)
        return row

    def _credential_generation(self, connection_id: UUID) -> tuple[int, str | None]:
        row = self._db.get(SubscriptionCredential, (self._owner_id, connection_id), populate_existing=True)
        return (row.credential_version, row.state) if row is not None else (0, None)

    def status(self, connection_id: UUID) -> SubscriptionStatus:
        self._connection(connection_id)
        metadata = self._store.get_metadata(connection_id)
        if metadata is None:
            return SubscriptionStatus(
                state="not_connected", credential_version=0, expires_at=None, has_credential=False,
            )
        return SubscriptionStatus(
            state=metadata.state, credential_version=metadata.credential_version,
            expires_at=metadata.expires_at, has_credential=metadata.has_credential,
        )

    def _expire_stale(self, connection_id: UUID | None = None) -> bool:
        now = utcnow()
        statement = select(SubscriptionDeviceAuthorization).where(
            SubscriptionDeviceAuthorization.user_id == self._owner_id,
            SubscriptionDeviceAuthorization.state.in_(
                ("starting", "pending", "polling", "exchanging"),
            ),
        )
        if connection_id is not None:
            statement = statement.where(SubscriptionDeviceAuthorization.connection_id == connection_id)
        changed = False
        for row in self._db.scalars(statement):
            timeout = START_CLAIM_TIMEOUT if row.state == "starting" else (
                IO_CLAIM_TIMEOUT if row.state in {"polling", "exchanging"} else None
            )
            if row.state == "pending" and _aware(row.expires_at) <= now:
                row.state, row.safe_error_code = "expired", "subscription-device-expired"
            elif timeout is not None and _aware(row.updated_at) + timeout <= now:
                row.state, row.safe_error_code = "interrupted", "subscription-device-interrupted"
            else:
                continue
            row.encrypted_device = None
            row.next_poll_at = None
            row.updated_at = now
            changed = True
        return changed

    def _cancel_active(self, connection_id: UUID, *, except_id: UUID | None = None) -> None:
        statement = select(SubscriptionDeviceAuthorization).where(
            SubscriptionDeviceAuthorization.user_id == self._owner_id,
            SubscriptionDeviceAuthorization.connection_id == connection_id,
            SubscriptionDeviceAuthorization.state.in_(
                ("starting", "pending", "polling", "exchanging"),
            ),
        )
        if except_id is not None:
            statement = statement.where(SubscriptionDeviceAuthorization.id != except_id)
        for row in self._db.scalars(statement):
            self._scrub(row, "cancelled", "subscription-device-cancelled")

    @staticmethod
    def _scrub(row: SubscriptionDeviceAuthorization, state: str, error: str | None) -> None:
        row.state = state
        row.safe_error_code = error
        row.encrypted_device = None
        row.next_poll_at = None
        row.updated_at = utcnow()

    def _view(self, row: SubscriptionDeviceAuthorization) -> SubscriptionAuthorizationView:
        state, error = row.state, row.safe_error_code
        now = utcnow()
        if state == "starting" and _aware(row.updated_at) + START_CLAIM_TIMEOUT <= now:
            state, error = "interrupted", "subscription-device-interrupted"
        elif state == "pending" and _aware(row.expires_at) <= now:
            state, error = "expired", "subscription-device-expired"
        elif state in {"polling", "exchanging"} and _aware(row.updated_at) + IO_CLAIM_TIMEOUT <= now:
            state, error = "interrupted", "subscription-device-interrupted"
        code = None
        verification_uri = None
        if state == "pending" and row.encrypted_device is not None:
            try:
                metadata = self._store.open_device_metadata(row)
            except SubscriptionCredentialProblem:
                raise SubscriptionDeviceProblem("subscription-device-state-unavailable", 503) from None
            value = metadata.get("user_code")
            if not isinstance(value, str) or not 1 <= len(value) <= 64:
                raise SubscriptionDeviceProblem("subscription-device-state-unavailable", 503)
            code, verification_uri = value, DEVICE_URI
        return SubscriptionAuthorizationView(
            id=row.id, state=state, safe_error_code=error, oauth_status=row.oauth_status,
            created_at=_aware(row.created_at), expires_at=_aware(row.expires_at),
            next_poll_at=_aware(row.next_poll_at) if state == "pending" and row.next_poll_at else None,
            poll_interval_seconds=row.poll_interval_seconds,
            user_code=code, verification_uri=verification_uri,
        )

    def current(self, connection_id: UUID) -> SubscriptionAuthorizationView | None:
        self._connection(connection_id)
        row = self._db.scalar(select(SubscriptionDeviceAuthorization).where(
            SubscriptionDeviceAuthorization.user_id == self._owner_id,
            SubscriptionDeviceAuthorization.connection_id == connection_id,
            SubscriptionDeviceAuthorization.state.in_(
                ("starting", "pending", "polling", "exchanging"),
            ),
        ).order_by(SubscriptionDeviceAuthorization.created_at.desc()).limit(1))
        return self._view(row) if row is not None else None

    def get(self, connection_id: UUID, attempt_id: UUID) -> SubscriptionAuthorizationView:
        self._connection(connection_id)
        return self._view(self._attempt(connection_id, attempt_id))

    def start(
        self,
        connection_id: UUID,
        attempt_id: UUID,
        payload: SubscriptionAuthorizationStart,
        *,
        _operation_locked: bool = False,
    ) -> tuple[SubscriptionAuthorizationView, bool]:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner_id)
        with context:
            self._active()
            if self._db.get(AIConnectionRetirement, (self._owner_id, connection_id), populate_existing=True):
                raise SubscriptionDeviceProblem("subscription-connection-retired")
            request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
            receipt = self._db.get(
                SubscriptionDeviceAuthorizationReceipt, (self._owner_id, attempt_id), populate_existing=True,
            )
            if receipt is not None:
                if receipt.connection_id != connection_id or receipt.request_hash != request_hash:
                    raise SubscriptionDeviceProblem("subscription-authorization-conflict")
                existing = self._db.get(
                    SubscriptionDeviceAuthorization, (self._owner_id, attempt_id), populate_existing=True,
                )
                if existing is None:
                    raise SubscriptionDeviceProblem("subscription-authorization-retired")
                return self._view(existing), False

            self._expire_stale(connection_id)
            connection = self._db.scalar(select(AIProviderConnection).where(
                AIProviderConnection.user_id == self._owner_id,
                AIProviderConnection.id == connection_id,
            ).execution_options(populate_existing=True))
            if connection is None:
                count = self._db.scalar(select(func.count()).select_from(AIProviderConnection).where(
                    AIProviderConnection.user_id == self._owner_id,
                )) or 0
                if count >= MAX_CONNECTIONS:
                    raise SubscriptionDeviceProblem("ai-connection-limit")
                connection = AIProviderConnection(
                    user_id=self._owner_id, id=connection_id, label="ChatGPT (Abonnement)",
                    provider_kind="chatgpt_plan", model_id=None, base_url=None,
                    enabled=False, timeout_seconds=60, configuration_version=1,
                )
                self._db.add(connection)
                self._db.flush()
            elif connection.provider_kind != "chatgpt_plan":
                raise SubscriptionDeviceProblem("subscription-connection-kind-invalid")

            credential_generation, credential_state = self._credential_generation(connection_id)
            if credential_generation != payload.expected_credential_version:
                raise SubscriptionDeviceProblem("subscription-credential-version-conflict")
            if credential_state == "ready":
                raise SubscriptionDeviceProblem("subscription-already-connected")
            if credential_state in {"refresh_claimed", "revocation_pending"}:
                raise SubscriptionDeviceProblem("subscription-credential-transition-in-progress")
            if connection.enabled:
                raise SubscriptionDeviceProblem("subscription-connection-must-be-disabled")

            cutoff = utcnow() - timedelta(minutes=1)
            starts = self._db.scalar(select(func.count()).select_from(SubscriptionDeviceAuthorizationReceipt).where(
                SubscriptionDeviceAuthorizationReceipt.user_id == self._owner_id,
                SubscriptionDeviceAuthorizationReceipt.created_at >= cutoff,
            )) or 0
            if starts >= MAX_STARTS_PER_MINUTE:
                raise SubscriptionDeviceProblem("subscription-device-rate-limit", 429)

            self._store.ensure_device_encryption(connection_id, attempt_id)
            self._cancel_active(connection_id)
            now = utcnow()
            self._db.add(SubscriptionDeviceAuthorizationReceipt(
                user_id=self._owner_id, authorization_id=attempt_id, connection_id=connection_id,
                request_hash=request_hash, created_at=now,
            ))
            row = SubscriptionDeviceAuthorization(
                user_id=self._owner_id, id=attempt_id, connection_id=connection_id,
                configuration_version=connection.configuration_version,
                credential_generation=credential_generation, request_hash=request_hash,
                state="starting", safe_error_code=None, oauth_status=None,
                encrypted_device=None, poll_interval_seconds=5, next_poll_at=None,
                expires_at=now + DEVICE_TTL, created_at=now, updated_at=now,
            )
            self._db.add(row)
            self._prune_terminal()
            self._commit()

            try:
                metadata = self._start_call()
                interval, expires_at = self._validate_device_metadata(metadata)
                self._check_snapshots(row, connection_id, expected_state="starting")
                row = self._attempt(connection_id, attempt_id)
                row.encrypted_device = self._store.seal_device_metadata(
                    connection_id, attempt_id,
                    configuration_version=row.configuration_version,
                    credential_generation=row.credential_generation,
                    metadata=metadata,
                )
                row.poll_interval_seconds = interval
                row.next_poll_at = utcnow() + timedelta(seconds=interval)
                row.expires_at = expires_at
                row.state = "pending"
                row.updated_at = utcnow()
                self._commit()
            except ProtocolError as exc:
                self._fail_attempt(connection_id, attempt_id, exc, expected_state="starting")
                raise SubscriptionDeviceProblem("subscription-device-provider-failed", 502) from None
            except SubscriptionDeviceProblem as exc:
                if exc.code == "subscription-device-response-invalid":
                    row = self._attempt(connection_id, attempt_id)
                    if row.state == "starting":
                        self._scrub(row, "failed", exc.code)
                        self._commit()
                raise
            except Exception:
                # The durable starting claim remains. Replaying this UUID never
                # repeats an uncertain external start request.
                raise SubscriptionDeviceProblem("subscription-device-state-unconfirmed", 503) from None
            return self._view(row), True

    @staticmethod
    def _validate_device_metadata(metadata: dict[str, object]) -> tuple[int, datetime]:
        if set(metadata) != {"device_auth_id", "user_code", "interval", "expires_at"}:
            raise SubscriptionDeviceProblem("subscription-device-response-invalid", 502)
        device_id, code, interval, expiry = (
            metadata.get("device_auth_id"), metadata.get("user_code"),
            metadata.get("interval"), metadata.get("expires_at"),
        )
        if (
            not isinstance(device_id, str) or not 1 <= len(device_id) <= 1024
            or not isinstance(code, str) or not 1 <= len(code) <= 64
            or isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 60
            or not isinstance(expiry, str) or len(expiry) > 64
        ):
            raise SubscriptionDeviceProblem("subscription-device-response-invalid", 502)
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            raise SubscriptionDeviceProblem("subscription-device-response-invalid", 502) from None
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise SubscriptionDeviceProblem("subscription-device-response-invalid", 502)
        expires_at = expires_at.astimezone(UTC)
        now = utcnow()
        if expires_at <= now or expires_at > now + MAX_DEVICE_TTL:
            raise SubscriptionDeviceProblem("subscription-device-response-invalid", 502)
        return interval, expires_at

    def _check_snapshots(
        self, row: SubscriptionDeviceAuthorization, connection_id: UUID, *, expected_state: str,
    ) -> None:
        row = self._attempt(connection_id, row.id)
        if row.state != expected_state:
            raise SubscriptionDeviceProblem("subscription-device-stale")
        now = utcnow()
        if _aware(row.expires_at) <= now:
            self._scrub(row, "expired", "subscription-device-expired")
            self._commit()
            raise SubscriptionDeviceProblem("subscription-device-stale")
        timeout = START_CLAIM_TIMEOUT if expected_state == "starting" else IO_CLAIM_TIMEOUT
        if expected_state != "pending" and _aware(row.updated_at) + timeout <= now:
            self._scrub(row, "interrupted", "subscription-device-interrupted")
            self._commit()
            raise SubscriptionDeviceProblem("subscription-device-stale")
        connection = self._connection(connection_id)
        generation, _state = self._credential_generation(connection_id)
        if (
            connection.configuration_version != row.configuration_version
            or generation != row.credential_generation
        ):
            self._scrub(row, "cancelled", "subscription-device-stale")
            self._commit()
            raise SubscriptionDeviceProblem("subscription-device-stale")

    def _fail_attempt(
        self, connection_id: UUID, attempt_id: UUID, exc: ProtocolError, *, expected_state: str,
    ) -> None:
        row = self._attempt(connection_id, attempt_id)
        if row.state != expected_state:
            return
        row.state = "failed"
        row.safe_error_code = f"subscription-device-{exc.code}"[:64]
        row.oauth_status = exc.status
        row.encrypted_device = None
        row.next_poll_at = None
        row.updated_at = utcnow()
        self._commit()

    def _prune_terminal(self) -> None:
        terminal = ("completed", "denied", "failed", "cancelled", "expired", "interrupted")
        count = self._db.scalar(select(func.count()).select_from(SubscriptionDeviceAuthorization).where(
            SubscriptionDeviceAuthorization.user_id == self._owner_id,
        )) or 0
        excess = count + 1 - MAX_FULL_ATTEMPTS
        if excess <= 0:
            return
        old_ids = list(self._db.scalars(select(SubscriptionDeviceAuthorization.id).where(
            SubscriptionDeviceAuthorization.user_id == self._owner_id,
            SubscriptionDeviceAuthorization.state.in_(terminal),
        ).order_by(SubscriptionDeviceAuthorization.created_at.asc()).limit(excess)))
        if old_ids:
            self._db.execute(delete(SubscriptionDeviceAuthorization).where(
                SubscriptionDeviceAuthorization.user_id == self._owner_id,
                SubscriptionDeviceAuthorization.id.in_(old_ids),
            ))

    def poll(
        self, connection_id: UUID, attempt_id: UUID, *, _operation_locked: bool = False,
    ) -> SubscriptionAuthorizationView:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner_id)
        with context:
            self._active()
            row = self._attempt(connection_id, attempt_id)
            if self._expire_stale(connection_id):
                self._commit()
                row = self._attempt(connection_id, attempt_id)
            if row.state != "pending":
                return self._view(row)
            try:
                self._check_snapshots(row, connection_id, expected_state="pending")
            except SubscriptionDeviceProblem as exc:
                if exc.code == "subscription-device-stale":
                    return self._view(self._attempt(connection_id, attempt_id))
                raise
            now = utcnow()
            if row.next_poll_at is not None and _aware(row.next_poll_at) > now:
                return self._view(row)
            try:
                metadata = self._store.open_device_metadata(row)
            except SubscriptionCredentialProblem:
                self._scrub(row, "failed", "subscription-device-state-unavailable")
                self._commit()
                return self._view(row)
            device_id, code = metadata.get("device_auth_id"), metadata.get("user_code")
            if not isinstance(device_id, str) or not isinstance(code, str):
                self._scrub(row, "failed", "subscription-device-state-unavailable")
                self._commit()
                return self._view(row)
            row.state, row.updated_at = "polling", now
            self._commit()  # Claim before one bounded provider poll.
            try:
                status, result = self._poll_call(device_id, code)
            except ProtocolError as exc:
                self._fail_attempt(connection_id, attempt_id, exc, expected_state="polling")
                return self._view(self._attempt(connection_id, attempt_id))
            row = self._attempt(connection_id, attempt_id)
            try:
                self._check_snapshots(row, connection_id, expected_state="polling")
            except SubscriptionDeviceProblem as exc:
                if exc.code == "subscription-device-stale":
                    return self._view(self._attempt(connection_id, attempt_id))
                raise
            if status == "pending":
                row.state = "pending"
                row.next_poll_at = utcnow() + timedelta(seconds=row.poll_interval_seconds)
                row.updated_at = utcnow()
                self._commit()
                return self._view(row)
            if status == "denied":
                self._scrub(row, "denied", "subscription-device-denied")
                self._commit()
                return self._view(row)
            if status != "authorized" or result is None or set(result) != {
                "authorization_code", "code_verifier", "code_challenge",
            }:
                self._scrub(row, "failed", "subscription-device-response-invalid")
                self._commit()
                return self._view(row)
            auth_code, verifier, challenge = (
                result.get("authorization_code"), result.get("code_verifier"), result.get("code_challenge"),
            )
            if (
                not isinstance(auth_code, str) or not auth_code
                or not isinstance(verifier, str) or not verifier
                or not isinstance(challenge, str) or not challenge
            ):
                self._scrub(row, "failed", "subscription-device-response-invalid")
                self._commit()
                return self._view(row)

            row.state = "exchanging"
            row.encrypted_device = None
            row.updated_at = utcnow()
            self._commit()  # One-shot exchange claim; code/verifier remain memory-only.
            try:
                self._check_snapshots(row, connection_id, expected_state="exchanging")
                self._commit()  # End the read transaction before network exchange.
                grant = self._exchange_call(auth_code, verifier, challenge)
                if not grant.inference_enabled:
                    raise ProtocolError("invalid_token")
                self._check_snapshots(row, connection_id, expected_state="exchanging")
                self._store.save_verified(
                    connection_id, grant,
                    expected_credential_version=row.credential_generation,
                    _operation_locked=True, _commit=False,
                )
                row = self._attempt(connection_id, attempt_id)
                row.state, row.safe_error_code = "completed", None
                row.oauth_status = None
                row.encrypted_device = None
                row.next_poll_at = None
                row.updated_at = utcnow()
                self._commit()
            except ProtocolError as exc:
                self._db.rollback()
                self._fail_attempt(connection_id, attempt_id, exc, expected_state="exchanging")
            except SubscriptionDeviceProblem as exc:
                self._db.rollback()
                row = self._attempt(connection_id, attempt_id)
                if row.state != "exchanging" or exc.code == "subscription-device-state-unconfirmed":
                    return self._view(row)
                self._scrub(row, "failed", "subscription-device-exchange-failed")
                self._commit()
            except SubscriptionCredentialProblem:
                self._db.rollback()
                row = self._attempt(connection_id, attempt_id)
                if row.state == "exchanging":
                    self._scrub(row, "failed", "subscription-device-exchange-failed")
                    self._commit()
            except Exception:
                # The exchanging claim remains durable, and the authorization
                # code is never retried after an ambiguous exchange.
                self._db.rollback()
                row = self._attempt(connection_id, attempt_id)
                if row.state == "exchanging":
                    self._scrub(row, "interrupted", "subscription-device-interrupted")
                    self._commit()
            return self._view(self._attempt(connection_id, attempt_id))

    def cancel(
        self, connection_id: UUID, attempt_id: UUID, *, _operation_locked: bool = False,
    ) -> None:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner_id)
        with context:
            self._active()
            connection = self._db.get(
                AIProviderConnection, (self._owner_id, connection_id), populate_existing=True,
            )
            if connection is not None and connection.provider_kind != "chatgpt_plan":
                raise SubscriptionDeviceProblem("subscription-connection-kind-invalid")
            receipt = self._db.get(
                SubscriptionDeviceAuthorizationReceipt, (self._owner_id, attempt_id), populate_existing=True,
            )
            row = self._db.get(
                SubscriptionDeviceAuthorization, (self._owner_id, attempt_id), populate_existing=True,
            )
            if row is not None and row.connection_id != connection_id:
                raise SubscriptionDeviceProblem("subscription-authorization-not-found", 404)
            if receipt is not None and receipt.connection_id != connection_id:
                raise SubscriptionDeviceProblem("subscription-authorization-not-found", 404)
            if receipt is None:
                cutoff = utcnow() - timedelta(minutes=1)
                starts = self._db.scalar(select(func.count()).select_from(
                    SubscriptionDeviceAuthorizationReceipt,
                ).where(
                    SubscriptionDeviceAuthorizationReceipt.user_id == self._owner_id,
                    SubscriptionDeviceAuthorizationReceipt.created_at >= cutoff,
                )) or 0
                if starts >= MAX_STARTS_PER_MINUTE:
                    raise SubscriptionDeviceProblem("subscription-device-rate-limit", 429)
                self._db.add(SubscriptionDeviceAuthorizationReceipt(
                    user_id=self._owner_id, authorization_id=attempt_id,
                    connection_id=connection_id, request_hash=_CANCEL_RECEIPT_HASH, created_at=utcnow(),
                ))
            if row is not None and row.state in {"starting", "pending", "polling", "exchanging"}:
                self._scrub(row, "cancelled", "subscription-device-cancelled")
            self._commit()

    def disconnect(self, connection_id: UUID, *, _operation_locked: bool = False) -> None:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner_id)
        with context:
            self._connection(connection_id)
            self._store.disconnect(connection_id, _operation_locked=True)
