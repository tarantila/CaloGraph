"""Read-only, user-scoped Google Health connection verification."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.google_health.client import GoogleHealthClient
from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.google_health.credentials import resolve_google_health_credentials
from app.google_health.errors import (
    GoogleHealthAuthenticationError,
    GoogleHealthScopeError,
)
from app.models import GoogleHealthConnection
from app.security_events import log_security_event, security_reference
from app.services.credential_crypto import decrypt_credential
from app.services.google_health_nutrition_sync import _default_credentials


class _ReadOnlyClient(Protocol):
    def probe_nutrition_log(
        self,
        *,
        page_size: int,
        civil_start_time: date | None = None,
        civil_end_time: date | None = None,
    ) -> int: ...

    def probe_data_points(
        self,
        data_type: str,
        *,
        start_time: datetime,
        end_time: datetime,
        page_size: int,
    ) -> int: ...

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
_RETRYABLE_CATEGORIES = frozenset({"rate_limited", "provider_error", "transient_error"})
_REAUTH_CATEGORIES = frozenset(
    {"credentials_unavailable", "reauth_required", "scope_missing"}
)


@dataclass(frozen=True, slots=True)
class GoogleHealthConnectionTestDiagnostic:
    domain: str
    operation: str
    endpoint_key: str
    upstream_status_code: int | None
    error_category: str
    retryable: bool
    reauth_required: bool


@dataclass(frozen=True, slots=True)
class GoogleHealthConnectionTestResult:
    status: str
    error_category: str | None = None
    diagnostic: GoogleHealthConnectionTestDiagnostic | None = None


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

    @classmethod
    def _diagnostic(
        cls,
        *,
        domain: str,
        operation: str,
        endpoint_key: str,
        exc: BaseException,
    ) -> GoogleHealthConnectionTestDiagnostic:
        category = cls._category(exc)
        status = getattr(exc, "upstream_status_code", None)
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            status = None
        return GoogleHealthConnectionTestDiagnostic(
            domain=domain,
            operation=operation,
            endpoint_key=endpoint_key,
            upstream_status_code=status,
            error_category=category,
            retryable=category in _RETRYABLE_CATEGORIES,
            reauth_required=category in _REAUTH_CATEGORIES,
        )

    @staticmethod
    def _log_failure(user_id: UUID, diagnostic: GoogleHealthConnectionTestDiagnostic) -> None:
        details: dict[str, object] = {
            "domain": diagnostic.domain,
            "operation": diagnostic.operation,
            "endpoint_key": diagnostic.endpoint_key,
            "error_category": diagnostic.error_category,
            "retryable": diagnostic.retryable,
            "reauth_required": diagnostic.reauth_required,
        }
        if diagnostic.upstream_status_code is not None:
            details["upstream_status_code"] = diagnostic.upstream_status_code
        with suppress(Exception):
            log_security_event(
                "integration.google_health.connection_test_failed",
                target_ref=security_reference("google_health", user_id),
                details=details,
            )

    def test(self, *, user_id: UUID) -> GoogleHealthConnectionTestResult:
        connection = self._connection(user_id)
        if connection is None or connection.state != "active":
            diagnostic = GoogleHealthConnectionTestDiagnostic(
                "connection",
                "connection_state",
                "connection_state",
                None,
                "reauth_required",
                False,
                True,
            )
            self._log_failure(user_id, diagnostic)
            return GoogleHealthConnectionTestResult(
                "reauth_required", "reauth_required", diagnostic
            )
        if not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ())):
            diagnostic = GoogleHealthConnectionTestDiagnostic(
                "connection",
                "scope_validation",
                "scope_validation",
                None,
                "scope_missing",
                False,
                True,
            )
            self._log_failure(user_id, diagnostic)
            return GoogleHealthConnectionTestResult("reauth_required", "scope_missing", diagnostic)
        try:
            client_id, client_secret = resolve_google_health_credentials(connection)
            refresh_token = self._decrypt_refresh_token(connection.encrypted_refresh_token or b"")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError("missing refresh token")
            credentials = self._credentials_factory(refresh_token, client_id, client_secret)
            client = self._client_factory(credentials)
        except Exception:
            diagnostic = GoogleHealthConnectionTestDiagnostic(
                "connection",
                "credential_resolution",
                "credential_resolution",
                None,
                "credentials_unavailable",
                False,
                True,
            )
            self._log_failure(user_id, diagnostic)
            return GoogleHealthConnectionTestResult(
                "reauth_required", "credentials_unavailable", diagnostic
            )
        finally:
            refresh_token = ""
            client_secret = ""

        now = datetime.now(UTC)
        probes: tuple[tuple[str, str, str, Callable[[], object]], ...] = (
            (
                "nutrition",
                "nutrition_read",
                "nutrition_log_data_points",
                lambda: client.probe_nutrition_log(
                    page_size=self._page_size,
                    civil_start_time=now.date(),
                    civil_end_time=now.date() + timedelta(days=1),
                ),
            ),
            (
                "activity_energy",
                "activity_read",
                "active_energy_burned_data_points",
                lambda: client.probe_data_points(
                    "active-energy-burned",
                    start_time=now - timedelta(days=1),
                    end_time=now,
                    page_size=self._page_size,
                ),
            ),
            (
                "weight",
                "weight_read",
                "weight_data_points",
                lambda: client.probe_data_points(
                    "weight",
                    start_time=now - timedelta(days=1),
                    end_time=now,
                    page_size=self._page_size,
                ),
            ),
        )
        try:
            for domain, operation, endpoint_key, probe in probes:
                try:
                    probe()
                except Exception as exc:
                    diagnostic = self._diagnostic(
                        domain=domain,
                        operation=operation,
                        endpoint_key=endpoint_key,
                        exc=exc,
                    )
                    self._log_failure(user_id, diagnostic)
                    return GoogleHealthConnectionTestResult(
                        "reauth_required" if diagnostic.reauth_required else "failed",
                        diagnostic.error_category,
                        diagnostic,
                    )
        finally:
            with suppress(Exception):
                client.close()
        return GoogleHealthConnectionTestResult("connected")


__all__ = [
    "GoogleHealthConnectionTestDiagnostic",
    "GoogleHealthConnectionTestResult",
    "GoogleHealthConnectionTestService",
]
