from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import HealthSample, User
from app.services.import_service import persist_import


def sample(value: Decimal = Decimal("500")) -> CanonicalSample:
    return CanonicalSample(
        metric_type="dietary_energy_kcal",
        value=value,
        unit="kcal",
        original_value=value,
        original_unit="kcal",
        start_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
        end_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
        timezone="Europe/Berlin",
        source_type="calograph_sync_v1",
        source_name="Test",
        source_identifier="phone",
        external_sample_id="stable-id",
    )


def test_repeated_import_is_idempotent(db: Session, user: User) -> None:
    first = persist_import(
        db,
        user,
        AdapterResult("calograph_sync_v1", [sample()], received=1),
        b"{}",
        "application/json",
        "test",
    )
    second = persist_import(
        db,
        user,
        AdapterResult("calograph_sync_v1", [sample()], received=1),
        b"{}",
        "application/json",
        "test",
    )
    count = db.scalar(select(func.count()).select_from(HealthSample))
    assert first.inserted == 1
    assert second.skipped == 1
    assert count == 1


def test_duplicate_samples_in_same_import_are_skipped(db: Session, user: User) -> None:
    first = sample()
    first.external_sample_id = None
    duplicate = sample()
    duplicate.external_sample_id = None

    result = persist_import(
        db,
        user,
        AdapterResult("apple_health_xml", [first, duplicate], received=2),
        None,
        "application/xml",
        "export.xml",
    )

    count = db.scalar(select(func.count()).select_from(HealthSample))
    assert result.inserted == 1
    assert result.skipped == 1
    assert count == 1


def test_stable_id_updates_changed_value(db: Session, user: User) -> None:
    persist_import(
        db,
        user,
        AdapterResult("calograph_sync_v1", [sample()], received=1),
        None,
        "application/json",
        "test",
    )
    result = persist_import(
        db,
        user,
        AdapterResult("calograph_sync_v1", [sample(Decimal("550"))], received=1),
        None,
        "application/json",
        "test",
    )
    saved = db.scalar(select(HealthSample))
    assert result.updated == 1
    assert saved and saved.value == Decimal("550")
