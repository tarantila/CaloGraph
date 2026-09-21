from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, Protocol

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.dependencies import _revalidate_locked_session
from app.config import settings
from app.google_health.constants import (
    GOOGLE_HEALTH_AUTH_URI,
    GOOGLE_HEALTH_REQUIRED_SCOPES,
    GOOGLE_HEALTH_SCOPES,
    GOOGLE_HEALTH_TOKEN_URI,
)
from app.google_health.errors import (
    GoogleHealthDisabledError,
    GoogleHealthOAuthError,
    GoogleHealthTokenExchangeError,
)
from app.google_health.credentials import (
    GoogleHealthCredentialError,
    GoogleHealthCredentialUnavailableError,
    credential_pair_from_input,
    resolve_google_health_credentials,
)
from app.google_health.oauth import (
    build_authorization_url,
    create_oauth_state,
    create_pkce_verifier,
    google_health_redirect_uri,
    hash_oauth_state,
    normalize_granted_scopes,
)
from app.models import GoogleHealthConnection, GoogleHealthOAuthFlow, User
from app.schemas_google_health import GoogleHealthCredentialsInput, GoogleHealthStatus
from app.security_events import log_security_event, security_reference
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.rate_limit import check_rate_limit, normalize_client_ip
from app.services.user_operation_lock import exclusive_user_lifecycle_operation

FLOW_TTL = timedelta(minutes=10)
MAX_REFRESH_TOKEN_EXPIRES_IN = 10 * 365 * 24 * 60 * 60


class OAuthAdapter(Protocol):
    def exchange(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> Mapping[str, Any]: ...


class _GoogleOAuthAdapter:
    def exchange(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> Mapping[str, Any]:
        from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": GOOGLE_HEALTH_AUTH_URI,
                    "token_uri": GOOGLE_HEALTH_TOKEN_URI,
                }
            },
            scopes=list(GOOGLE_HEALTH_SCOPES),
            redirect_uri=redirect_uri,
        )
        token_response = flow.fetch_token(code=code, code_verifier=code_verifier)
        credentials = flow.credentials
        granted_scopes = getattr(credentials, "granted_scopes", None)
        if granted_scopes is None and isinstance(token_response, Mapping):
            granted_scopes = token_response.get("granted_scopes", token_response.get("scope"))
        result: dict[str, Any] = {
            "refresh_token": credentials.refresh_token,
            "access_token": credentials.token,
            "granted_scopes": granted_scopes,
        }
        if credentials.expiry is not None:
            result["access_token_expires_at"] = credentials.expiry
        if isinstance(token_response, Mapping):
            refresh_expires_in = token_response.get("refresh_token_expires_in")
            if refresh_expires_in is not None:
                result["refresh_token_expires_in"] = refresh_expires_in
        return result


def _now(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    return result if result.tzinfo is not None else result.replace(tzinfo=UTC)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _require_enabled() -> None:
    if not settings.google_health_enabled:
        raise GoogleHealthDisabledError("disabled")

def _rate_limit_start(db: Session, user: User, client_ip: str | None) -> None:
    if client_ip is not None:
        normalized = normalize_client_ip(client_ip)
        check_rate_limit(
            db,
            "google-health-oauth-ip",
            f"ip:{normalized}",
            settings.reconcile_ip_rate_limit,
            settings.reconcile_rate_limit_window_seconds,
        )
    check_rate_limit(
        db,
        "google-health-oauth-user",
        f"user:{user.id}",
        settings.reconcile_rate_limit,
        settings.reconcile_rate_limit_window_seconds,
    )

def _configured(connection: GoogleHealthConnection | None) -> bool:
    return bool(connection and connection.client_id and connection.encrypted_client_secret)


_SAFE_ERROR_CATEGORIES = frozenset(
    {
        "credential_unavailable",
        "credentials_unavailable",
        "invalid_response",
        "provider_error",
        "rate_limited",
        "reauth_required",
        "scope_missing",
        "transient_error",
    }
)


def _safe_error_category(value: str | None) -> str | None:
    return value if value in _SAFE_ERROR_CATEGORIES else None


def _status_from_connection(connection: GoogleHealthConnection | None) -> GoogleHealthStatus:
    client_id_configured = bool(connection and connection.client_id)
    client_secret_configured = bool(connection and connection.encrypted_client_secret)
    configured = client_id_configured and client_secret_configured
    if not settings.google_health_enabled:
        state = "disabled"
    elif not configured:
        state = "not_configured"
    elif connection is None or not connection.encrypted_refresh_token:
        state = "not_connected"
    elif connection.last_error == "scope_missing" or (
        connection.state != "reauth_required"
        and not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ()))
    ):
        state = "scope_missing"
    elif connection.state in {"active", "reauth_required", "not_connected"}:
        state = connection.state
    else:
        state = "reauth_required"
    error_category = _safe_error_category(connection.last_error if connection else None)
    return GoogleHealthStatus(
        available=bool(settings.google_health_enabled),
        configured=configured,
        client_id_configured=client_id_configured,
        client_secret_configured=client_secret_configured,
        redirect_uri=google_health_redirect_uri(settings.calograph_public_url),
        state=state,
        sync_state=connection.sync_state if connection else "idle",
        retry_attempt=connection.retry_attempt if connection else 0,
        retry_max_attempts=connection.retry_max_attempts if connection else 0,
        next_retry_at=connection.next_retry_at if connection else None,
        granted_scopes=tuple(connection.granted_scopes or ()) if connection else (),
        refresh_token_expires_at=connection.refresh_token_expires_at if connection else None,
        last_attempt_at=connection.last_attempt_at if connection else None,
        last_success_at=connection.last_success_at if connection else None,
        last_error=error_category,
        last_error_category=(
            _safe_error_category(connection.last_error_category)
            if connection
            else error_category
        ),
    )


