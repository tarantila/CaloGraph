"""Read, aggregate, and persist Google Health Nutrition Log pages.

The service deliberately keeps provider I/O outside the Nutrition write boundary.
Only bounded DTOs cross into the injected persistence adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.google_health.client import (
    GOOGLE_HEALTH_MAX_PAGE_SIZE,
    GoogleHealthClient,
    NutritionLogDataPoint,
    NutritionLogPage,
)
from app.google_health.constants import (
    GOOGLE_HEALTH_REQUIRED_SCOPES,
    GOOGLE_HEALTH_SCOPES,
    GOOGLE_HEALTH_TOKEN_URI,
)
from app.google_health.errors import GoogleHealthClientError
from app.models import GoogleHealthConnection
from app.nutrition.models import NutritionSourceObservation
from app.nutrition.projection.lifecycle import rebuild_affected_nutrition_days
from app.services.credential_crypto import decrypt_credential
from app.services.google_health_nutrition_ingestion import (
    ingest_google_health_nutrition_logs,
)
from app.source_priority.bootstrap import bootstrap_nutrition_priority

DEFAULT_MAX_SYNC_PAGES = 100


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
    "scope_missing": "Google Health readonly permissions are unavailable.",
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
_REAUTH_ERROR_CODES = frozenset(
    {
        "reauth_required",
        "scope_missing",
        "credential_decryption_error",
        "credentials_unavailable",
    }
)


def _record_connection_status(
    session_factory: SessionFactory,
    *,
    user_id: UUID,
    error_code: str | None = None,
    success_at: datetime | None = None,
) -> None:
    """Record sync outcome metadata without changing the sync's error contract."""
    db: Session | None = None
    try:
        db = session_factory()
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(
                GoogleHealthConnection.user_id == user_id,
            )
            .with_for_update()
        )
        if connection is not None:
            if success_at is not None:
                connection.last_success_at = success_at
                connection.last_error = None
                connection.state = "active"
            elif error_code is not None:
                safe_code = error_code if error_code in _ERROR_MESSAGES else "provider_error"
                connection.last_error = safe_code
                if safe_code in _REAUTH_ERROR_CODES:
                    connection.state = "reauth_required"
            db.commit()
    except Exception:
        if db is not None:
            with suppress(Exception):
                db.rollback()
    finally:
        if db is not None:
            db.close()



def _default_credentials(refresh_token: str) -> object:
    """Build Google credentials without persisting the decrypted refresh token."""
    from google.oauth2.credentials import Credentials

    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_HEALTH_TOKEN_URI,
        client_id=settings.google_health_client_id,
        client_secret=settings.google_health_client_secret,
        scopes=list(GOOGLE_HEALTH_SCOPES),
    )


def _default_client(credentials: object) -> _PagedClient:
    return GoogleHealthClient(None, credentials)


def _safe_error(code: str) -> GoogleHealthNutritionSyncError:
    safe_code = code if code in _ERROR_MESSAGES else "provider_error"
    return GoogleHealthNutritionSyncError(safe_code, _ERROR_MESSAGES[safe_code])


