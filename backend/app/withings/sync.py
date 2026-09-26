"""Safe, domain-isolated manual synchronization for Withings."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC
from app.importers.common import ORIGINAL_VALUE_LIMIT, CanonicalSample, local_date_for
from app.models import ImportBatch, User, WithingsConnection
from app.services.credential_crypto import CredentialEncryptionError
from app.services.import_service import _persist_sample_batch, _start_batch
from app.services.user_operation_lock import exclusive_user_lifecycle_operation
from app.withings.client import WithingsHTTPClient, WithingsPageResult
from app.withings.constants import (
    WITHINGS_ACTIVITY_SOURCE_TYPE,
    WITHINGS_REQUIRED_SCOPES,
)
from app.withings.credentials import decrypt_token
from app.withings.errors import (
    WithingsClientError,
    WithingsInvalidResponseError,
)
from app.withings.ingestion import ingest_withings_measurements
from app.withings.parsers import WithingsActivity, WithingsMeasureGroup
from app.withings.service import refresh_withings_tokens_default

LOGGER = logging.getLogger(__name__)


_SAFE_ERROR_CODES = frozenset(
    {
        "not_connected",
        "reauth_required",
        "scope_missing",
        "credential_unavailable",
        "rate_limited",
        "transient_error",
        "provider_error",
        "invalid_response",
        "persistence_error",
    }
)
_REAUTH_ERROR_CODES = frozenset({"reauth_required", "scope_missing", "credential_unavailable"})
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_SAFE_SQLSTATE = re.compile(r"[A-Z0-9]{5}\Z")
_SAFE_ACTIVITY_FIELDS = frozenset(
    {
        "metric_type",
        "value",
        "unit",
        "original_value",
        "original_unit",
        "start_at",
        "end_at",
        "timezone",
        "source_type",
        "source_name",
        "source_identifier",
        "external_sample_id",
        "local_date",
    }
)


@dataclass(frozen=True, slots=True)
class WithingsDomainResult:
    status: str
    fetched_count: int
    persisted_count: int
    requested_start: date
    requested_end: date
    covered_start: date | None = None
    covered_end: date | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class WithingsSyncResult:
    status: str
    weight: WithingsDomainResult
    activity_energy: WithingsDomainResult
    provider_attempted: bool = True


@dataclass(frozen=True, slots=True)
class WithingsActivityIngestionResult:
    source_type: str
    batch_id: UUID
    received: int
    inserted: int
    updated: int
    skipped: int

    @property
    def persisted_count(self) -> int:
        return self.inserted + self.updated


class _PageClient(Protocol):
    def fetch_measure_pages(
        self, *, access_token: str, startdate: int, enddate: int
    ) -> WithingsPageResult: ...

    def fetch_activity_pages(
        self, *, access_token: str, startdateymd: date, enddateymd: date
    ) -> WithingsPageResult: ...

    def close(self) -> None: ...


SessionFactory = Callable[[], Session]
ClientFactory = Callable[[str], _PageClient]


def _owned_connection(db: Session, user_id: UUID) -> tuple[User | None, WithingsConnection | None]:
    user = db.scalar(select(User).where(User.id == user_id))
    connection = db.scalar(select(WithingsConnection).where(WithingsConnection.user_id == user_id))
    return user, connection


def _timezone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("user timezone is invalid") from exc


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _unix_day_range(start: date, end: date, timezone: ZoneInfo) -> tuple[int, int]:
    start_at = datetime.combine(start - timedelta(days=2), time.min, tzinfo=timezone).astimezone(UTC)
    end_at = datetime.combine(end + timedelta(days=3), time.min, tzinfo=timezone).astimezone(UTC)
    return int(start_at.timestamp()), int(end_at.timestamp())


def _covered(values: Iterable[date]) -> tuple[date | None, date | None]:
    dates = sorted(set(values))
    return (dates[0], dates[-1]) if dates else (None, None)


def _measure_group_local_date(group: WithingsMeasureGroup, fallback_timezone: str) -> date:
    if group.timezone is None:
        return local_date_for(group.measured_at, fallback_timezone)
    local_date = group.local_date
    if local_date is None:
        raise ValueError("Withings measurement local date is unavailable")
    return local_date


def _safe_error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _SAFE_ERROR_CODES:
        return code
    if isinstance(exc, CredentialEncryptionError):
        return "credential_unavailable"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transient_error"
    if isinstance(exc, WithingsClientError):
        return "invalid_response"
    return "persistence_error" if isinstance(exc, (ValueError, TypeError)) else "provider_error"


def _safe_activity_failure_details(exc: Exception) -> tuple[str, ...]:
    field_path = "unavailable"
    validation_rule = "unavailable"
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        if errors:
            location = errors[0].get("loc", ())
            safe_location = tuple(part for part in location if isinstance(part, str))
            if (
                location
                and len(safe_location) == len(location)
                and all(part in _SAFE_ACTIVITY_FIELDS for part in safe_location)
            ):
                field_path = ".".join(safe_location)
            rule = errors[0].get("type")
            if isinstance(rule, str) and _SAFE_IDENTIFIER.fullmatch(rule):
                validation_rule = rule

    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    table_name = getattr(diagnostic, "table_name", None)
    column_name = getattr(diagnostic, "column_name", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(diagnostic, "sqlstate", None)

    def identifier(value: object) -> str:
        return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else "unavailable"

    safe_constraint = identifier(constraint_name)
    safe_table = identifier(table_name)
    safe_column = identifier(column_name)
    safe_sqlstate = sqlstate if isinstance(sqlstate, str) and _SAFE_SQLSTATE.fullmatch(sqlstate) else "unavailable"

    if isinstance(exc, ValidationError):
        safe_message = "canonical_validation_failed"
    elif isinstance(exc, DBAPIError) or any(
        value != "unavailable"
        for value in (safe_constraint, safe_table, safe_column, safe_sqlstate)
    ):
        safe_message = "database_operation_failed"
    elif isinstance(exc, TypeError):
        safe_message = "type_validation_failed"
    elif isinstance(exc, ValueError):
        safe_message = "value_validation_failed"
    else:
        safe_message = "activity_operation_failed"

    return (
        type(exc).__name__,
        safe_message,
        field_path,
        validation_rule,
        safe_constraint,
        safe_table,
        safe_column,
        safe_sqlstate,
    )


def _log_activity_failure(stage: str, exc: Exception) -> None:
    (
        exception_type,
        safe_message,
        field_path,
        validation_rule,
        constraint_name,
        table_name,
        column_name,
        sqlstate,
    ) = _safe_activity_failure_details(exc)
    LOGGER.warning(
        "withings.activity.sync_failed stage=%s error_code=%s exception_type=%s "
        "safe_message=%s field_path=%s validation_rule=%s constraint_name=%s "
        "table_name=%s column_name=%s sqlstate=%s",
        stage,
        _safe_error_code(exc),
        exception_type,
        safe_message,
        field_path,
        validation_rule,
        constraint_name,
        table_name,
        column_name,
        sqlstate,
    )


def _result(
    *,
    status: str,
    fetched: int,
    persisted: int,
    requested_start: date,
    requested_end: date,
    covered: tuple[date | None, date | None] = (None, None),
    error: str | None = None,
) -> WithingsDomainResult:
    return WithingsDomainResult(
        status=status,
        fetched_count=fetched,
        persisted_count=persisted,
        requested_start=requested_start,
        requested_end=requested_end,
        covered_start=covered[0],
        covered_end=covered[1],
        error_code=error,
    )




_ACTIVITY_SOURCE_QUANTUM = Decimal("0.000000000001")
_ACTIVITY_CANONICAL_QUANTUM = Decimal("0.000001")


def _project_activity_calories(calories: Decimal) -> tuple[Decimal, Decimal]:
    if calories >= ORIGINAL_VALUE_LIMIT:
        # Let CanonicalSample reject out-of-range values without quantizing them.
        return calories, calories

    precision = max(38, len(calories.as_tuple().digits) + 24)
    with localcontext() as context:
        context.prec = precision
        try:
            source_value = calories.quantize(
                _ACTIVITY_SOURCE_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
            canonical_value = calories.quantize(
                _ACTIVITY_CANONICAL_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
        except InvalidOperation as exc:
            raise ValueError("Withings activity calories cannot be normalized") from exc
    return source_value, canonical_value


def _activity_sample(
    activity: WithingsActivity, *, timezone: str, source_identifier: str
) -> CanonicalSample:
    if not isinstance(activity, WithingsActivity):
        raise ValueError("Withings activity DTO is invalid")
    if not isinstance(activity.civil_date, date) or isinstance(activity.civil_date, datetime):
        raise ValueError("Withings activity date is invalid")
    calories = activity.calories
    if not isinstance(calories, Decimal) or not calories.is_finite() or calories < 0:
        raise ValueError("Withings activity calories are invalid")
    source_value, canonical_value = _project_activity_calories(calories)
    anchor = datetime.combine(
        activity.civil_date, time(hour=12), tzinfo=ZoneInfo(timezone)
    ).astimezone(UTC)
    return CanonicalSample(
        metric_type=ACTIVE_ENERGY_METRIC,
        value=canonical_value,
        unit="kcal",
        original_value=source_value,
        original_unit="kcal",
        start_at=anchor,
        end_at=anchor,
        timezone=timezone,
        source_type=WITHINGS_ACTIVITY_SOURCE_TYPE,
        source_name="Withings",
        source_identifier=source_identifier,
        external_sample_id=activity.civil_date.isoformat(),
        local_date=activity.civil_date,
    )


def ingest_withings_activity(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    activities: Iterable[WithingsActivity],
) -> WithingsActivityIngestionResult:
    """Persist official daily Withings calories as one idempotent sample per day."""
    stage = "validate_ownership"
    try:
        user, connection = _owned_connection(db, user_id)
        if user is None or connection is None or connection.id != source_instance_id:
            raise ValueError("source connection is outside the requested user scope")
        timezone = user.timezone
        # One provider civil day is one canonical row. If a malformed provider response
        # repeats a date, retaining the final DTO is deterministic and never sums rows.
        stage = "validate_activity_dtos"
        by_day: dict[date, WithingsActivity] = {}
        for activity in activities:
            if not isinstance(activity, WithingsActivity):
                raise ValueError("Withings activity DTO is invalid")
            by_day[activity.civil_date] = activity
        stage = "canonical_sample_validation"
        samples = [
            _activity_sample(activity, timezone=timezone, source_identifier=str(connection.id))
            for _, activity in sorted(by_day.items())
        ]
        stage = "create_import_batch"
        batch: ImportBatch = _start_batch(
            db,
            user,
            WITHINGS_ACTIVITY_SOURCE_TYPE,
            str(connection.id),
            connector_variant="withings-activity-v2",
            commit=False,
            log_started=False,
        )
        stage = "sample_persistence"
        inserted, updated, skipped = _persist_sample_batch(db, user, batch, samples)
        batch.received = len(samples)
        batch.inserted = inserted
        batch.updated = updated
        batch.skipped = skipped
        batch.status = "completed"
        stage = "batch_flush"
        db.flush()
    except Exception as exc:
        _log_activity_failure(stage, exc)
        raise
    return WithingsActivityIngestionResult(
        source_type=WITHINGS_ACTIVITY_SOURCE_TYPE,
        batch_id=batch.id,
        received=len(samples),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
    )


def _empty_result(start: date, end: date, error: str) -> WithingsSyncResult:
    item = _result(
        status="failed",
        fetched=0,
        persisted=0,
        requested_start=start,
        requested_end=end,
        error=error,
    )
    return WithingsSyncResult(
        status="failed", weight=item, activity_energy=item, provider_attempted=False
    )


class WithingsSyncService:
    """Fetch and commit each Withings domain independently."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if session_factory is None:
            from app.database import SessionLocal

            session_factory = SessionLocal
        self.session_factory = session_factory
        self.client_factory = client_factory or (lambda token: WithingsHTTPClient())

    def _connection_status(
        self,
        db: Session,
        connection: WithingsConnection,
        *,
        error_code: str | None = None,
        success: bool = False,
        activity_attempted: bool = False,
        activity_error_code: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        connection.last_attempt_at = now
        if success:
            connection.last_success_at = now
            connection.last_error = None
            connection.last_error_category = None
            connection.state = "active"
        elif error_code:
            connection.last_error = error_code
            connection.last_error_category = error_code
            if error_code in _REAUTH_ERROR_CODES:
                connection.state = "reauth_required"
        if activity_attempted:
            connection.last_activity_error_category = activity_error_code
        db.commit()

    def sync(
        self,
        *,
        user_id: UUID,
        requested_start: date,
        requested_end: date,
        db: Session | None = None,
        _operation_locked: bool = False,
    ) -> WithingsSyncResult:
        if requested_start > requested_end:
            raise ValueError("requested date range is invalid")
        owns_session = db is None
        session = db or self.session_factory()
        client: _PageClient | None = None
        if not _operation_locked:
            try:
                with exclusive_user_lifecycle_operation(session, user_id):
                    return self.sync(
                        user_id=user_id,
                        requested_start=requested_start,
                        requested_end=requested_end,
                        db=session,
                        _operation_locked=True,
                    )
            finally:
                if owns_session:
                    session.close()
        try:
            user, connection = _owned_connection(session, user_id)
            if user is None or connection is None:
                return _empty_result(requested_start, requested_end, "not_connected")
            if connection.state != "active":
                error = (
                    "reauth_required" if connection.state == "reauth_required" else "not_connected"
                )
                return _empty_result(requested_start, requested_end, error)
            if not connection.encrypted_access_token:
                return _empty_result(requested_start, requested_end, "not_connected")
            if not WITHINGS_REQUIRED_SCOPES.issubset(set(connection.granted_scopes or ())):
                return _empty_result(requested_start, requested_end, "scope_missing")
            try:
                if connection.access_token_expires_at is not None and _utc(
                    connection.access_token_expires_at
                ) <= datetime.now(UTC):
                    # The route or service recursion already holds the exclusive user lock.
                    connection = refresh_withings_tokens_default(session, user, lock=False)
                encrypted_access_token = connection.encrypted_access_token
                if not encrypted_access_token:
                    return _empty_result(requested_start, requested_end, "not_connected")
                access_token = decrypt_token(encrypted_access_token)
                timezone = _timezone(user)
            except Exception as exc:
                error = _safe_error_code(exc)
                self._connection_status(session, connection, error_code=error)
                return _empty_result(requested_start, requested_end, error)
            try:
                client = self.client_factory(access_token)
                startdate, enddate = _unix_day_range(requested_start, requested_end, timezone)
            except Exception as exc:
                error = _safe_error_code(exc)
                self._connection_status(session, connection, error_code=error)
                return _empty_result(requested_start, requested_end, error)

            weight_result = self._sync_weight(
                session,
                user_id=user.id,
                source_instance_id=connection.id,
                client=client,
                access_token=access_token,
                startdate=startdate,
                enddate=enddate,
                requested_start=requested_start,
                requested_end=requested_end,
                timezone=user.timezone,
            )
            activity_result = self._sync_activity(
                session,
                user_id=user.id,
                source_instance_id=connection.id,
                client=client,
                access_token=access_token,
                timezone=user.timezone,
                requested_start=requested_start,
                requested_end=requested_end,
            )
            statuses = (weight_result.status, activity_result.status)
            errors = [
                item.error_code for item in (weight_result, activity_result) if item.error_code
            ]
            activity_error_code = (
                activity_result.error_code if activity_result.status == "failed" else None
            )
            if "failed" in statuses:
                overall = (
                    "partial_failure"
                    if "success" in statuses or "no_data" in statuses
                    else "failed"
                )
                self._connection_status(
                    session,
                    connection,
                    error_code=errors[0] if errors else "provider_error",
                    activity_attempted=True,
                    activity_error_code=activity_error_code,
                )
            elif "success" in statuses:
                overall = "success"
                self._connection_status(
                    session,
                    connection,
                    success=True,
                    activity_attempted=True,
                    activity_error_code=activity_error_code,
                )
            else:
                overall = "no_data"
                self._connection_status(
                    session,
                    connection,
                    success=True,
                    activity_attempted=True,
                    activity_error_code=activity_error_code,
                )
            return WithingsSyncResult(overall, weight_result, activity_result)
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()
            if owns_session:
                session.close()

    def _sync_weight(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        client: _PageClient,
        access_token: str,
        startdate: int,
        enddate: int,
        requested_start: date,
        requested_end: date,
        timezone: str,
    ) -> WithingsDomainResult:
        try:
            fetched: WithingsPageResult = client.fetch_measure_pages(
                access_token=access_token, startdate=startdate, enddate=enddate
            )
            all_groups = [group for page in fetched.measure_pages for group in page.groups]
            localized_groups = [
                (group, _measure_group_local_date(group, timezone))
                for group in all_groups
            ]
            selected_groups = [
                (group, local_date)
                for group, local_date in localized_groups
                if requested_start <= local_date <= requested_end
            ]
            groups = [group for group, _local_date in selected_groups]
            covered = _covered(local_date for _group, local_date in selected_groups)
            result = ingest_withings_measurements(
                db,
                user_id=user_id,
                source_instance_id=source_instance_id,
                groups=groups,
            )
            db.commit()
            status = "success" if groups else "no_data"
            return _result(
                status=status,
                fetched=len(all_groups),
                persisted=result.persisted_count,
                requested_start=requested_start,
                requested_end=requested_end,
                covered=covered,
            )
        except Exception as exc:
            if isinstance(exc, WithingsInvalidResponseError) and exc.diagnostic is not None:
                diagnostic = exc.diagnostic
                LOGGER.warning(
                    "withings.measure.parser_rejected "
                    "stage=%s field_path=%s rule=%s reason=%s "
                    "observed_json_type=%s expected_json_type=%s "
                    "group_index=%s measurement_index=%s presence=%s",
                    diagnostic.parser_stage,
                    diagnostic.field_path,
                    diagnostic.validation_rule,
                    diagnostic.structural_reason,
                    diagnostic.observed_json_type,
                    diagnostic.expected_json_type,
                    diagnostic.group_index,
                    diagnostic.measurement_index,
                    diagnostic.presence,
                )
            db.rollback()
            return _result(
                status="failed",
                fetched=0,
                persisted=0,
                requested_start=requested_start,
                requested_end=requested_end,
                error=_safe_error_code(exc),
            )

    def _sync_activity(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        client: _PageClient,
        access_token: str,
        timezone: str,
        requested_start: date,
        requested_end: date,
    ) -> WithingsDomainResult:
        fetched_count = 0
        stage = "fetch_activity_pages"
        try:
            fetched: WithingsPageResult = client.fetch_activity_pages(
                access_token=access_token,
                startdateymd=requested_start,
                enddateymd=requested_end,
            )
            all_activities = [
                activity for page in fetched.activity_pages for activity in page.activities
            ]
            fetched_count = len(all_activities)
            stage = "filter_activity_range"
            activities = [
                activity
                for activity in all_activities
                if requested_start <= activity.civil_date <= requested_end
            ]
            covered = _covered(activity.civil_date for activity in activities)
            stage = "activity_ingestion"
            result = ingest_withings_activity(
                db,
                user_id=user_id,
                source_instance_id=source_instance_id,
                activities=activities,
            )
            stage = "commit_activity"
            db.commit()
            status = "success" if activities else "no_data"
            return _result(
                status=status,
                fetched=fetched_count,
                persisted=result.persisted_count,
                requested_start=requested_start,
                requested_end=requested_end,
                covered=covered,
            )
        except Exception as exc:
            if stage != "activity_ingestion":
                _log_activity_failure(stage, exc)
            db.rollback()
            return _result(
                status="failed",
                fetched=fetched_count,
                persisted=0,
                requested_start=requested_start,
                requested_end=requested_end,
                error=_safe_error_code(exc),
            )


def sync_withings(
    db: Session,
    *,
    user_id: UUID,
    requested_start: date,
    requested_end: date,
) -> WithingsSyncResult:
    return WithingsSyncService(session_factory=lambda: db).sync(
        user_id=user_id,
        requested_start=requested_start,
        requested_end=requested_end,
        db=db,
    )


__all__ = [
    "WithingsActivityIngestionResult",
    "WithingsDomainResult",
    "WithingsSyncResult",
    "WithingsSyncService",
    "ingest_withings_activity",
    "sync_withings",
]