def _status_for_error(code: str) -> GoogleHealthStatus:
    state = "scope_missing" if code == "scope_missing" else "reauth_required"
    safe_code = _safe_error_category(code)
    return GoogleHealthStatus(
        available=bool(settings.google_health_enabled),
        configured=False,
        redirect_uri=google_health_redirect_uri(settings.calograph_public_url),
        state=state,
        last_error=safe_code,
        last_error_category=safe_code,
    )


def google_health_status(db: Session, user: User) -> GoogleHealthStatus:
    connection = db.scalar(
        select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user.id)
    )
    return _status_from_connection(connection)


def save_google_health_credentials(
    db: Session,
    user: User,
    payload: GoogleHealthCredentialsInput,
    *,
    lock: bool = True,
) -> GoogleHealthStatus:
    pair = credential_pair_from_input(payload)
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        if pair is None:
            if connection is None or not _configured(connection):
                raise GoogleHealthCredentialError("credential_pair_required")
            db.commit()
            result = _status_from_connection(connection)
            reason = "preserved"
        else:
            client_id, client_secret = pair
            try:
                encrypted_secret = encrypt_credential(client_secret)
            except CredentialEncryptionError:
                raise GoogleHealthCredentialError("credential_unavailable") from None
            if connection is None:
                connection = GoogleHealthConnection(
                    user_id=user.id,
                    client_id=client_id,
                    encrypted_client_secret=encrypted_secret,
                    encrypted_refresh_token=None,
                    granted_scopes=[],
                    state="not_connected",
                )
                db.add(connection)
                reason = "configured"
            else:
                connection.client_id = client_id
                connection.encrypted_client_secret = encrypted_secret
                # A client replacement invalidates consent for the old client,
                # but deliberately retains ciphertext until explicit deletion.
                connection.granted_scopes = []
                connection.refresh_token_expires_at = None
                connection.last_error = None
                connection.state = (
                    "reauth_required" if connection.encrypted_refresh_token else "not_connected"
                )
                reason = "replaced"
            db.commit()
            result = _status_from_connection(connection)
    log_security_event(
        "integration.google_health.credentials_updated",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("google_health", user.id),
        reason=reason,
        actor_user_id=user.id,
    )
    return result


def delete_google_health_credentials(
    db: Session, user: User, *, lock: bool = True
) -> GoogleHealthStatus:
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is not None:
            connection.client_id = None
            connection.encrypted_client_secret = None
            connection.encrypted_refresh_token = None
            connection.granted_scopes = []
            connection.refresh_token_expires_at = None
            connection.state = "not_connected"
            connection.last_error = None
            db.commit()
        result = _status_from_connection(connection)
    log_security_event(
        "integration.google_health.credentials_deleted",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("google_health", user.id),
        reason="deleted",
        actor_user_id=user.id,
    )
    return result


def disconnect_google_health(
    db: Session, user: User, *, lock: bool = True
) -> GoogleHealthStatus:
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is not None:
            connection.encrypted_refresh_token = None
            connection.granted_scopes = []
            connection.refresh_token_expires_at = None
            connection.state = "not_connected"
            connection.last_error = None
            db.commit()
        result = _status_from_connection(connection)
    log_security_event(
        "integration.google_health.connection_disconnected",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("google_health", user.id),
        reason="disconnected",
        actor_user_id=user.id,
    )
    return result



