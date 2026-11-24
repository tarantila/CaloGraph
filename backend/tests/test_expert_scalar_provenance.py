"""Context retains scalar source changes and excludes retired source instances."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.analytics.scalar_selection import ScalarProviderSelection
from app.analytics.weight_history import read_weight_history
from app.expert.context import ContextBuilder
from app.expert.preferences_contracts import ExpertPreferencesView
from app.models import GoogleHealthConnection, HealthSample, ImportBatch, User, YazioConnection


def sample(db, owner, day, source_type, source_identifier, metric, value, *, hour=12):
    batch = ImportBatch(user_id=owner.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    timestamp = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    identifier = uuid4()
    db.add(HealthSample(
        user_id=owner.id, import_batch_id=batch.id, external_sample_id=str(identifier),
        fingerprint=identifier.hex, source_type=source_type, source_identifier=source_identifier,
        metric_type=metric, value=Decimal(value), original_value=Decimal(value),
        unit="kg" if metric == "weight_kg" else "kcal", original_unit="kg" if metric == "weight_kg" else "kcal",
        start_at=timestamp, end_at=timestamp, local_date=day, timezone="UTC",
    ))
    db.flush()


def test_weight_fallback_provenance_reaches_daily_and_aggregate_context(db: Session, user: User, monkeypatch):
    today = datetime.now(UTC).date()
    first = today - timedelta(days=1)
    connection = GoogleHealthConnection(user_id=user.id, state="active")
    db.add(connection)
    db.flush()
    selected = ScalarProviderSelection("weight", "apple_health", "apple_health_xml", (
        ("apple_health", "apple_health_xml"), ("google_health", "google_health_weight_v4"),
    ))
    monkeypatch.setattr("app.analytics.weight_history.resolve_scalar_provider", lambda *a, **k: selected)
    sample(db, user, first, "apple_health_xml", "synthetic-device", "weight_kg", "80")
    sample(db, user, first, "google_health_weight_v4", str(connection.id), "weight_kg", "100")
    sample(db, user, today, "google_health_weight_v4", str(connection.id), "weight_kg", "89")
    history = read_weight_history(db, user_id=user.id, start=first, end=today)
    assert history.points == ((first, Decimal("80")), (today, Decimal("89")))
    assert dict(history.providers) == {first: "apple_health", today: "google_health"}
    context = ContextBuilder(db, user).build(ExpertPreferencesView(
        data_domains=("weight",), include_meal_names=False, configuration_version=1,
    ))
    points = {point.date: point for point in context.weight_daily}
    assert points[first].provider_key == "apple_health"
    assert points[today].provider_key == "google_health"
    counts = {item.provider_key: item.days_with_data for aggregate in context.weight_aggregates for item in aggregate.providers}
    assert counts == {"apple_health": 1, "google_health": 1}


@pytest.mark.parametrize("provider,source_type", [("google_health", "google_health_activity_v4"), ("yazio", "yazio_export_v1")])
def test_activity_context_binds_values_to_the_same_current_source_as_bounds(db: Session, user: User, monkeypatch, provider, source_type):
    today = datetime.now(UTC).date()
    if provider == "google_health":
        connection = GoogleHealthConnection(user_id=user.id, state="active")
        db.add(connection)
        db.flush()
        source_identifier = str(connection.id)
    else:
        connection = db.query(YazioConnection).filter_by(user_id=user.id).one_or_none()
        if connection is None:
            connection = YazioConnection(user_id=user.id, source_identifier="current-synthetic-source", encrypted_email=b"synthetic", encrypted_password=b"synthetic")
            db.add(connection)
            db.flush()
        source_identifier = connection.source_identifier
    selected = ScalarProviderSelection("activity_energy", provider, source_type, ((provider, source_type),))
    monkeypatch.setattr("app.expert.context.resolve_scalar_provider", lambda *a, **k: selected)
    monkeypatch.setattr("app.analytics.verification.resolve_scalar_provider", lambda *a, **k: selected)
    sample(db, user, today, source_type, "retired-synthetic-source", "active_energy_kcal", "100", hour=1)
    sample(db, user, today, source_type, source_identifier, "active_energy_kcal", "300")
    context = ContextBuilder(db, user).build(ExpertPreferencesView(
        data_domains=("activity",), include_meal_names=False, configuration_version=1,
    ))
    point = next(item for item in context.activity_daily if item.date == today)
    assert point.value == Decimal("300")
    assert point.provider_key == provider
    assert context.activity_aggregates[0].providers[0].provider_key == provider
    assert context.activity_aggregates[0].providers[0].days_with_data == 1
    assert any("currently selected canonical provider" in item for item in context.limitations)
