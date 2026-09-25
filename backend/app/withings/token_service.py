from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode

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


class _NoTokenRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


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
        request = urllib.request.Request(
            WITHINGS_TOKEN_URL,
            data=urlencode(payload).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        opener = urllib.request.build_opener(_NoTokenRedirect())
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status == 403:
                code = "scope_missing"
            elif status == 429:
                code = "rate_limited"
            elif status >= 500:
                code = "provider_error"
            else:
                code = "invalid_response"
            raise WithingsTokenExchangeError(code) from None

        try:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status is not None and 300 <= status < 400:
                raise WithingsTokenExchangeError("invalid_response")
            response_body = response.read(2 * 1024 * 1024 + 1)
        finally:
            response.close()

        if len(response_body) > 2 * 1024 * 1024:
            raise WithingsTokenExchangeError("invalid_response")
        try:
            result = json.loads(response_body)
        except ValueError:
            raise WithingsTokenExchangeError("invalid_response") from None
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
