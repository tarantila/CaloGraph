"""Read-only, user-scoped Google Health connection verification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.google_health.credentials import resolve_google_health_credentials
from app.google_health.errors import (
    GoogleHealthAuthenticationError,
    GoogleHealthScopeError,
)
from app.google_health.client import GoogleHealthClient
from app.models import GoogleHealthConnection
from app.services.credential_crypto import decrypt_credential
from app.services.google_health_nutrition_sync import _default_credentials


class _ReadOnlyClient(Protocol):
    def get_nutrition_log_page(
        self,
        *,
        page_size: int,
        civil_start_time: date | None = None,
        civil_end_time: date | None = None,
    ) -> object: ...

    def get_data_points_page(
        self,
        data_type: str,
        *,
        start_time: datetime,
        end_time: datetime,
        page_size: int,
    ) -> object: ...

    def close(self) -> None: ...


SessionFactory = Callable[[], Session]
ClientFactory = Callable[[object], _ReadOnlyClient]
CredentialsFactory = Callable[[str, str, str], object]
DecryptRefreshToken = Callable[[bytes], str]

_SAFE_CATEGORIES = frozenset(
    {
        "credentials_unavailable",
        "reauth_required",
        "scope_missing",
        "rate_limited",
        "provider_error",
        "transient_error",
        "invalid_response",
    }
)


@dataclass(frozen=True, slots=True)
class GoogleHealthConnectionTestResult:
    status: str
    error_category: str | None = None


class GoogleHealthConnectionTestService:
    """Verify the current user's token using bounded GET requests only."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        client_factory: ClientFactory | None = None,
        credentials_factory: CredentialsFactory = _default_credentials,
        decrypt_refresh_token: DecryptRefreshToken = decrypt_credential,
        page_size: int = 1,
    ) -> None:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 0 < page_size <= 10:
            raise ValueError("page_size must be between 1 and 10")
        self._session_factory = session_factory
        self._client_factory = client_factory or (lambda credentials: GoogleHealthClient(None, credentials))
        self._credentials_factory = credentials_factory
        self._decrypt_refresh_token = decrypt_refresh_token
        self._page_size = page_size

    def _connection(self, user_id: UUID) -> GoogleHealthConnection | None:
        db = self._session_factory()
        try:
            return db.scalar(
                select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user_id)
            )
        finally:
            db.close()

    @staticmethod
    def _category(exc: BaseException) -> str:
        if isinstance(exc, GoogleHealthAuthenticationError):
            return "reauth_required"
        if isinstance(exc, GoogleHealthScopeError):
            return "scope_missing"
        code = getattr(exc, "code", None)
        return code if isinstance(code, str) and code in _SAFE_CATEGORIES else "provider_error"

    def test(self, *, user_id: UUID) -> GoogleHealthConnectionTestResult:
        connection = self._connection(user_id)
        if connection is None or connection.state != "active":
            return GoogleHealthConnectionTestResult("reauth_required", "reauth_required")
        if not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ())):
            return GoogleHealthConnectionTestResult("reauth_required", "scope_missing")
        try:
            client_id, client_secret = resolve_google_health_credentials(connection)
            refresh_token = self._decrypt_refresh_token(connection.encrypted_refresh_token or b"")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError("missing refresh token")
            credentials = self._credentials_factory(refresh_token, client_id, client_secret)
            client = self._client_factory(credentials)
        except Exception:
            return GoogleHealthConnectionTestResult("reauth_required", "credentials_unavailable")
        finally:
            refresh_token = ""
            client_secret = ""

        now = datetime.now(UTC)
        try:
            # Each request is a bounded GET and the response is deliberately discarded.
            client.get_nutrition_log_page(
                page_size=self._page_size,
                civil_start_time=now.date(),
                civil_end_time=now.date() + timedelta(days=1),
            )
            client.get_data_points_page(
                "active-energy-burned",
                start_time=now - timedelta(days=1),
                end_time=now,
                page_size=self._page_size,
            )
            client.get_data_points_page(
                "weight",
                start_time=now - timedelta(days=1),
                end_time=now,
                page_size=self._page_size,
            )
        except Exception as exc:
            category = self._category(exc)
            return GoogleHealthConnectionTestResult(
                "reauth_required" if category in {"reauth_required", "scope_missing"} else "failed",
                category,
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
        return GoogleHealthConnectionTestResult("connected")


__all__ = ["GoogleHealthConnectionTestResult", "GoogleHealthConnectionTestService"]
