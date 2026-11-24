"""Source-coherent canonical weight history shared with trusted readers."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.analytics.scalar_selection import resolve_scalar_provider
from app.models import GoogleHealthConnection, HealthSample, WithingsConnection, YazioConnection
from app.provider_preferences import WEIGHT_DATA_AREA
from app.weight import WEIGHT_METRIC, WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS


class WeightHistorySourceUnavailable(RuntimeError):
    """A selected scalar provider no longer has its expected owner source."""


@dataclass(frozen=True, slots=True)
class WeightHistory:
    selected_provider: str | None
    points: tuple[tuple[date, Decimal], ...]
    providers: tuple[tuple[date, str], ...] = ()


def read_weight_history(
    db: Session, *, user_id: UUID, start: date, end: date,
) -> WeightHistory:
    """Read one winner per owner-local day following configured provider priority."""
    selection = resolve_scalar_provider(
        db, user_id=user_id, data_area=WEIGHT_DATA_AREA,
        source_types=WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS,
    )
    if selection is None:
        return WeightHistory(None, ())
    provider_sources = selection.provider_sources or ((selection.provider_key, selection.source_type),)
    filters, provider_by_source = _source_filters(db, user_id=user_id, provider_sources=provider_sources)
    samples = list(db.scalars(
        select(HealthSample).where(
            HealthSample.user_id == user_id,
            HealthSample.metric_type == WEIGHT_METRIC,
            HealthSample.local_date >= start,
            HealthSample.local_date <= end,
            or_(*filters),
        ).order_by(HealthSample.local_date, HealthSample.start_at, HealthSample.id)
    ))
    latest_by_provider_day: dict[tuple[str, date], HealthSample] = {}
    for sample in samples:
        provider_key = provider_by_source.get(sample.source_type)
        if provider_key is None:
            continue
        key = (provider_key, sample.local_date)
        previous = latest_by_provider_day.get(key)
        if previous is None or (sample.start_at, sample.id) > (previous.start_at, previous.id):
            latest_by_provider_day[key] = sample
    latest_by_day: dict[date, HealthSample] = {}
    for provider_key, _ in provider_sources:
        for (sample_provider, sample_day), sample in latest_by_provider_day.items():
            if sample_provider == provider_key and sample_day not in latest_by_day:
                latest_by_day[sample_day] = sample
    return WeightHistory(
        selection.provider_key,
        tuple((day, sample.value) for day, sample in sorted(latest_by_day.items())),
        tuple((day, provider_by_source[sample.source_type]) for day, sample in sorted(latest_by_day.items())),
    )


def weight_history_bounds(db: Session, *, user_id: UUID) -> tuple[date, date] | None:
    selection = resolve_scalar_provider(
        db, user_id=user_id, data_area=WEIGHT_DATA_AREA,
        source_types=WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS,
    )
    if selection is None:
        return None
    provider_sources = selection.provider_sources or ((selection.provider_key, selection.source_type),)
    filters, _ = _source_filters(db, user_id=user_id, provider_sources=provider_sources)
    result = db.execute(
        select(func.min(HealthSample.local_date), func.max(HealthSample.local_date)).where(
            HealthSample.user_id == user_id,
            HealthSample.metric_type == WEIGHT_METRIC,
            or_(*filters),
        )
    ).one()
    return (result[0], result[1]) if result[0] is not None and result[1] is not None else None


def _source_filters(
    db: Session, *, user_id: UUID, provider_sources: tuple[tuple[str, str], ...],
) -> tuple[list[ColumnElement[bool]], dict[str, str]]:
    source_identifier: str | None = None
    if any(provider_key == "yazio" for provider_key, _ in provider_sources):
        yazio_connection = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user_id))
        if yazio_connection is None:
            raise WeightHistorySourceUnavailable("selected YAZIO weight source is unavailable")
        source_identifier = yazio_connection.source_identifier
    google_connection = None
    if any(provider_key == "google_health" for provider_key, _ in provider_sources):
        google_connection = db.scalar(select(GoogleHealthConnection).where(
            GoogleHealthConnection.user_id == user_id,
            GoogleHealthConnection.state == "active",
        ))
        if google_connection is None:
            raise WeightHistorySourceUnavailable("selected Google Health weight source is unavailable")
    google_source_id = str(google_connection.id) if google_connection is not None else None
    withings_connection = None
    if any(provider_key == "withings" for provider_key, _ in provider_sources):
        withings_connection = db.scalar(select(WithingsConnection).where(
            WithingsConnection.user_id == user_id,
            WithingsConnection.state == "active",
        ))
        if withings_connection is None:
            raise WeightHistorySourceUnavailable("selected Withings weight source is unavailable")
    withings_source_id = str(withings_connection.id) if withings_connection is not None else None
    provider_by_source = {source_type: provider_key for provider_key, source_type in provider_sources}
    filters: list[ColumnElement[bool]] = []
    for provider_key, source_type in provider_sources:
        source_filter = [HealthSample.source_type == source_type]
        if provider_key == "yazio":
            source_filter.append(HealthSample.source_identifier == source_identifier)
        elif provider_key == "analysis_demo":
            from app.services.analysis_demo import demo_source_instance_id

            source_filter.append(HealthSample.source_identifier == str(demo_source_instance_id(user_id)))
        elif provider_key == "google_health":
            if google_source_id is None:
                raise WeightHistorySourceUnavailable("selected Google Health weight source is unavailable")
            source_filter.append(HealthSample.source_identifier == google_source_id)
        elif provider_key == "withings":
            if withings_source_id is None:
                raise WeightHistorySourceUnavailable("selected Withings weight source is unavailable")
            source_filter.extend((HealthSample.source_identifier == withings_source_id, HealthSample.value > 0))
        filters.append(and_(*source_filter))
    return filters, provider_by_source