def start_google_health_oauth(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
    oauth_adapter: OAuthAdapter | None = None,
    client_ip: str | None = None,
    lock: bool = True,
) -> str:
    del oauth_adapter
    _require_enabled()
    timestamp = _now(now)
    _rate_limit_start(db, user, client_ip)
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        db.execute(
            delete(GoogleHealthOAuthFlow)
            .where(
                GoogleHealthOAuthFlow.user_id == user.id,
                GoogleHealthOAuthFlow.expires_at <= timestamp,
            )
            .execution_options(synchronize_session=False)
        )
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        try:
            client_id, _client_secret = resolve_google_health_credentials(connection)
        except GoogleHealthCredentialUnavailableError:
            raise GoogleHealthOAuthError("credential_unavailable") from None
        initial = connection is None
        reauthorize = bool(connection and connection.state == "reauth_required")
        missing_refresh = bool(connection and not connection.encrypted_refresh_token)
        scope_change = bool(
            connection
            and not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(
                set(connection.granted_scopes or ())
            )
        )
        state = create_oauth_state()
        verifier = create_pkce_verifier()
        db.add(
            GoogleHealthOAuthFlow(
                user_id=user.id,
                state_hash=hash_oauth_state(state),
                encrypted_pkce_verifier=encrypt_credential(verifier),
                expires_at=timestamp + FLOW_TTL,
            )
        )
        url = build_authorization_url(
            client_id=client_id,
            state=state,
            verifier=verifier,
            initial=initial,
            reauthorize=reauthorize,
            missing_refresh=missing_refresh,
            scope_change=scope_change,
        )
        db.commit()
    log_security_event(
        "integration.google_health.oauth_started",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("google_health", user.id),
        reason=(
            "reauthorize"
            if (reauthorize or missing_refresh or scope_change)
            else "initial"
            if initial
            else "reconnect"
        ),
        actor_user_id=user.id,
    )
    return url


def _token_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _refresh_token_expiry(payload: Mapping[str, Any], timestamp: datetime) -> datetime | None:
    value = _token_value(payload, "refresh_token_expires_in")
    if value is None:
        explicit = _token_value(payload, "refresh_token_expires_at", "refresh_token_expiry")
        return explicit if isinstance(explicit, datetime) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid refresh token expiry")
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        raise ValueError("invalid refresh token expiry") from None
    if not isfinite(seconds) or seconds < 0 or seconds > MAX_REFRESH_TOKEN_EXPIRES_IN:
        raise ValueError("invalid refresh token expiry")
    try:
        return timestamp + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        raise ValueError("invalid refresh token expiry") from None


