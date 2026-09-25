"""Canonical ingestion for Withings measurement groups."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.common import CanonicalSample, local_date_for
from app.models import ImportBatch, User, WithingsConnection
from app.services.import_service import _persist_sample_batch, _start_batch
from app.withings.constants import WITHINGS_MEASURE_TYPES, WITHINGS_WEIGHT_SOURCE_TYPE
from app.withings.parsers import WithingsMeasure, WithingsMeasureGroup

_SOURCE_NAME = "Withings"
_CONNECTOR_VARIANT = "withings-measure-v1"


@dataclass(frozen=True, slots=True)
class WithingsMeasurementIngestionResult:
    """Bounded counters for one canonical Withings measurement batch."""

    source_type: str
    batch_id: UUID
    received: int
    inserted: int
    updated: int
    skipped: int

    @property
    def persisted_count(self) -> int:
        return self.inserted + self.updated


def _owned_user_and_connection(
    db: Session, *, user_id: UUID, source_instance_id: UUID
) -> tuple[User, WithingsConnection]:
    user = db.scalar(select(User).where(User.id == user_id))
    connection = db.scalar(
        select(WithingsConnection).where(
            WithingsConnection.id == source_instance_id,
            WithingsConnection.user_id == user_id,
        )
    )
    if user is None or connection is None:
        raise ValueError("source connection is outside the requested user scope")
    return user, connection


def _decimal_value(value: object, *, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"Withings measurement {field} is invalid")
    return value


def _sample_for_measure(
    group: WithingsMeasureGroup,
    measure: WithingsMeasure,
    *,
    source_identifier: str,
    fallback_timezone: str,
) -> CanonicalSample | None:
    if not isinstance(measure, WithingsMeasure):
        raise ValueError("Withings measurement shape is invalid")
    mapping = WITHINGS_MEASURE_TYPES.get(measure.measure_type)
    if mapping is None:
        # Parser DTOs already omit these, but keep direct callers bounded too.
        return None
    metric_type, unit = mapping
    if (
        not isinstance(measure, WithingsMeasure)
        or measure.metric_type != metric_type
        or measure.unit != unit
        or not isinstance(measure.exponent, int)
        or isinstance(measure.exponent, bool)
    ):
        raise ValueError("Withings measurement shape is invalid")
    original_value = _decimal_value(measure.original_value, field="original value")
    canonical_value = _decimal_value(measure.value, field="value")
    if not isinstance(group.measured_at, datetime) or group.measured_at.tzinfo is None:
        raise ValueError("Withings measurement timestamp is invalid")
    if group.timezone is None:
        timezone = fallback_timezone
        local_date = local_date_for(group.measured_at, timezone)
    else:
        if not isinstance(group.timezone, str) or not group.timezone:
            raise ValueError("Withings measurement timezone is invalid")
        timezone = "UTC" if group.timezone == "Z" else group.timezone
        provider_local_date = group.local_date
        if provider_local_date is None:
            raise ValueError("Withings measurement local date is unavailable")
        local_date = provider_local_date
    return CanonicalSample(
        metric_type=metric_type,
        value=canonical_value,
        unit=unit,
        original_value=original_value,
        # Withings' provider unit is a decimal exponent, not a physical unit.
        original_unit=str(measure.exponent),
        start_at=group.measured_at,
        end_at=group.measured_at,
        timezone=timezone,
        source_type=WITHINGS_WEIGHT_SOURCE_TYPE,
        source_name=_SOURCE_NAME,
        source_identifier=source_identifier,
        external_sample_id=f"{group.group_id}:{measure.measure_type}",
        local_date=local_date,
    )


def ingest_withings_measurements(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    groups: Iterable[WithingsMeasureGroup],
) -> WithingsMeasurementIngestionResult:
    """Validate and persist parser-produced Withings measurement groups.

    All ownership and value validation happens before creating an import batch,
    so malformed provider data leaves no partial canonical or batch rows.
    """
    user, connection = _owned_user_and_connection(
        db, user_id=user_id, source_instance_id=source_instance_id
    )
    source_identifier = str(connection.id)
    samples: list[CanonicalSample] = []
    for group in groups:
        if not isinstance(group, WithingsMeasureGroup):
            raise ValueError("Withings measurement group is invalid")
        if not isinstance(group.measures, tuple):
            raise ValueError("Withings measurement group measures are invalid")
        for measure in group.measures:
            sample = _sample_for_measure(
                group,
                measure,
                source_identifier=source_identifier,
                fallback_timezone=user.timezone,
            )
            if sample is not None:
                samples.append(sample)

    batch: ImportBatch = _start_batch(
        db,
        user,
        WITHINGS_WEIGHT_SOURCE_TYPE,
        source_identifier,
        connector_variant=_CONNECTOR_VARIANT,
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
    return WithingsMeasurementIngestionResult(
        source_type=WITHINGS_WEIGHT_SOURCE_TYPE,
        batch_id=batch.id,
        received=len(samples),
        inserted=inserted,
        updated=updated,
        skipped=skipped,
    )


# Explicit alias for callers that use the parser's group terminology.
ingest_withings_measurement_groups = ingest_withings_measurements


__all__ = [
    "WithingsMeasurementIngestionResult",
    "ingest_withings_measurement_groups",
    "ingest_withings_measurements",
]
