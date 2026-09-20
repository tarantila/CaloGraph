"""Bounded Google Health v4 Activity and Weight ingestion."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE
from app.google_health.client import (
    GOOGLE_HEALTH_MAX_PAGE_SIZE,
    ActiveEnergyBurnedDataPoint,
    GoogleHealthActivityDataPoint,
    GoogleHealthDataPointPage,
    WeightDataPoint,
)
from app.importers.common import CanonicalSample, local_date_for, normalize_value
from app.models import GoogleHealthConnection, ImportBatch, User
from app.services.import_service import _persist_sample_batch, _start_batch
from app.weight import WEIGHT_METRIC, GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE

ACTIVITY_SOURCE_TYPE = GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE
WEIGHT_SOURCE_TYPE = GOOGLE_HEALTH_WEIGHT_SOURCE_TYPE
_SOURCE_NAME = "Google Health"
_DEFAULT_CONNECTOR = "google-health-api-v4"
_MASS_UNIT_ALIASES = {
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
}


@dataclass(frozen=True, slots=True)
class GoogleHealthScalarSyncResult:
    source_type: str
    batch_id: UUID
    received: int
    inserted: int
    updated: int
    skipped: int

    @property
    def persisted_count(self) -> int:
        return self.inserted + self.updated
 
 
class _PagedClient(Protocol):
    def iter_data_points_pages(
        self,
        data_type: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
        page_token: str | None = None,
        max_pages: int = 100,
    ) -> Iterable[GoogleHealthDataPointPage]: ...


SessionFactory = Callable[[], Session]
ClientFactory = Callable[[object], _PagedClient]
CredentialsFactory = Callable[[str], object]
DecryptRefreshToken = Callable[[bytes], str]


def _validate_request(requested_start: date, requested_end: date) -> None:
    if (
        not isinstance(requested_start, date)
        or isinstance(requested_start, datetime)
        or not isinstance(requested_end, date)
        or isinstance(requested_end, datetime)
    ):
        raise ValueError("requested date range is invalid")
    if requested_start > requested_end:
        raise ValueError("requested_start must not be after requested_end")


def _owned_user_and_connection(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
) -> tuple[User, GoogleHealthConnection]:
    user = db.scalar(select(User).where(User.id == user_id))
    connection = db.scalar(
        select(GoogleHealthConnection).where(
            GoogleHealthConnection.id == source_instance_id,
            GoogleHealthConnection.user_id == user_id,
        )
    )
    if user is None or connection is None:
        raise ValueError("source connection is outside the requested user scope")
    return user, connection


def _point_identity(point: object) -> str:
    name = getattr(point, "name", None)
    if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 255:
        raise ValueError("Google Health datapoint identity is invalid")
    return name


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Google Health datapoint value is invalid") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Google Health datapoint value is invalid")
    return result


def _timezone(user: User) -> str:
    timezone = user.timezone
    if not isinstance(timezone, str) or not timezone:
        raise ValueError("user timezone is invalid")
    return timezone


def _sample_for_activity(
    point: GoogleHealthActivityDataPoint,
    *,
    timezone: str,
    source_identifier: str,
) -> CanonicalSample:
    if not isinstance(point, ActiveEnergyBurnedDataPoint):
        raise ValueError("activity data_points contain an invalid DTO")
    identity = _point_identity(point)
    if point.start_time.tzinfo is None or point.end_time.tzinfo is None:
        raise ValueError("Google Health datapoint timestamp is invalid")
    if point.start_time >= point.end_time:
        raise ValueError("Google Health activity interval is invalid")
    original_value = _decimal(point.value)
    original_unit = point.unit
    if not isinstance(original_unit, str) or not original_unit or len(original_unit) > 64:
        raise ValueError("Google Health datapoint unit is invalid")
    if original_unit != "kcal":
        raise ValueError("Google Health datapoint unit is invalid")
    try:
        canonical_value = normalize_value(original_value, original_unit, "kcal")
    except Exception as exc:
        raise ValueError("Google Health datapoint unit is invalid") from exc
    return CanonicalSample(
        metric_type=ACTIVE_ENERGY_METRIC,
        value=canonical_value,
        unit="kcal",
        original_value=original_value,
        original_unit=original_unit,
        start_at=point.start_time,
        end_at=point.end_time,
        timezone=timezone,
        source_type=ACTIVITY_SOURCE_TYPE,
        source_name=_SOURCE_NAME,
        source_identifier=source_identifier,
        external_sample_id=identity,
    )


def _sample_for_weight(
    point: WeightDataPoint,
    *,
    timezone: str,
    source_identifier: str,
) -> CanonicalSample:
    if not isinstance(point, WeightDataPoint):
        raise ValueError("weight data_points contain an invalid DTO")
    identity = _point_identity(point)
    if point.start_time.tzinfo is None or point.end_time.tzinfo is None:
        raise ValueError("Google Health datapoint timestamp is invalid")
    if point.start_time != point.end_time:
        raise ValueError("Google Health weight interval is invalid")
    original_value = _decimal(point.value)
    original_unit = point.unit
    if not isinstance(original_unit, str) or not original_unit or len(original_unit) > 64:
        raise ValueError("Google Health datapoint unit is invalid")
    try:
        normalized_unit = _MASS_UNIT_ALIASES[original_unit.strip().lower()]
        canonical_value = normalize_value(original_value, normalized_unit, "kg")
    except Exception as exc:
        raise ValueError("Google Health datapoint unit is invalid") from exc
    return CanonicalSample(
        metric_type=WEIGHT_METRIC,
        value=canonical_value,
        unit="kg",
        original_value=original_value,
        original_unit=original_unit,
        start_at=point.start_time,
        end_at=point.end_time,
        timezone=timezone,
        source_type=WEIGHT_SOURCE_TYPE,
        source_name=_SOURCE_NAME,
        source_identifier=source_identifier,
        external_sample_id=identity,
    )


def _persist_scalar_samples(
    db: Session,
    *,
    user: User,
    source_type: str,
    samples: list[CanonicalSample],
) -> GoogleHealthScalarSyncResult:
    batch: ImportBatch = _start_batch(
        db,
        user,
        source_type,
        str(user.id),
        connector_variant=_DEFAULT_CONNECTOR,
        commit=False,
        log_started=False,
    )
    inserted, updated, skipped = _persist_sample_batch(db, user, batch, samples)
    batch.received = len(samples)
    batch.inserted = inserted
    batch.updated = updated
    batch.skipped = skipped
    batch.status = "completed"
    db.flush()
    return GoogleHealthScalarSyncResult(
        source_type=source_type,
        batch_id=batch.id,
        received=len(samples),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
    )


def sync_google_health_activity(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    requested_start: date,
    requested_end: date,
    data_points: tuple[GoogleHealthActivityDataPoint, ...] | list[GoogleHealthActivityDataPoint],
) -> GoogleHealthScalarSyncResult:
    """Validate and persist already-fetched Google Activity DTOs."""
    _validate_request(requested_start, requested_end)
    user, _ = _owned_user_and_connection(
        db, user_id=user_id, source_instance_id=source_instance_id
    )
    timezone = _timezone(user)
    if not isinstance(data_points, (tuple, list)):
        raise ValueError("data_points must be a tuple or list")
    samples = [
        _sample_for_activity(point, timezone=timezone, source_identifier=str(source_instance_id))
        for point in data_points
    ]
    for sample in samples:
        local_date = local_date_for(sample.start_at, timezone)
        if not requested_start <= local_date <= requested_end:
            raise ValueError("Google Health datapoint is outside the requested date range")
    return _persist_scalar_samples(
        db,
        user=user,
        source_type=ACTIVITY_SOURCE_TYPE,
        samples=samples,
    )


def sync_google_health_weight(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    requested_start: date,
    requested_end: date,
    data_points: tuple[WeightDataPoint, ...] | list[WeightDataPoint],
) -> GoogleHealthScalarSyncResult:
    """Validate and persist already-fetched Google Weight DTOs."""
    _validate_request(requested_start, requested_end)
    user, _ = _owned_user_and_connection(
        db, user_id=user_id, source_instance_id=source_instance_id
    )
    timezone = _timezone(user)
    if not isinstance(data_points, (tuple, list)):
        raise ValueError("data_points must be a tuple or list")
    samples = [
        _sample_for_weight(point, timezone=timezone, source_identifier=str(source_instance_id))
        for point in data_points
    ]
    for sample in samples:
        local_date = local_date_for(sample.start_at, timezone)
        if not requested_start <= local_date <= requested_end:
            raise ValueError("Google Health datapoint is outside the requested date range")
    return _persist_scalar_samples(
        db,
        user=user,
        source_type=WEIGHT_SOURCE_TYPE,
        samples=samples,
    )


# Explicit ingestion aliases make the persistence boundary easy to reuse from
# a network orchestrator without changing the stable Task 3 interface.
ingest_google_health_activity = sync_google_health_activity
ingest_google_health_weight = sync_google_health_weight


class GoogleHealthScalarSyncService:
    """Fetch bounded Activity/Weight pages, then persist one owned transaction."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        client_factory: ClientFactory,
        credentials_factory: CredentialsFactory,
        decrypt_refresh_token: DecryptRefreshToken,
        page_size: int = GOOGLE_HEALTH_MAX_PAGE_SIZE,
        max_pages: int = 100,
    ) -> None:
        if not isinstance(page_size, int) or page_size <= 0:
            raise ValueError("page_size must be positive")
        if not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self._session_factory = session_factory
        self._client_factory = client_factory
        self._credentials_factory = credentials_factory
        self._decrypt_refresh_token = decrypt_refresh_token
        self._page_size = page_size
        self._max_pages = max_pages

    def _fetch(
        self,
        *,
        user_id: UUID,
        requested_start: date,
        requested_end: date,
        data_type: str,
    ) -> tuple[UUID, tuple[GoogleHealthDataPointPage, ...]]:
        _validate_request(requested_start, requested_end)
        db = self._session_factory()
        try:
            user = db.scalar(select(User).where(User.id == user_id))
            connection = db.scalar(
                select(GoogleHealthConnection).where(
                    GoogleHealthConnection.user_id == user_id,
                )
            )
            if (
                user is None
                or connection is None
                or connection.state != "active"
                or not isinstance(user.timezone, str)
            ):
                raise ValueError("Google Health connection is unavailable")
            try:
                timezone = ZoneInfo(user.timezone)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError("user timezone is invalid") from None
            source_instance_id = connection.id
            refresh_token = self._decrypt_refresh_token(connection.encrypted_refresh_token)
        finally:
            db.close()
        credentials = self._credentials_factory(refresh_token)
        client = self._client_factory(credentials)
        try:
            local_start = datetime.combine(
                requested_start, datetime.min.time(), tzinfo=timezone
            )
            local_end = datetime.combine(
                requested_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone
            )
            pages = tuple(
                client.iter_data_points_pages(
                    data_type,
                    start_time=local_start.astimezone(UTC),
                    end_time=local_end.astimezone(UTC),
                    page_size=self._page_size,
                    max_pages=self._max_pages,
                )
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        return source_instance_id, pages

    def sync_activity(
        self, *, user_id: UUID, requested_start: date, requested_end: date
    ) -> GoogleHealthScalarSyncResult:
        source_instance_id, pages = self._fetch(
            user_id=user_id,
            requested_start=requested_start,
            requested_end=requested_end,
            data_type="active-energy-burned",
        )
        points = tuple(point for page in pages for point in page.data_points)
        db = self._session_factory()
        try:
            result = sync_google_health_activity(
                db,
                user_id=user_id,
                source_instance_id=source_instance_id,
                requested_start=requested_start,
                requested_end=requested_end,
                data_points=points,  # type: ignore[arg-type]
            )
            db.commit()
            return result
        finally:
            db.close()

    def sync_weight(
        self, *, user_id: UUID, requested_start: date, requested_end: date
    ) -> GoogleHealthScalarSyncResult:
        source_instance_id, pages = self._fetch(
            user_id=user_id,
            requested_start=requested_start,
            requested_end=requested_end,
            data_type="weight",
        )
        points = tuple(point for page in pages for point in page.data_points)
        db = self._session_factory()
        try:
            result = sync_google_health_weight(
                db,
                user_id=user_id,
                source_instance_id=source_instance_id,
                requested_start=requested_start,
                requested_end=requested_end,
                data_points=points,  # type: ignore[arg-type]
            )
            db.commit()
            return result
        finally:
            db.close()


__all__ = [
    "ACTIVITY_SOURCE_TYPE",
    "WEIGHT_SOURCE_TYPE",
    "GoogleHealthScalarSyncResult",
    "GoogleHealthScalarSyncService",
    "ingest_google_health_activity",
    "ingest_google_health_weight",
    "sync_google_health_activity",
    "sync_google_health_weight",
]