def _error_code(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    text = str(exc).casefold()
    if "scope" in text or "forbidden" in text or status == 403:
        return "scope_missing"
    if (
        "invalid_grant" in text
        or "expired_grant" in text
        or "expired token" in text
        or "token has expired" in text
    ):
        return "reauth_required"
    if status == 429 or "rate" in text:
        return "rate_limited"
    if isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in text or "network" in text:
        return "transient_error"
    if isinstance(status, int) and status >= 500:
        return "provider_error"
    return "invalid_response"


def _record_failure(
    db: Session,
    user: User,
    timestamp: datetime,
    code: str,
) -> GoogleHealthConnection | None:
    connection = db.scalar(
        select(GoogleHealthConnection)
        .where(GoogleHealthConnection.user_id == user.id)
        .with_for_update()
    )
    if connection is not None:
        connection.last_attempt_at = timestamp
        connection.last_error = code
        if code in {"reauth_required", "scope_missing"}:
            connection.state = "reauth_required"
    return connection


def complete_google_health_oauth(
    db: Session,
    user: User,
    *,
    state: str | None,
    code: str | None,
    error: str | None,
    now: datetime | None = None,
    oauth_adapter: OAuthAdapter | None = None,
    request: Request | None = None,
) -> GoogleHealthStatus:
    _require_enabled()
    timestamp = _now(now)
    if not state:
        raise GoogleHealthOAuthError("invalid_state")
    if not code and not error:
        raise GoogleHealthOAuthError("invalid_response")
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
                raise GoogleHealthOAuthError("session_inactive", status_code=401)
        flow = db.scalar(
            select(GoogleHealthOAuthFlow)
            .where(
                GoogleHealthOAuthFlow.user_id == user.id,
                GoogleHealthOAuthFlow.state_hash == hash_oauth_state(state),
            )
            .with_for_update()
        )
        if flow is None:
            raise GoogleHealthOAuthError("unknown_state")
        if _utc(flow.expires_at) <= timestamp:
            raise GoogleHealthOAuthError("expired_state")
        if flow.consumed_at is not None:
            raise GoogleHealthOAuthError("replayed_state")
        flow.consumed_at = timestamp
        try:
            verifier = decrypt_credential(flow.encrypted_pkce_verifier)
        except CredentialEncryptionError as exc:
            db.commit()
            raise GoogleHealthOAuthError("invalid_response", status_code=500) from exc

        if error:
            connection = _record_failure(db, user, timestamp, "reauth_required")
            db.commit()
            log_security_event(
                "integration.google_health.oauth_failed",
                actor_ref=security_reference("user", user.id),
                target_ref=security_reference("google_health", user.id),
                reason="reauth_required",
                actor_user_id=user.id,
            )
            return (
                _status_from_connection(connection)
                if connection
                else _status_for_error("reauth_required")
            )

        adapter = oauth_adapter or _GoogleOAuthAdapter()
        redirect_uri = google_health_redirect_uri(settings.calograph_public_url)
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        try:
            client_id, client_secret = resolve_google_health_credentials(connection)
        except GoogleHealthCredentialUnavailableError:
            _record_failure(db, user, timestamp, "credential_unavailable")
            db.commit()
            raise GoogleHealthOAuthError("credential_unavailable") from None
        try:
            payload = adapter.exchange(
                code=code or "",
                redirect_uri=redirect_uri,
                client_id=client_id,
                client_secret=client_secret,
                code_verifier=verifier,
            )
        except Exception as exc:
            failure_code = _error_code(exc)
            connection = _record_failure(db, user, timestamp, failure_code)
            db.commit()
            log_security_event(
                "integration.google_health.oauth_failed",
                actor_ref=security_reference("user", user.id),
                target_ref=security_reference("google_health", user.id),
                reason=failure_code,
                actor_user_id=user.id,
            )
            raise GoogleHealthTokenExchangeError(failure_code) from None

        if not isinstance(payload, Mapping):
            _record_failure(db, user, timestamp, "invalid_response")
            db.commit()
            raise GoogleHealthTokenExchangeError("invalid_response")
        scopes = normalize_granted_scopes(
            _token_value(payload, "granted_scopes", "scopes", "scope")
        )
        if not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(scopes)):
            connection = _record_failure(db, user, timestamp, "scope_missing")
            db.commit()
            log_security_event(
                "integration.google_health.oauth_failed",
                actor_ref=security_reference("user", user.id),
                target_ref=security_reference("google_health", user.id),
                reason="scope_missing",
                actor_user_id=user.id,
            )
            return (
                _status_from_connection(connection)
                if connection
                else _status_for_error("scope_missing")
            )
        try:
            refresh_expires_at = _refresh_token_expiry(payload, timestamp)
        except ValueError:
            _record_failure(db, user, timestamp, "invalid_response")
            db.commit()
            raise GoogleHealthTokenExchangeError("invalid_response") from None
        refresh_token = _token_value(payload, "refresh_token", "refreshToken")
        if not isinstance(refresh_token, str) or not refresh_token:
            connection = _record_failure(db, user, timestamp, "reauth_required")
            db.commit()
            log_security_event(
                "integration.google_health.oauth_failed",
                actor_ref=security_reference("user", user.id),
                target_ref=security_reference("google_health", user.id),
                reason="reauth_required",
                actor_user_id=user.id,
            )
            return (
                _status_from_connection(connection)
                if connection
                else _status_for_error("reauth_required")
            )
        try:
            encrypted_refresh_token = encrypt_credential(refresh_token)
        except CredentialEncryptionError as exc:
            connection = _record_failure(db, user, timestamp, "credential_unavailable")
            db.commit()
            raise GoogleHealthOAuthError("credential_unavailable", status_code=500) from exc
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is None:
            connection = GoogleHealthConnection(user_id=user.id)
            db.add(connection)
        connection.encrypted_refresh_token = encrypted_refresh_token
        connection.granted_scopes = list(scopes)
        connection.state = "active"
        connection.last_attempt_at = timestamp
        connection.last_success_at = timestamp
        connection.last_error = None
        connection.refresh_token_expires_at = refresh_expires_at
        db.commit()
    log_security_event(
        "integration.google_health.oauth_completed",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("google_health", user.id),
        reason="succeeded",
        actor_user_id=user.id,
    )
    return _status_from_connection(connection)
