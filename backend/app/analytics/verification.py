from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
from app.analytics.scalar_selection import (
    ScalarProviderNotReady,
    ScalarProviderSelection,
    resolve_scalar_provider,
)
from app.models import HealthSample
from app.provider_preferences import ACTIVITY_ENERGY_DATA_AREA
from app.schemas import (
    VerificationActivityDayResponse,
    VerificationActivityProviderRecord,
    VerificationActivityResponse,
    VerificationView,
)
from app.services.provider_preferences import resolve_provider_source_type

_PROVIDER_KEYS = tuple(ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS)
_KNOWN_SOURCE_TYPES = tuple(
    source_type
    for source_types in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS.values()
    for source_type in source_types
)


def _source_types_for_samples(
    provider_key: str, samples: list[HealthSample]
) -> list[str]:
    observed = {sample.source_type for sample in samples}
    return [
        source_type
        for source_type in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS[provider_key]
        if source_type in observed
    ]


def _resolve_apple_transport(db: Session, user_id: UUID) -> str | None:
    try:
        return resolve_provider_source_type(
            db,
            user_id=user_id,
            data_area=ACTIVITY_ENERGY_DATA_AREA,
            provider_key="apple_health",
            configured=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"],
        )
    except ValueError:
        return None


def _canonical_selection(
    db: Session, user_id: UUID
) -> tuple[ScalarProviderSelection | None, str | None]:
    try:
        selection = resolve_scalar_provider(
            db,
            user_id=user_id,
            data_area=ACTIVITY_ENERGY_DATA_AREA,
            source_types=ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS,
        )
    except ScalarProviderNotReady:
        if _resolve_apple_transport(db, user_id) is not None:
            raise
        source_types = dict(ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS)
        source_types["apple_health"] = (ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["apple_health"][0],)
        return (
            resolve_scalar_provider(
                db,
                user_id=user_id,
                data_area=ACTIVITY_ENERGY_DATA_AREA,
                source_types=source_types,
            ),
            None,
        )
    return (
        selection,
        (
            selection.source_type
            if selection is not None and selection.provider_key == "apple_health"
            else None
        ),
    )


def _provider_record(
    *,
    provider_key: str,
    samples: list[HealthSample],
    apple_source_type: str | None = None,
) -> VerificationActivityProviderRecord:
    if not samples:
        return VerificationActivityProviderRecord(
            provider_key=provider_key,
            status="no_data",
            active_energy_kcal=None,
            record_count=0,
            source_types=[],
        )

    if provider_key == "apple_health":
        if apple_source_type is None:
            return VerificationActivityProviderRecord(
                provider_key=provider_key,
                status="unavailable",
                active_energy_kcal=None,
                record_count=len(samples),
                source_types=_source_types_for_samples(provider_key, samples),
            )
        samples = [sample for sample in samples if sample.source_type == apple_source_type]
        if not samples:
            return VerificationActivityProviderRecord(
                provider_key=provider_key,
                status="no_data",
                active_energy_kcal=None,
                record_count=0,
                source_types=[],
            )

    if provider_key == "google_health":
        active_energy_kcal = samples[0].value
    else:
        active_energy_kcal = sum((sample.value for sample in samples), Decimal())
    return VerificationActivityProviderRecord(
        provider_key=provider_key,
        status="available",
        active_energy_kcal=float(active_energy_kcal),
        record_count=len(samples),
        source_types=_source_types_for_samples(provider_key, samples),
    )


def read_activity_verification(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    view: VerificationView,
) -> VerificationActivityResponse:
    """Read user-scoped activity evidence without blending provider totals."""
    selection: ScalarProviderSelection | None = None
    apple_source_type: str | None = None
    if view == "canonical":
        selection, apple_source_type = _canonical_selection(db, user_id)
    else:
        apple_source_type = _resolve_apple_transport(db, user_id)

    samples_by_provider_day: defaultdict[tuple[str, date], list[HealthSample]] = defaultdict(list)
    samples = db.scalars(
        select(HealthSample)
        .where(
            HealthSample.user_id == user_id,
            HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            HealthSample.source_type.in_(_KNOWN_SOURCE_TYPES),
        )
        .order_by(HealthSample.local_date, HealthSample.start_at, HealthSample.id)
    )
    source_to_provider = {
        source_type: provider_key
        for provider_key, source_types in ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS.items()
        for source_type in source_types
    }
    for sample in samples:
        samples_by_provider_day[(source_to_provider[sample.source_type], sample.local_date)].append(sample)

    days: list[VerificationActivityDayResponse] = []
    for offset in range((end - start).days + 1):
        local_date = start + timedelta(days=offset)
        if view == "canonical":
            canonical = (
                None
                if selection is None
                else _provider_record(
                    provider_key=selection.provider_key,
                    samples=samples_by_provider_day[(selection.provider_key, local_date)],
                    apple_source_type=apple_source_type,
                )
            )
            days.append(
                VerificationActivityDayResponse(
                    date=local_date,
                    canonical=canonical,
                    providers=[],
                )
            )
            continue
        days.append(
            VerificationActivityDayResponse(
                date=local_date,
                canonical=None,
                providers=[
                    _provider_record(
                        provider_key=provider_key,
                        samples=samples_by_provider_day[(provider_key, local_date)],
                        apple_source_type=apple_source_type,
                    )
                    for provider_key in _PROVIDER_KEYS
                ],
            )
        )

    return VerificationActivityResponse(
        start_date=start,
        end_date=end,
        view=view,
        days=days,
    )
