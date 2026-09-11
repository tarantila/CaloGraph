"""Read, aggregate, and persist Google Health Nutrition Log pages.

The service deliberately keeps provider I/O outside the Nutrition write boundary.
Only bounded DTOs cross into the injected persistence adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.google_health.client import (
    GOOGLE_HEALTH_MAX_PAGE_SIZE,
    GoogleHealthClient,
    NutritionLogDataPoint,
    NutritionLogPage,
)
from app.google_health.constants import (
    GOOGLE_HEALTH_SCOPE,
    GOOGLE_HEALTH_TOKEN_URI,
)
from app.google_health.errors import GoogleHealthClientError
from app.models import GoogleHealthConnection
from app.services.credential_crypto import decrypt_credential
from app.services.google_health_nutrition_ingestion import (
    ingest_google_health_nutrition_logs,
)


class GoogleHealthNutritionSyncError(RuntimeError):
    """A safe, typed failure from Google Health Nutrition synchronization."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class GoogleHealthNutritionSyncResult:
    """Safe aggregate returned after a successful synchronization."""

    status: str
    fetched_count: int
    persisted_count: int
    requested_start: date
    requested_end: date
    covered_start: date | None
    covered_end: date | None
    run: Any


class _PagedClient(Protocol):
    def get_nutrition_log_page(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
        start_time: None = None,
        end_time: None = None,
        civil_start_time: date | None = None,
        civil_end_time: date | None = None,
    ) -> NutritionLogPage: ...


SessionFactory = Callable[[], Session]
DecryptRefreshToken = Callable[[bytes], str]
CredentialsFactory = Callable[[str], object]
ClientFactory = Callable[[object], _PagedClient]
Adapter = Callable[..., Any]


_ERROR_MESSAGES = {
    "connection_not_configured": "Google Health connection is not configured.",
    "connection_inactive": "Google Health connection is inactive.",
    "scope_missing": "Google Health nutrition permission is unavailable.",
    "credential_decryption_error": "Google Health credentials are unavailable.",
    "credentials_unavailable": "Google Health credentials are unavailable.",
    "pagination_error": "Google Health returned invalid pagination.",
    "reauth_required": "Google Health requires reauthorization.",
    "rate_limited": "Google Health is temporarily unavailable.",
    "transient_error": "Google Health is temporarily unavailable.",
    "provider_error": "Google Health is temporarily unavailable.",
    "invalid_response": "Google Health returned an invalid response.",
    "persistence_error": "Google Health nutrition data could not be stored.",
    "invalid_request": "The requested date range is invalid.",
}
_PROVIDER_ERROR_CODES = frozenset(
    {
        "reauth_required",
        "scope_missing",
        "rate_limited",
        "transient_error",
        "provider_error",
        "invalid_response",
    }
)


def _default_credentials(refresh_token: str) -> object:
    """Build Google credentials without persisting the decrypted refresh token."""
    from google.oauth2.credentials import Credentials

    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_HEALTH_TOKEN_URI,
        client_id=settings.google_health_client_id,
        client_secret=settings.google_health_client_secret,
        scopes=[GOOGLE_HEALTH_SCOPE],
    )


def _default_client(credentials: object) -> _PagedClient:
    return GoogleHealthClient(None, credentials)


def _safe_error(code: str) -> GoogleHealthNutritionSyncError:
    safe_code = code if code in _ERROR_MESSAGES else "provider_error"
    return GoogleHealthNutritionSyncError(safe_code, _ERROR_MESSAGES[safe_code])


class GoogleHealthNutritionSyncService:
    """Orchestrate a complete, read-before-write Google Nutrition sync."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        client_factory: ClientFactory = _default_client,
        credentials_factory: CredentialsFactory = _default_credentials,
        decrypt_refresh_token: DecryptRefreshToken = decrypt_credential,
        adapter: Adapter = ingest_google_health_nutrition_logs,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
    ) -> None:
        if not isinstance(page_size, int) or page_size <= 0:
            raise ValueError("page_size must be positive")
        self._session_factory = session_factory
        self._client_factory = client_factory
        self._credentials_factory = credentials_factory
        self._decrypt_refresh_token = decrypt_refresh_token
        self._adapter = adapter
        self._page_size = page_size

    def sync(
        self,
        *,
        user_id: UUID,
        requested_start: date,
        requested_end: date,
    ) -> GoogleHealthNutritionSyncResult:
        """Fetch all pages, then invoke the adapter exactly once.

        Neither this method nor its injected adapter commits the caller-owned
        SQLAlchemy session.
        """
        if requested_start > requested_end:
            raise _safe_error("invalid_request")

        db = self._session_factory()
        connection = db.scalar(
            select(GoogleHealthConnection).where(
                GoogleHealthConnection.user_id == user_id,
            )
        )
        if connection is None:
            raise _safe_error("connection_not_configured")
        if connection.state != "active":
            raise _safe_error("connection_inactive")
        if GOOGLE_HEALTH_SCOPE not in (connection.granted_scopes or ()):
            raise _safe_error("scope_missing")

        # These are intentionally local variables.  They are never included in
        # a result, exception, log, or provider metadata value.
        try:
            refresh_token = self._decrypt_refresh_token(connection.encrypted_refresh_token)
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError("empty credential")
        except Exception:
            raise _safe_error("credential_decryption_error") from None

        try:
            credentials = self._credentials_factory(refresh_token)
            client = self._client_factory(credentials)
        except Exception:
            raise _safe_error("credentials_unavailable") from None
        finally:
            # Do not retain the plaintext token on the service instance.
            refresh_token = ""

        points: list[NutritionLogDataPoint] = []
        page_token: str | None = None
        seen_tokens: set[str | None] = {None}
        civil_end = requested_end + timedelta(days=1)
        try:
            while True:
                page = client.get_nutrition_log_page(
                    page_size=self._page_size,
                    page_token=page_token,
                    civil_start_time=requested_start,
                    civil_end_time=civil_end,
                )
                points.extend(page.data_points)
                next_page_token = page.next_page_token
                if next_page_token is None:
                    break
                if next_page_token in seen_tokens:
                    raise _safe_error("pagination_error")
                seen_tokens.add(next_page_token)
                page_token = next_page_token
        except GoogleHealthNutritionSyncError:
            raise
        except GoogleHealthClientError as exc:
            code = getattr(exc, "code", "provider_error")
            raise _safe_error(code if code in _PROVIDER_ERROR_CODES else "provider_error") from None
        except Exception:
            raise _safe_error("provider_error") from None

        # A nested transaction gives the adapter one atomic write boundary
        # while preserving ownership of the outer transaction and its commit.
        try:
            with db.begin_nested():
                run = self._adapter(
                    db,
                    user_id=user_id,
                    source_instance_id=connection.id,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    data_points=tuple(points),
                )
        except Exception:
            raise _safe_error("persistence_error") from None

        return GoogleHealthNutritionSyncResult(
            status=cast(str, getattr(run, "status", "completed")),
            fetched_count=len(points),
            persisted_count=len(points),
            requested_start=requested_start,
            requested_end=requested_end,
            covered_start=cast(date | None, getattr(run, "covered_start_date", requested_start)),
            covered_end=cast(date | None, getattr(run, "covered_end_date", requested_end)),
            run=run,
        )


__all__ = [
    "GoogleHealthNutritionSyncError",
    "GoogleHealthNutritionSyncResult",
    "GoogleHealthNutritionSyncService",
]
