from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.dependencies import _revalidate_locked_session
from app.config import settings
from app.models import User, WithingsConnection, WithingsOAuthFlow
from app.schemas_withings import (
    WithingsConnectionTestResponse,
    WithingsCredentialsInput,
    WithingsStatus,
)
from app.security_events import log_security_event, security_reference
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
    encrypt_token,
    expiry_from_payload,
    normalize_scopes,
    resolve_withings_credentials,
    token_value,
)
from app.withings.errors import (
    WithingsDisabledError,
    WithingsNotConfiguredError,
    WithingsOAuthError,
    WithingsTokenExchangeError,
)
from app.withings.oauth import (
    build_authorization_url,
    create_oauth_state,
    hash_oauth_state,
)
from app.withings.token_service import OAuthAdapter, _WithingsOAuthAdapter

FLOW_TTL = timedelta(minutes=10)


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


def _log_withings_event(event: str, user: User, reason: str) -> None:
    log_security_event(
        event,
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("withings", user.id),
        reason=reason,
        actor_user_id=user.id,
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
        reason = "replaced" if _has_credentials(connection) else "configured"
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
        result = _status_from_connection(connection)
    _log_withings_event("integration.withings.credentials_updated", user, reason)
    return result


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
        result = _status_from_connection(connection)
    _log_withings_event("integration.withings.credentials_deleted", user, "deleted")
    return result


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


def _record_withings_failure(
    db: Session,
    user: User,
    timestamp: datetime,
    code: str,
) -> WithingsStatus:
    safe_code = code if code in {
        "reauth_required",
        "scope_missing",
        "rate_limited",
        "transient_error",
        "provider_error",
        "invalid_response",
    } else "invalid_response"
    connection = db.scalar(
        select(WithingsConnection)
        .where(WithingsConnection.user_id == user.id)
        .with_for_update()
    )
    if connection is not None:
        connection.last_attempt_at = timestamp
        connection.last_error_category = safe_code
        connection.last_error = safe_code
        if safe_code in {"reauth_required", "scope_missing"}:
            connection.state = WITHINGS_STATUS_REAUTH_REQUIRED
    db.commit()
    _log_withings_event("integration.withings.oauth_failed", user, safe_code)
    return _status_from_connection(connection)


def _persist_rotated_withings_tokens_locked(
    db: Session,
    user: User,
    payload: Mapping[str, Any],
    *,
    now: datetime,
) -> WithingsStatus:
    connection = db.scalar(
        select(WithingsConnection)
        .where(WithingsConnection.user_id == user.id)
        .with_for_update()
    )
    if connection is None:
        raise WithingsOAuthError("not_connected", status_code=409)

    current_scopes = normalize_scopes(connection.granted_scopes or ())
    provider_scopes = token_value(payload, "granted_scopes", "scopes", "scope")
    next_scopes = (
        current_scopes
        if provider_scopes is None
        and WITHINGS_REQUIRED_SCOPES.issubset(set(current_scopes))
        else normalize_scopes(provider_scopes)
    )
    if not WITHINGS_REQUIRED_SCOPES.issubset(set(next_scopes)):
        return _record_withings_failure(db, user, now, "scope_missing")

    access_token = token_value(payload, "access_token", "accessToken")
    refresh_token = token_value(payload, "refresh_token", "refreshToken")
    if not isinstance(access_token, str) or not access_token:
        return _record_withings_failure(db, user, now, "invalid_response")
    if refresh_token is not None and (
        not isinstance(refresh_token, str) or not refresh_token
    ):
        return _record_withings_failure(db, user, now, "invalid_response")
    if refresh_token is None and not connection.encrypted_refresh_token:
        return _record_withings_failure(db, user, now, "reauth_required")

    try:
        expires_at = expiry_from_payload(payload, now)
        encrypted_access_token = encrypt_token(access_token)
        encrypted_refresh_token = (
            encrypt_token(refresh_token)
            if refresh_token is not None
            else connection.encrypted_refresh_token
        )
    except (CredentialEncryptionError, ValueError, OverflowError):
        return _record_withings_failure(db, user, now, "invalid_response")

    connection.encrypted_access_token = encrypted_access_token
    connection.encrypted_refresh_token = encrypted_refresh_token
    connection.granted_scopes = list(next_scopes)
    connection.access_token_expires_at = expires_at
    connection.state = WITHINGS_STATUS_ACTIVE
    connection.last_attempt_at = now
    connection.last_success_at = now
    connection.last_error_category = None
    connection.last_error = None
    db.commit()
    return _status_from_connection(connection)


def persist_rotated_withings_tokens(
    db: Session,
    user: User,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> WithingsStatus:
    timestamp = _now(now)
    with exclusive_user_lifecycle_operation(db, user.id):
        return _persist_rotated_withings_tokens_locked(
            db,
            user,
            payload,
            now=timestamp,
        )


def refresh_withings_tokens_default(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
    lock: bool = True,
) -> WithingsConnection:
    from app.withings.token_service import refresh_withings_tokens

    return refresh_withings_tokens(db, user, now=now, lock=lock)


def start_withings_oauth(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
    lock: bool = True,
) -> str:
    _require_enabled()
    timestamp = _now(now)
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        db.execute(
            delete(WithingsOAuthFlow)
            .where(
                WithingsOAuthFlow.user_id == user.id,
                WithingsOAuthFlow.expires_at <= timestamp,
            )
            .execution_options(synchronize_session=False)
        )
        connection = db.scalar(
            select(WithingsConnection)
            .where(WithingsConnection.user_id == user.id)
            .with_for_update()
        )
        try:
            client_id, _client_secret = resolve_withings_credentials(connection)
        except WithingsCredentialUnavailableError:
            raise WithingsOAuthError("credential_unavailable") from None

        state = create_oauth_state()
        db.add(
            WithingsOAuthFlow(
                user_id=user.id,
                connection_id=connection.id,
                state_hash=hash_oauth_state(state),
                expires_at=timestamp + FLOW_TTL,
            )
        )
        url = build_authorization_url(
            client_id=client_id,
            redirect_uri=settings.withings_redirect_uri or "",
            state=state,
        )
        db.commit()
    _log_withings_event("integration.withings.oauth_started", user, "started")
    return url


def complete_withings_oauth(
    db: Session,
    user: User,
    *,
    state: str | None,
    code: str | None,
    error: str | None,
    now: datetime | None = None,
    oauth_adapter: OAuthAdapter | None = None,
    request: Request | None = None,
) -> WithingsStatus:
    _require_enabled()
    timestamp = _now(now)
    if not state:
        raise WithingsOAuthError("invalid_state")
    if not code and not error:
        raise WithingsOAuthError("invalid_response")

    with exclusive_user_lifecycle_operation(db, user.id):
        if request is not None:
            _revalidate_locked_session(request, user.id, db)
        else:
            active_user = db.scalar(
                select(User)
                .where(User.id == user.id, User.is_active.is_(True))
                .execution_options(populate_existing=True)
            )
            if active_user is None:
                raise WithingsOAuthError("session_inactive", status_code=401)

        flow = db.scalar(
            select(WithingsOAuthFlow)
            .where(
                WithingsOAuthFlow.user_id == user.id,
                WithingsOAuthFlow.state_hash == hash_oauth_state(state),
            )
            .with_for_update()
        )
        if flow is None:
            raise WithingsOAuthError("unknown_state")
        if _utc(flow.expires_at) <= timestamp:
            raise WithingsOAuthError("expired_state")
        if flow.consumed_at is not None:
            raise WithingsOAuthError("replayed_state")

        connection = db.scalar(
            select(WithingsConnection)
            .where(WithingsConnection.user_id == user.id)
            .with_for_update()
        )
        if (
            connection is None
            or flow.connection_id is None
            or flow.connection_id != connection.id
        ):
            flow.consumed_at = timestamp
            db.commit()
            raise WithingsOAuthError("connection_changed")
        flow.consumed_at = timestamp

        if error:
            return _record_withings_failure(db, user, timestamp, "reauth_required")
        try:
            client_id, client_secret = resolve_withings_credentials(connection)
        except WithingsCredentialUnavailableError:
            db.commit()
            raise WithingsOAuthError("credential_unavailable") from None

        adapter = oauth_adapter or _WithingsOAuthAdapter()
        try:
            payload = adapter.exchange(
                code=code or "",
                redirect_uri=settings.withings_redirect_uri or "",
                client_id=client_id,
                client_secret=client_secret,
            )
        except Exception as exc:
            code = _safe_error_code(exc)
            _record_withings_failure(db, user, timestamp, code)
            raise WithingsTokenExchangeError(code) from None

        if not isinstance(payload, Mapping):
            _record_withings_failure(db, user, timestamp, "invalid_response")
            raise WithingsTokenExchangeError("invalid_response")

        scopes = normalize_scopes(token_value(payload, "granted_scopes", "scopes", "scope"))
        if not WITHINGS_REQUIRED_SCOPES.issubset(set(scopes)):
            return _record_withings_failure(db, user, timestamp, "scope_missing")

        access_token = token_value(payload, "access_token", "accessToken")
        refresh_token = token_value(payload, "refresh_token", "refreshToken")
        provider_user_id = token_value(payload, "userid", "user_id", "withings_user_id")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(refresh_token, str)
            or not refresh_token
            or isinstance(provider_user_id, bool)
            or not isinstance(provider_user_id, (str, int))
        ):
            _record_withings_failure(db, user, timestamp, "reauth_required")
            return _status_from_connection(connection)
        provider_user_id = str(provider_user_id)
        if not provider_user_id or len(provider_user_id) > 64:
            _record_withings_failure(db, user, timestamp, "invalid_response")
            return _status_from_connection(connection)

        try:
            expires_at = expiry_from_payload(payload, timestamp)
            encrypted_access_token = encrypt_token(access_token)
            encrypted_refresh_token = encrypt_token(refresh_token)
        except (CredentialEncryptionError, ValueError, OverflowError):
            _record_withings_failure(db, user, timestamp, "invalid_response")
            return _status_from_connection(connection)

        connection.withings_user_id = provider_user_id
        connection.encrypted_access_token = encrypted_access_token
        connection.encrypted_refresh_token = encrypted_refresh_token
        connection.granted_scopes = list(scopes)
        connection.access_token_expires_at = expires_at
        connection.state = WITHINGS_STATUS_ACTIVE
        connection.last_attempt_at = timestamp
        connection.last_success_at = timestamp
        connection.last_error_category = None
        connection.last_error = None
        db.commit()
        result = _status_from_connection(connection)

    _log_withings_event("integration.withings.oauth_completed", user, "connected")
    return result


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
        result = _status_from_connection(connection)
    _log_withings_event(
        "integration.withings.connection_disconnected",
        user,
        "disconnected",
    )
    return result
