from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, WithingsConnection, WithingsOAuthFlow
from app.schemas_withings import (
    WithingsConnectionTestResponse,
    WithingsCredentialsInput,
    WithingsStatus,
)
from app.services.credential_crypto import CredentialEncryptionError, encrypt_credential
from app.services.user_operation_lock import exclusive_user_lifecycle_operation
from app.withings.constants import (
    WITHINGS_REQUIRED_SCOPES,
    WITHINGS_STATUS_ACTIVE,
    WITHINGS_STATUS_DISABLED,
    WITHINGS_STATUS_ERROR,
    WITHINGS_STATUS_NOT_CONFIGURED,
    WITHINGS_STATUS_NOT_CONNECTED,
    WITHINGS_STATUS_REAUTH_REQUIRED,
)
from app.withings.credentials import (
    WithingsCredentialError,
    WithingsCredentialUnavailableError,
    credential_pair_from_input,
    decrypt_token,
    resolve_withings_credentials,
)
from app.withings.errors import WithingsDisabledError, WithingsNotConfiguredError, WithingsOAuthError


class ConnectionAdapter(Protocol):
    def test_connection(self, *, access_token: str, withings_user_id: str) -> Mapping[str, Any]: ...


class _WithingsConnectionAdapter:
    def test_connection(self, *, access_token: str, withings_user_id: str) -> Mapping[str, Any]:
        del withings_user_id
        from app.withings.client import WithingsHTTPTransport
        from app.withings.parsers import parse_measure_response

        enddate = int(datetime.now(UTC).timestamp())
        transport = WithingsHTTPTransport()
        try:
            response = transport.get_measure(
                access_token=access_token,
                startdate=enddate - 86_400,
                enddate=enddate,
            )
            parse_measure_response(response)
        finally:
            transport.close()
        return {"ok": True}


