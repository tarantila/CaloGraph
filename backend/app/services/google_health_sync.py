"""Bounded, safe orchestration for Google Health Nutrition, Activity and Weight."""

from __future__ import annotations

import time as time_module
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.google_health.client import (
    GOOGLE_HEALTH_MAX_PAGE_SIZE,
    GoogleHealthClient,
    GoogleHealthDataPointPage,
    NutritionLogDataPoint,
    NutritionLogPage,
)
from app.google_health.constants import GOOGLE_HEALTH_REQUIRED_SCOPES
from app.google_health.credentials import resolve_google_health_credentials
from app.models import GoogleHealthConnection, User
from app.nutrition.enums import CoverageState
from app.nutrition.models import NutritionSourceObservation
from app.nutrition.projection.lifecycle import rebuild_affected_nutrition_days
from app.services.credential_crypto import decrypt_credential
from app.services.google_health_nutrition_ingestion import ingest_google_health_nutrition_logs
from app.services.google_health_nutrition_sync import _default_credentials
from app.services.google_health_scalar_sync import (
    sync_google_health_activity,
    sync_google_health_weight,
)
from app.source_priority.bootstrap import bootstrap_nutrition_priority

DEFAULT_MAX_SYNC_PAGES = 100
DEFAULT_MAX_SYNC_POINTS = 10_000


MAX_SYNC_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 2.0)
RETRYABLE_CODES = frozenset({"rate_limited", "provider_error", "transient_error"})
Sleep = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class GoogleHealthDomainResult:
    status: str
    fetched_count: int
    persisted_count: int
    requested_start: date
    requested_end: date
    covered_start: date | None = None
    covered_end: date | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class GoogleHealthSyncResult:
    status: str
    nutrition: GoogleHealthDomainResult
    activity_energy: GoogleHealthDomainResult
    weight: GoogleHealthDomainResult
    provider_attempted: bool = True


class _PagedClient(Protocol):
    def get_nutrition_log_page(self, *, page_size: int, page_token: str | None = None,
                               civil_start_time: date | None = None,
                               civil_end_time: date | None = None) -> NutritionLogPage: ...

    def get_data_points_page(self, data_type: str, start_time: datetime | None = None,
                             end_time: datetime | None = None, page_token: str | None = None,
                             page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE) -> GoogleHealthDataPointPage: ...

    def close(self) -> None: ...


SessionFactory = Callable[[], Session]
ClientFactory = Callable[[object], _PagedClient]
CredentialsFactory = Callable[[str, str, str], object]
DecryptRefreshToken = Callable[[bytes], str]

_SAFE_CODES = frozenset({
    "connection_not_configured", "connection_inactive", "scope_missing", "reauth_required",
    "credential_decryption_error", "credentials_unavailable", "rate_limited", "transient_error",
    "provider_error", "invalid_response", "pagination_error", "persistence_error",
})
_REAUTH_CODES = frozenset({"scope_missing", "reauth_required", "credential_decryption_error", "credentials_unavailable"})
class _PostCommitPersistenceError(RuntimeError):
    def __init__(self, persisted_count: int) -> None:
        self.persisted_count = persisted_count
        super().__init__("Google Health post-commit processing failed")


class _RetryStatusPersistenceError(RuntimeError):
    pass


def _record_connection_status(
    session_factory: SessionFactory,
    *,
    user_id: UUID,
    error_code: str | None = None,
    success: bool = False,
) -> None:
    db: Session | None = None
    try:
        db = session_factory()
        connection = db.scalar(
            select(GoogleHealthConnection)
            .where(GoogleHealthConnection.user_id == user_id)
            .with_for_update()
        )
        if connection is None:
            return
        if success:
            connection.last_success_at = datetime.now(UTC)
            connection.last_error = None
            connection.last_error_category = None
            connection.state = "active"
        elif error_code is not None:
            safe_code = error_code if error_code in _SAFE_CODES else "provider_error"
            connection.last_error = safe_code
            connection.last_error_category = safe_code
            if safe_code in _REAUTH_CODES:
                connection.state = "reauth_required"
        db.commit()
    except Exception:
        if db is not None:
            with suppress(Exception):
                db.rollback()
    finally:
        if db is not None:
            db.close()


def _safe_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code in _SAFE_CODES else "invalid_response"


def _result(
    *,
    status: str,
    fetched: int,
    persisted: int,
    start: date,
    end: date,
    covered: tuple[date | None, date | None] = (None, None),
    error: str | None = None,
) -> GoogleHealthDomainResult:
    return GoogleHealthDomainResult(
        status=status,
        fetched_count=fetched,
        persisted_count=persisted,
        requested_start=start,
        requested_end=end,
        covered_start=covered[0],
        covered_end=covered[1],
        error_code=error,
    )


