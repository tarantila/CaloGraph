from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, Protocol

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, WithingsConnection
from app.services.user_operation_lock import exclusive_user_lifecycle_operation
from app.withings.constants import WITHINGS_TOKEN_URL
from app.withings.credentials import (
    WithingsCredentialUnavailableError,
    decrypt_token,
    resolve_withings_credentials,
)
from app.withings.errors import (
    WithingsDisabledError,
    WithingsOAuthError,
    WithingsTokenExchangeError,
)


class OAuthAdapter(Protocol):
    def exchange(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> Mapping[str, Any]: ...

    def refresh(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> Mapping[str, Any]: ...


class _WithingsOAuthAdapter:
    def exchange(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> Mapping[str, Any]:
        return self._request_token(
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )

    def refresh(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> Mapping[str, Any]:
        return self._request_token(
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )

    @staticmethod
    def _request_token(payload: Mapping[str, str]) -> Mapping[str, Any]:
        response = requests.post(WITHINGS_TOKEN_URL, data=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, Mapping) or result.get("status") != 0:
            raise WithingsTokenExchangeError("provider_error")
        body = result.get("body")
        if not isinstance(body, Mapping):
            raise WithingsTokenExchangeError("invalid_response")
        return body


def _now(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    return result if result.tzinfo is not None else result.replace(tzinfo=UTC)


def refresh_withings_tokens(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
    lock: bool = True,
    oauth_adapter: OAuthAdapter | None = None,
) -> WithingsConnection:
    if not settings.withings_enabled:
        raise WithingsDisabledError("disabled")
    timestamp = _now(now)
    operation = exclusive_user_lifecycle_operation(db, user.id) if lock else nullcontext()
    with operation:
        connection = db.scalar(
            select(WithingsConnection)
            .where(WithingsConnection.user_id == user.id)
            .with_for_update()
        )
        if connection is None or not connection.encrypted_refresh_token:
            raise WithingsOAuthError("not_connected", status_code=409)
        try:
            client_id, client_secret = resolve_withings_credentials(connection)
            refresh_token = decrypt_token(connection.encrypted_refresh_token)
        except WithingsCredentialUnavailableError:
            raise WithingsOAuthError("credential_unavailable") from None
        except Exception:
            from app.withings.service import _record_withings_failure

            _record_withings_failure(db, user, timestamp, "reauth_required")
            raise WithingsOAuthError("reauth_required") from None

        from app.withings.service import (
            _persist_rotated_withings_tokens_locked,
            _record_withings_failure,
            _safe_error_code,
        )

        adapter = oauth_adapter or _WithingsOAuthAdapter()
        try:
            payload = adapter.refresh(
                refresh_token=refresh_token,
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

        _persist_rotated_withings_tokens_locked(db, user, payload, now=timestamp)
        updated = db.scalar(
            select(WithingsConnection).where(WithingsConnection.user_id == user.id)
        )
        if updated is None:
            raise WithingsOAuthError("not_connected", status_code=409)
        return updated