def _now(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    return result if result.tzinfo is not None else result.replace(tzinfo=UTC)


def _require_enabled() -> None:
    if not settings.withings_enabled:
        raise WithingsDisabledError("disabled")


def _has_credentials(connection: WithingsConnection | None) -> bool:
    return bool(connection and connection.client_id and connection.encrypted_client_secret)


def _configured(connection: WithingsConnection | None) -> bool:
    return bool(settings.withings_enabled and _has_credentials(connection))


def _status_from_connection(connection: WithingsConnection | None) -> WithingsStatus:
    credentials_configured = _has_credentials(connection)
    if not settings.withings_enabled:
        state = WITHINGS_STATUS_DISABLED
    elif not credentials_configured:
        state = WITHINGS_STATUS_NOT_CONFIGURED
    elif connection is not None and connection.last_error_category in {
        "reauth_required",
        "scope_missing",
    }:
        state = WITHINGS_STATUS_REAUTH_REQUIRED
    elif connection is None or not connection.encrypted_refresh_token:
        state = WITHINGS_STATUS_NOT_CONNECTED
    elif connection.last_error_category in {
        "provider_error",
        "transient_error",
        "rate_limited",
        "invalid_response",
    }:
        state = WITHINGS_STATUS_ERROR
    elif connection.state == WITHINGS_STATUS_ACTIVE and not WITHINGS_REQUIRED_SCOPES.issubset(
        set(connection.granted_scopes or ())
    ):
        state = WITHINGS_STATUS_REAUTH_REQUIRED
    else:
        state = connection.state
    return WithingsStatus(
        available=bool(settings.withings_enabled),
        configured=_configured(connection),
        credentials_configured=credentials_configured,
        redirect_uri=settings.withings_redirect_uri or "",
        connected=state
        in {
            WITHINGS_STATUS_ACTIVE,
            WITHINGS_STATUS_REAUTH_REQUIRED,
            WITHINGS_STATUS_ERROR,
        },
        state=state,
        granted_scopes=tuple(connection.granted_scopes or ()) if connection else (),
        access_token_expires_at=connection.access_token_expires_at if connection else None,
        last_attempt_at=connection.last_attempt_at if connection else None,
        last_success_at=connection.last_success_at if connection else None,
        last_error_category=connection.last_error_category if connection else None,
    )


def _invalidate_oauth_flows(db: Session, user: User) -> None:
    db.execute(
        delete(WithingsOAuthFlow)
        .where(
            WithingsOAuthFlow.user_id == user.id,
            WithingsOAuthFlow.consumed_at.is_(None),
        )
        .execution_options(synchronize_session=False)
    )


def withings_status(db: Session, user: User) -> WithingsStatus:
    connection = db.scalar(select(WithingsConnection).where(WithingsConnection.user_id == user.id))
    return _status_from_connection(connection)


def save_withings_credentials(
    db: Session,
    user: User,
    payload: WithingsCredentialsInput,
    *,
    lock: bool = True,
) -> WithingsStatus:
    client_id, client_secret = credential_pair_from_input(payload)
    try:
        encrypted_secret = encrypt_credential(client_secret)
    except CredentialEncryptionError:
        raise WithingsCredentialError("credential_unavailable") from None

    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(WithingsConnection)
            .where(WithingsConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is None:
            connection = WithingsConnection(user_id=user.id)
            db.add(connection)
        connection.client_id = client_id
        connection.encrypted_client_secret = encrypted_secret
        connection.withings_user_id = None
        connection.encrypted_access_token = None
        connection.encrypted_refresh_token = None
        connection.granted_scopes = []
        connection.access_token_expires_at = None
        connection.state = WITHINGS_STATUS_NOT_CONNECTED
        connection.last_error_category = None
        connection.last_error = None
        _invalidate_oauth_flows(db, user)
        db.commit()
        return _status_from_connection(connection)


def delete_withings_credentials(
    db: Session,
    user: User,
    *,
    lock: bool = True,
) -> WithingsStatus:
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(WithingsConnection)
            .where(WithingsConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is not None:
            connection.client_id = None
            connection.encrypted_client_secret = None
            connection.withings_user_id = None
            connection.encrypted_access_token = None
            connection.encrypted_refresh_token = None
            connection.granted_scopes = []
            connection.access_token_expires_at = None
            connection.state = WITHINGS_STATUS_NOT_CONNECTED
            connection.last_error_category = None
            connection.last_error = None
        _invalidate_oauth_flows(db, user)
        db.commit()
        return _status_from_connection(connection)


def _safe_error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in {
        "reauth_required",
        "scope_missing",
        "rate_limited",
        "transient_error",
        "provider_error",
        "invalid_response",
    }:
        return code
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "upstream_status_code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    text = str(exc).casefold()
    if "scope" in text or status == 403:
        return "scope_missing"
    if "invalid_grant" in text or "invalid token" in text or "expired" in text:
        return "reauth_required"
    if status == 429 or "rate" in text:
        return "rate_limited"
    if isinstance(exc, (TimeoutError, ConnectionError)) or "network" in text or "timeout" in text:
        return "transient_error"
    if isinstance(status, int) and status >= 500:
        return "provider_error"
    return "invalid_response"


def test_withings_connection(
    db: Session,
    user: User,
    *,
    adapter: ConnectionAdapter | None = None,
    now: datetime | None = None,
) -> WithingsConnectionTestResponse:
    _require_enabled()
    timestamp = _now(now)
    connection = db.scalar(
        select(WithingsConnection)
        .where(WithingsConnection.user_id == user.id)
        .with_for_update()
    )
    try:
        resolve_withings_credentials(connection)
    except WithingsCredentialUnavailableError:
        raise WithingsNotConfiguredError("not_configured") from None
    if (
        connection is None
        or not connection.encrypted_access_token
        or not connection.encrypted_refresh_token
        or not connection.withings_user_id
    ):
        raise WithingsOAuthError("not_connected", status_code=409)
    try:
        access_token = decrypt_token(connection.encrypted_access_token)
        result = (
            adapter.test_connection(
                access_token=access_token, withings_user_id=connection.withings_user_id
            )
            if adapter
            else _WithingsConnectionAdapter().test_connection(
                access_token=access_token, withings_user_id=connection.withings_user_id
            )
        )
        ok = bool(result.get("ok", True)) if isinstance(result, Mapping) else True
    except Exception as exc:
        code = _safe_error_code(exc)
        connection.last_attempt_at = timestamp
        connection.last_error_category = code
        connection.last_error = code
        if code in {"reauth_required", "scope_missing"}:
            connection.state = WITHINGS_STATUS_REAUTH_REQUIRED
        db.commit()
        return WithingsConnectionTestResponse(
            ok=False, state=_status_from_connection(connection).state, error_category=code
        )
    connection.last_attempt_at = timestamp
    if ok:
        connection.last_success_at = timestamp
        connection.last_error_category = None
        connection.last_error = None
        connection.state = WITHINGS_STATUS_ACTIVE
    else:
        connection.last_error_category = "provider_error"
        connection.last_error = "provider_error"
    db.commit()
    return WithingsConnectionTestResponse(
        ok=ok,
        state=_status_from_connection(connection).state,
        error_category=None if ok else "provider_error",
    )


def disconnect_withings(
    db: Session,
    user: User,
    *,
    lock: bool = True,
) -> WithingsStatus:
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(WithingsConnection)
            .where(WithingsConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is not None:
            connection.withings_user_id = None
            connection.encrypted_access_token = None
            connection.encrypted_refresh_token = None
            connection.granted_scopes = []
            connection.access_token_expires_at = None
            connection.state = WITHINGS_STATUS_NOT_CONNECTED
            connection.last_error_category = None
            connection.last_error = None
        _invalidate_oauth_flows(db, user)
        db.commit()
        return _status_from_connection(connection)