def _empty_domains(
    start: date,
    end: date,
    *,
    status: str,
    error: str | None = None,
) -> GoogleHealthSyncResult:
    item = _result(status=status, fetched=0, persisted=0, start=start, end=end, error=error)
    return GoogleHealthSyncResult(
        status="reauth_required" if status == "reauth_required" else "failed",
        nutrition=item,
        activity_energy=item,
        weight=item,
        provider_attempted=False,
    )


class GoogleHealthSyncService:
    """Read all domains with independent finite budgets and write each independently."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        client_factory: ClientFactory | None = None,
        credentials_factory: CredentialsFactory = _default_credentials,
        decrypt_refresh_token: DecryptRefreshToken = decrypt_credential,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_SYNC_PAGES,
        max_points: int = DEFAULT_MAX_SYNC_POINTS,
        sleep: Sleep = time_module.sleep,
    ) -> None:
        if not isinstance(page_size, int) or page_size <= 0:
            raise ValueError("page_size must be positive")
        if not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if not isinstance(max_points, int) or max_points <= 0:
            raise ValueError("max_points must be positive")
        self._session_factory = session_factory
        self._client_factory = client_factory or (lambda credentials: GoogleHealthClient(None, credentials))
        self._credentials_factory = credentials_factory
        self._decrypt_refresh_token = decrypt_refresh_token
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_points = max_points
        self._sleep = sleep

    def _update_retry_status(
        self,
        *,
        user_id: UUID,
        state: str,
        attempt: int,
        next_retry_at: datetime | None = None,
        error_category: str | None = None,
        success: bool = False,
    ) -> None:
        db: Session | None = None
        try:
            db = self._session_factory()
            connection = db.scalar(
                select(GoogleHealthConnection)
                .where(GoogleHealthConnection.user_id == user_id)
                .with_for_update()
            )
            if connection is None:
                return
            now = datetime.now(UTC)
            connection.sync_state = state
            connection.retry_attempt = attempt
            connection.retry_max_attempts = MAX_SYNC_ATTEMPTS
            connection.next_retry_at = next_retry_at
            connection.last_attempt_at = now
            connection.last_error_category = error_category
            connection.last_error = error_category
            if success:
                connection.last_success_at = now
                connection.last_error = None
                connection.last_error_category = None
            db.commit()
        except Exception:
            if db is not None:
                with suppress(Exception):
                    db.rollback()
            raise _RetryStatusPersistenceError() from None
        finally:
            if db is not None:
                db.close()

    def _snapshot(self, user_id: UUID) -> tuple[UUID, str, bytes, str, str] | str:

        db = self._session_factory()
        try:
            row = db.scalar(select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user_id))
            user = db.scalar(select(User).where(User.id == user_id))
            attempted = datetime.now(UTC)
            if row is None or user is None:
                return "connection_not_configured"
            if row.state != "active":
                row.last_attempt_at = attempted
                row.last_error = "connection_inactive"
                db.commit()
                return "reauth_required"
            if not GOOGLE_HEALTH_REQUIRED_SCOPES.issubset(set(row.granted_scopes or ())):
                row.last_attempt_at = attempted
                row.last_error = "scope_missing"
                row.state = "reauth_required"
                db.commit()
                return "scope_missing"
            try:
                client_id, client_secret = resolve_google_health_credentials(row)
            except Exception:
                row.last_attempt_at = attempted
                row.last_error = "credentials_unavailable"
                row.state = "reauth_required"
                db.commit()
                return "credentials_unavailable"
            if not isinstance(user.timezone, str) or not user.timezone:
                return "provider_error"
            # Validate the timezone while the user row is still scoped.
            try:
                ZoneInfo(user.timezone)
            except (ValueError, ZoneInfoNotFoundError):
                return "provider_error"
            if not row.encrypted_refresh_token:
                row.last_error = "credentials_unavailable"
                row.state = "reauth_required"
                db.commit()
                return "credentials_unavailable"
            row.last_attempt_at = attempted
            db.commit()
            return row.id, user.timezone, row.encrypted_refresh_token, client_id, client_secret
        finally:
            db.close()

    def _pages(self, client: _PagedClient, *, data_type: str, start: date, end: date,
               timezone: str) -> tuple[tuple[Any, ...], bool]:
        points: list[Any] = []
        token: str | None = None
        seen: set[str | None] = {None}
        local_start = datetime.combine(start, time.min, tzinfo=ZoneInfo(timezone))
        local_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone))
        iterator_factory = getattr(client, "iter_data_points_pages", None)
        direct = (
            getattr(client, "get_nutrition_log_page", None)
            if data_type == "nutrition-log"
            else (
                None
                if callable(iterator_factory)
                else getattr(client, "get_data_points_page", None)
            )
        )
        if not callable(direct):
            if not callable(iterator_factory):
                raise ValueError("Google Health client cannot page data")
            iterator = iterator_factory(
                data_type,
                start_time=local_start.astimezone(UTC),
                end_time=local_end.astimezone(UTC),
                page_size=self._page_size,
                max_pages=self._max_pages,
            )
            truncated = False
            for page_number, page in enumerate(iterator):
                available = tuple(page.data_points)
                remaining = self._max_points - len(points)
                points.extend(available[:max(remaining, 0)])
                if len(available) > remaining or (
                    len(available) >= remaining and page.next_page_token is not None
                ):
                    truncated = True
                    break
                if page_number + 1 >= self._max_pages:
                    truncated = page.next_page_token is not None
                    break
            return tuple(points), truncated

        truncated = False
        for page_number in range(self._max_pages):
            if data_type == "nutrition-log":
                page = direct(
                    page_size=self._page_size,
                    page_token=token,
                    civil_start_time=start,
                    civil_end_time=end + timedelta(days=1),
                )
            else:
                page = direct(
                    data_type,
                    start_time=local_start.astimezone(UTC),
                    end_time=local_end.astimezone(UTC),
                    page_token=token,
                    page_size=self._page_size,
                )
            available = tuple(page.data_points)
            remaining = self._max_points - len(points)
            points.extend(available[:max(remaining, 0)])
            if len(available) > remaining or (
                len(available) >= remaining and page.next_page_token is not None
            ):
                truncated = True
                break
            next_token = page.next_page_token
            if next_token is None:
                break
            if next_token in seen:
                raise ValueError("pagination token repeated")
            seen.add(next_token)
            if page_number + 1 >= self._max_pages:
                truncated = True
                break
            token = next_token
        else:
            truncated = True
        return tuple(points), truncated

    @staticmethod
    def _covered(points: Iterable[Any], *, timezone: str, nutrition: bool) -> tuple[date | None, date | None]:
        dates: list[date] = []
        zone = ZoneInfo(timezone)
        for point in points:
            interval = getattr(getattr(point, "nutrition_log", None), "interval", None)
            if nutrition and interval is not None:
                civil = interval.civil_start_time
                if isinstance(civil, datetime):
                    dates.append(civil.date())
                elif isinstance(civil, date):
                    dates.append(civil)
            else:
                timestamp = getattr(point, "start_time", None)
                if isinstance(timestamp, datetime):
                    dates.append(timestamp.astimezone(zone).date())
        return (min(dates), max(dates)) if dates else (None, None)

    def _persist_nutrition(self, *, user_id: UUID, source_id: UUID, start: date, end: date,
                           points: tuple[NutritionLogDataPoint, ...], truncated: bool) -> int:
        db = self._session_factory()
        try:
            run = ingest_google_health_nutrition_logs(
                db, user_id=user_id, source_instance_id=source_id, requested_start=start,
                requested_end=end, data_points=points,
                coverage_state=CoverageState.PARTIAL.value if truncated else CoverageState.COMPLETE.value,
            )
            count = int(db.scalar(select(func.count(NutritionSourceObservation.id)).where(
                NutritionSourceObservation.ingestion_run_id == run.id)) or 0)
            db.commit()
        except Exception:
            with suppress(Exception):
                db.rollback()
            raise
        finally:
            db.close()
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
            raise _PostCommitPersistenceError(count) from None
        return count

    def _persist_scalar(
        self,
        *,
        user_id: UUID,
        source_id: UUID,
        start: date,
        end: date,
        points: tuple[Any, ...],
        activity: bool,
    ) -> int:
        db = self._session_factory()
        try:
            result = (sync_google_health_activity if activity else sync_google_health_weight)(
                db,
                user_id=user_id,
                source_instance_id=source_id,
                requested_start=start,
                requested_end=end,
                data_points=points,
            )
            db.commit()
            return result.persisted_count
        except Exception:
            with suppress(Exception):
                db.rollback()
            raise
        finally:
            db.close()

    def _sync_once(
        self,
        *,
        user_id: UUID,
        requested_start: date,
        requested_end: date,
        before_provider_attempt: Callable[[], None] | None = None,
    ) -> GoogleHealthSyncResult:
        if requested_start > requested_end:
            raise ValueError("requested date range is invalid")
        snapshot = self._snapshot(user_id)
        if isinstance(snapshot, str):
            status = "reauth_required" if snapshot in {"scope_missing", "reauth_required", "credentials_unavailable"} else "failed"
            return _empty_domains(
                requested_start,
                requested_end,
                status=status,
                error=snapshot,
            )
        source_id, timezone, encrypted, client_id, client_secret = snapshot
        try:
            refresh = self._decrypt_refresh_token(encrypted)
            if not isinstance(refresh, str) or not refresh:
                raise ValueError("empty credential")
        except Exception:
            code = "credential_decryption_error"
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=code,
            )
            return _empty_domains(
                requested_start,
                requested_end,
                status="reauth_required",
                error=code,
            )
        try:
            credentials = self._credentials_factory(refresh, client_id, client_secret)
            client = self._client_factory(credentials)
        except Exception:
            code = "credentials_unavailable"
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=code,
            )
            return _empty_domains(
                requested_start,
                requested_end,
                status="reauth_required",
                error=code,
            )
        finally:
            refresh = ""
            client_secret = ""

        if before_provider_attempt is not None:
            try:
                before_provider_attempt()
            except Exception:
                with suppress(Exception):
                    client.close()
                raise


        domains: dict[str, GoogleHealthDomainResult] = {}
        try:
            definitions = (("nutrition", "nutrition-log", True), ("activity_energy", "active-energy-burned", False),
                           ("weight", "weight", False))
            for name, data_type, is_nutrition in definitions:
                try:
                    points, truncated = self._pages(
                        client,
                        data_type=data_type,
                        start=requested_start,
                        end=requested_end,
                        timezone=timezone,
                    )
                except Exception as exc:
                    code = _safe_code(exc)
                    _record_connection_status(
                        self._session_factory,
                        user_id=user_id,
                        error_code=code,
                    )
                    domains[name] = _result(
                        status="reauth_required" if code in _REAUTH_CODES else "failed",
                        fetched=0,
                        persisted=0,
                        start=requested_start,
                        end=requested_end,
                        error=code,
                    )
                    if code in _REAUTH_CODES:
                        for remaining_name, _, _ in definitions:
                            if remaining_name not in domains:
                                domains[remaining_name] = _result(
                                    status="reauth_required",
                                    fetched=0,
                                    persisted=0,
                                    start=requested_start,
                                    end=requested_end,
                                    error=code,
                                )
                        break
                    continue
                covered = self._covered(
                    points,
                    timezone=timezone,
                    nutrition=is_nutrition,
                )
                try:
                    if is_nutrition:
                        persisted = self._persist_nutrition(
                            user_id=user_id,
                            source_id=source_id,
                            start=requested_start,
                            end=requested_end,
                            points=points,
                            truncated=truncated,
                        )
                    else:
                        persisted = self._persist_scalar(
                            user_id=user_id,
                            source_id=source_id,
                            start=requested_start,
                            end=requested_end,
                            points=points,
                            activity=name == "activity_energy",
                        )
                except Exception as exc:
                    persisted_count = int(getattr(exc, "persisted_count", 0))
                    _record_connection_status(
                        self._session_factory,
                        user_id=user_id,
                        error_code="persistence_error",
                    )
                    domains[name] = _result(
                        status="failed",
                        fetched=len(points),
                        persisted=persisted_count,
                        start=requested_start,
                        end=requested_end,
                        covered=covered,
                        error="persistence_error",
                    )
                    continue
                status = "truncated" if truncated else ("no_data" if not points else "success")
                domains[name] = _result(
                    status=status,
                    fetched=len(points),
                    persisted=persisted,
                    start=requested_start,
                    end=requested_end,
                    covered=covered,
                )
        finally:
            with suppress(Exception):
                client.close()

        nutrition = domains["nutrition"]
        activity = domains["activity_energy"]
        weight = domains["weight"]
        statuses = (nutrition.status, activity.status, weight.status)
        if all(item == "reauth_required" for item in statuses):
            aggregate = "reauth_required"
        elif all(item == "no_data" for item in statuses):
            aggregate = "no_data"
        elif any(item == "failed" for item in statuses) and all(item == "failed" for item in statuses):
            aggregate = "failed"
        elif any(item in {"failed", "reauth_required"} for item in statuses):
            aggregate = "partial_failure"
        else:
            aggregate = "success"
        if aggregate in {"success", "no_data"}:
            _record_connection_status(self._session_factory, user_id=user_id, success=True)
        else:
            error_codes = [
                item.error_code for item in (nutrition, activity, weight) if item.error_code
            ]
            first_error = next(
                (code for code in error_codes if code in _REAUTH_CODES),
                error_codes[0] if error_codes else "provider_error",
            )
            _record_connection_status(
                self._session_factory,
                user_id=user_id,
                error_code=first_error,
            )
        return GoogleHealthSyncResult(
            status=aggregate,
            nutrition=nutrition,
            activity_energy=activity,
            weight=weight,
        )

    @staticmethod
    def _retry_category(result: GoogleHealthSyncResult) -> str | None:
        codes = [
            item.error_code
            for item in (result.nutrition, result.activity_energy, result.weight)
            if item.error_code
        ]
        if not codes:
            return None
        for code in codes:
            if code not in RETRYABLE_CODES:
                return code
        return codes[0]

    def sync(
        self, *, user_id: UUID, requested_start: date, requested_end: date
    ) -> GoogleHealthSyncResult:
        if requested_start > requested_end:
            raise ValueError("requested date range is invalid")
        persisted_totals = {"nutrition": 0, "activity_energy": 0, "weight": 0}
        for attempt in range(1, MAX_SYNC_ATTEMPTS + 1):
            provider_attempted = False

            def before_provider_attempt(current_attempt: int = attempt) -> None:
                nonlocal provider_attempted
                self._update_retry_status(
                    user_id=user_id,
                    state="running",
                    attempt=current_attempt,
                )
                provider_attempted = True
            try:
                result = self._sync_once(
                    user_id=user_id,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    before_provider_attempt=before_provider_attempt,
                )
            except _RetryStatusPersistenceError:
                return _empty_domains(
                    requested_start,
                    requested_end,
                    status="failed",
                    error="persistence_error",
                )
            except Exception as exc:
                failure_code = _safe_code(exc)
                result = _empty_domains(
                    requested_start,
                    requested_end,
                    status="reauth_required" if failure_code in _REAUTH_CODES else "failed",
                    error=failure_code,
                )
            for domain_name in persisted_totals:
                domain_result = getattr(result, domain_name)
                persisted_totals[domain_name] += domain_result.persisted_count
                result = replace(
                    result,
                    **{
                        domain_name: replace(
                            domain_result,
                            persisted_count=persisted_totals[domain_name],
                        )
                    },
                )
            retry_category = self._retry_category(result)
            if not provider_attempted and result.provider_attempted:
                provider_attempted = True
            if not provider_attempted or not result.provider_attempted:
                try:
                    self._update_retry_status(
                        user_id=user_id,
                        state="failed",
                        attempt=0,
                        error_category=retry_category,
                    )
                except _RetryStatusPersistenceError:
                    return _empty_domains(
                        requested_start,
                        requested_end,
                        status="failed",
                        error="persistence_error",
                    )
                return result
            if retry_category not in RETRYABLE_CODES:
                try:
                    self._update_retry_status(
                        user_id=user_id,
                        state="completed" if result.status in {"success", "no_data"} else "failed",
                        attempt=attempt,
                        error_category=retry_category,
                        success=result.status in {"success", "no_data"},
                    )
                except _RetryStatusPersistenceError:
                    return _empty_domains(
                        requested_start,
                        requested_end,
                        status="failed",
                        error="persistence_error",
                    )
                return result
            if attempt >= MAX_SYNC_ATTEMPTS:
                try:
                    self._update_retry_status(
                        user_id=user_id,
                        state="failed",
                        attempt=attempt,
                        error_category=retry_category,
                    )
                except _RetryStatusPersistenceError:
                    return _empty_domains(
                        requested_start,
                        requested_end,
                        status="failed",
                        error="persistence_error",
                    )
                return result
            next_retry_at = datetime.now(UTC) + timedelta(
                seconds=RETRY_BACKOFF_SECONDS[attempt - 1]
            )
            try:
                self._update_retry_status(
                    user_id=user_id,
                    state="running",
                    attempt=attempt,
                    next_retry_at=next_retry_at,
                    error_category=retry_category,
                )
            except _RetryStatusPersistenceError:
                return _empty_domains(
                    requested_start,
                    requested_end,
                    status="failed",
                    error="persistence_error",
                )
            self._sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
        raise AssertionError("sync retry loop did not return")


__all__ = ["GoogleHealthDomainResult", "GoogleHealthSyncResult", "GoogleHealthSyncService"]