def _close_client(client: _PagedClient | None) -> None:
    with suppress(Exception):
        close = getattr(client, "close", None)
        if callable(close):
            close()


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
        """Fetch all pages, then persist them in one owned transaction."""
        if requested_start > requested_end:
            raise _safe_error("invalid_request")
        read_db = self._session_factory()
        try:
            connection = read_db.scalar(
                select(GoogleHealthConnection).where(
                    GoogleHealthConnection.user_id == user_id,
                )
            )
            if connection is None:
                raise _safe_error("connection_not_configured")

            attempted_at = datetime.now(UTC)
            if connection.state != "active":
                connection.last_attempt_at = attempted_at
                connection.last_error = "connection_inactive"
                read_db.commit()
                raise _safe_error("connection_inactive")
            if not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ())):
                connection.last_attempt_at = attempted_at
                connection.last_error = "scope_missing"
                connection.state = "reauth_required"
                read_db.commit()
                raise _safe_error("scope_missing")

            # Snapshot all values needed after the read session is released.
            source_instance_id = connection.id
            encrypted_refresh_token = connection.encrypted_refresh_token
            # Record the attempt before any provider or credential I/O.
            connection.last_attempt_at = attempted_at
            read_db.commit()
        finally:
            read_db.close()

        # These are intentionally local variables. They are never included in
        # a result, exception, log, or provider metadata value.
        try:
            refresh_token = self._decrypt_refresh_token(encrypted_refresh_token)
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError("empty credential")
        except Exception:
            error = _safe_error("credential_decryption_error")
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=error.code,
            )
            raise error from None

        try:
            credentials = self._credentials_factory(refresh_token)
            client = self._client_factory(credentials)
        except Exception:
            error = _safe_error("credentials_unavailable")
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=error.code,
            )
            raise error from None
        finally:
            # Do not retain the plaintext token on the service instance.
            refresh_token = ""

        points: list[NutritionLogDataPoint] = []
        page_token: str | None = None
        seen_tokens: set[str | None] = {None}
        civil_end = requested_end + timedelta(days=1)
        pages_read = 0
        try:
            while True:
                if pages_read >= DEFAULT_MAX_SYNC_PAGES:
                    raise _safe_error("pagination_error")
                page = client.get_nutrition_log_page(
                    page_size=self._page_size,
                    page_token=page_token,
                    civil_start_time=requested_start,
                    civil_end_time=civil_end,
                )
                pages_read += 1
                points.extend(page.data_points)
                next_page_token = page.next_page_token
                if next_page_token is None:
                    break
                if next_page_token in seen_tokens:
                    raise _safe_error("pagination_error")
                seen_tokens.add(next_page_token)
                page_token = next_page_token
        except GoogleHealthNutritionSyncError as exc:
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=exc.code,
            )
            raise
        except GoogleHealthClientError as exc:
            code = getattr(exc, "code", "provider_error")
            error = _safe_error(code if code in _PROVIDER_ERROR_CODES else "provider_error")
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=error.code,
            )
            raise error from None
        except Exception:
            error = _safe_error("provider_error")
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=error.code,
            )
            raise error from None
        finally:
            _close_client(client)

        write_db: Session | None = None
        persistence_error: GoogleHealthNutritionSyncError | None = None
        try:
            write_db = self._session_factory()
            run = self._adapter(
                write_db,
                user_id=user_id,
                source_instance_id=source_instance_id,
                requested_start=requested_start,
                requested_end=requested_end,
                data_points=tuple(points),
            )
            persisted_count = int(
                write_db.scalar(
                    select(func.count(NutritionSourceObservation.id)).where(
                        NutritionSourceObservation.ingestion_run_id == run.id,
                    )
                )
                or 0
            )
            write_db.commit()
            result = GoogleHealthNutritionSyncResult(
                status=cast(str, getattr(run, "status", "completed")),
                fetched_count=len(points),
                persisted_count=persisted_count,
                requested_start=requested_start,
                requested_end=requested_end,
                covered_start=cast(
                    date | None,
                    getattr(run, "covered_start_date", requested_start),
                ),
                covered_end=cast(
                    date | None,
                    getattr(run, "covered_end_date", requested_end),
                ),
                run=run,
            )
        except Exception:
            if write_db is not None:
                with suppress(Exception):
                    write_db.rollback()
            persistence_error = _safe_error("persistence_error")
        finally:
            if write_db is not None:
                write_db.close()

        if persistence_error is not None:
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=persistence_error.code,
            )
            raise persistence_error from None

        try:
            policy_at = datetime.now(UTC)
            bootstrap_nutrition_priority(
                session_factory=self._session_factory,
                user_id=user_id,
                effective_from=policy_at,
            )
            rebuild_affected_nutrition_days(
                session_factory=self._session_factory,
                user_id=user_id,
                ingestion_run_id=run.id,
                policy_at=policy_at,
            )
        except Exception:
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code="persistence_error",
            )
            raise

        _record_connection_status(
            self._session_factory,
            user_id=user_id,
            success_at=datetime.now(UTC),
        )
        return result


__all__ = [
    "GoogleHealthNutritionSyncError",
    "GoogleHealthNutritionSyncResult",
    "GoogleHealthNutritionSyncService",
]
