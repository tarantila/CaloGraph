from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.google_health.constants import (
    GOOGLE_HEALTH_AUTH_URI,
    GOOGLE_HEALTH_SCOPE,
    GOOGLE_HEALTH_TOKEN_URI,
)
from app.google_health.errors import (
    GoogleHealthDisabledError,
    GoogleHealthOAuthError,
    GoogleHealthTokenExchangeError,
)
from app.google_health.oauth import (
    build_authorization_url,
    create_oauth_state,
    create_pkce_verifier,
    google_health_redirect_uri,
    hash_oauth_state,
    normalize_granted_scopes,
)
from app.auth.dependencies import _revalidate_locked_session
from app.models import GoogleHealthConnection, GoogleHealthOAuthFlow, User
from app.schemas_google_health import GoogleHealthStatus
from app.security_events import log_security_event, security_reference
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.rate_limit import check_rate_limit, normalize_client_ip
from app.services.user_operation_lock import exclusive_user_lifecycle_operation

FLOW_TTL = timedelta(minutes=10)


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
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": GOOGLE_HEALTH_AUTH_URI,
                    "token_uri": GOOGLE_HEALTH_TOKEN_URI,
                }
            },
            scopes=[GOOGLE_HEALTH_SCOPE],
            redirect_uri=redirect_uri,
        )
        token_response = flow.fetch_token(code=code, code_verifier=code_verifier)
        credentials = flow.credentials
        granted_scopes = getattr(credentials, "granted_scopes", None)
        if granted_scopes is None and isinstance(token_response, Mapping):
            granted_scopes = token_response.get("granted_scopes", token_response.get("scope"))
        if granted_scopes is None:
            granted_scopes = (GOOGLE_HEALTH_SCOPE,)
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
    if not settings.google_health_client_id or not settings.google_health_client_secret:
        raise GoogleHealthDisabledError("not_configured")


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


def _configured() -> bool:
    return bool(
        settings.google_health_enabled
        and settings.google_health_client_id
        and settings.google_health_client_secret
    )


def _status_from_connection(connection: GoogleHealthConnection | None) -> GoogleHealthStatus:
    if not settings.google_health_enabled:
        state = "disabled"
    elif connection is None:
        state = "not_connected"
    elif connection.last_error == "scope_missing":
        state = "scope_missing"
    else:
        state = connection.state
    return GoogleHealthStatus(
        available=bool(settings.google_health_enabled),
        configured=_configured(),
        state=state,
        granted_scopes=tuple(connection.granted_scopes or ()) if connection else (),
        refresh_token_expires_at=connection.refresh_token_expires_at if connection else None,
        last_attempt_at=connection.last_attempt_at if connection else None,
        last_success_at=connection.last_success_at if connection else None,
        last_error=connection.last_error if connection else None,
    )


def _status_for_error(code: str) -> GoogleHealthStatus:
    state = "scope_missing" if code == "scope_missing" else "reauth_required"
    return GoogleHealthStatus(
        available=bool(settings.google_health_enabled),
        configured=_configured(),
        state=state,
        last_error=code,
    )


def google_health_status(db: Session, user: User) -> GoogleHealthStatus:
    connection = db.scalar(
        select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user.id)
    )
    return _status_from_connection(connection)


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
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user.id)
            .with_for_update()
        )
        initial = connection is None
        reauthorize = bool(connection and connection.state == "reauth_required")
        missing_refresh = bool(connection and not connection.encrypted_refresh_token)
        scope_change = bool(
            connection and GOOGLE_HEALTH_SCOPE not in (connection.granted_scopes or [])
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
            client_id=settings.google_health_client_id,
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
            return _status_from_connection(connection) if connection else _status_for_error("reauth_required")

        adapter = oauth_adapter or _GoogleOAuthAdapter()
        redirect_uri = google_health_redirect_uri(settings.calograph_public_url)
        try:
            payload = adapter.exchange(
                code=code or "",
                redirect_uri=redirect_uri,
                client_id=settings.google_health_client_id,
                client_secret=settings.google_health_client_secret,
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
        if GOOGLE_HEALTH_SCOPE not in scopes:
            connection = _record_failure(db, user, timestamp, "scope_missing")
            db.commit()
            log_security_event(
                "integration.google_health.oauth_failed",
                actor_ref=security_reference("user", user.id),
                target_ref=security_reference("google_health", user.id),
                reason="scope_missing",
                actor_user_id=user.id,
            )
            return _status_from_connection(connection) if connection else _status_for_error("scope_missing")
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
            return _status_from_connection(connection) if connection else _status_for_error("reauth_required")
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
        refresh_expiry = _token_value(payload, "refresh_token_expires_at", "refresh_token_expiry")
        refresh_expires_in = _token_value(payload, "refresh_token_expires_in")
        if isinstance(refresh_expires_in, (int, float)) and not isinstance(refresh_expires_in, bool):
            connection.refresh_token_expires_at = timestamp + timedelta(seconds=refresh_expires_in)
        elif isinstance(refresh_expiry, datetime):
            connection.refresh_token_expires_at = refresh_expiry
        else:
            connection.refresh_token_expires_at = None
        db.commit()
    log_security_event(
        "integration.google_health.oauth_completed",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("google_health", user.id),
        reason="succeeded",
        actor_user_id=user.id,
    )
    return _status_from_connection(connection)
