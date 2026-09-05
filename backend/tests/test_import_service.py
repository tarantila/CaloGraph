import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import HealthSample, ImportBatch, User
from app.services import import_service
from app.services.import_service import persist_apple_health_stream, persist_import


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


def test_yazio_upload_without_connector_variant_keeps_provenance_unknown(
    db: Session, user: User
) -> None:
    imported_sample = sample()
    imported_sample.source_type = "yazio_export_v1"

    result = persist_import(
        db,
        user,
        AdapterResult("yazio_export_v1", [imported_sample], received=1),
        None,
        "application/json",
        "uploaded-yazio.json",
    )

    batch = db.get(ImportBatch, result.batch_id)
    assert batch is not None
    assert batch.connector_variant is None

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


def test_sample_batches_use_bulk_lookups(
    db: Session,
    user: User,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "import_batch_size", 2)
    samples = []
    for index in range(5):
        item = sample(Decimal(500 + index))
        item.external_sample_id = None
        item.start_at = datetime(2024, 1, 2, 12, index, tzinfo=UTC)
        item.end_at = item.start_at
        samples.append(item)

    health_sample_selects = 0

    def count_queries(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal health_sample_selects
        if statement.lstrip().upper().startswith("SELECT") and "health_samples" in statement:
            health_sample_selects += 1

    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        result = persist_import(
            db,
            user,
            AdapterResult("apple_health_xml", samples, received=len(samples)),
            None,
            "application/xml",
            "export.xml",
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)

    assert result.inserted == 5
    assert health_sample_selects == 3


def test_malformed_xml_after_committed_batches_is_partial_failed(
    db: Session,
    user: User,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "import_batch_size", 2)
    records = "".join(
        (
            '<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" '
            f'sourceName="Synthetic" unit="kcal" value="{500 + index}" '
            f'startDate="2024-01-02 12:0{index}:00 +0000" />'
        )
        for index in range(5)
    )
    malformed = f"<?xml version='1.0'?><HealthData>{records}"

    result = persist_apple_health_stream(
        db,
        user,
        io.BytesIO(malformed.encode()),
        "application/xml",
        "broken-export.xml",
    )

    saved_batch = db.get(ImportBatch, result.batch_id)
    assert result.status == "partial_failed"
    assert result.received == 5
    assert result.inserted == 4
    assert result.failed == 1
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 4
    assert saved_batch is not None
    assert saved_batch.error_message == "XML- oder ZIP-Datei ist beschädigt oder unvollständig"


def test_record_limit_marks_only_committed_batches_as_partial(
    db: Session,
    user: User,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "import_batch_size", 2)
    monkeypatch.setattr(settings, "max_import_records", 3)
    records = "".join(
        (
            '<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" '
            f'sourceName="Synthetic" unit="kcal" value="{500 + index}" '
            f'startDate="2024-01-02 12:0{index}:00 +0000" />'
        )
        for index in range(4)
    )
    xml = f"<?xml version='1.0'?><HealthData>{records}</HealthData>"

    result = persist_apple_health_stream(
        db,
        user,
        io.BytesIO(xml.encode()),
        "application/xml",
        "limited-export.xml",
    )

    assert result.status == "partial_failed"
    assert result.inserted == 2
    saved_batch = db.get(ImportBatch, result.batch_id)
    assert saved_batch is not None
    assert saved_batch.error_message == "Import enthält zu viele XML-Datensätze"


def test_database_failure_after_xml_checkpoint_is_recorded_as_partial(
    db: Session,
    user: User,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "import_batch_size", 2)
    records = "".join(
        (
            '<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" '
            f'sourceName="Synthetic" unit="kcal" value="{500 + index}" '
            f'startDate="2024-01-02 12:0{index}:00 +0000" />'
        )
        for index in range(4)
    )
    xml = f"<?xml version='1.0'?><HealthData>{records}</HealthData>"
    original_persist = import_service._persist_sample_batch
    calls = 0

    def fail_second_batch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SQLAlchemyError("synthetic database failure")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(import_service, "_persist_sample_batch", fail_second_batch)

    with pytest.raises(SQLAlchemyError, match="synthetic database failure"):
        persist_apple_health_stream(
            db,
            user,
            io.BytesIO(xml.encode()),
            "application/xml",
            "database-failure.xml",
        )

    saved_batch = db.scalar(
        select(ImportBatch).where(
            ImportBatch.client_identifier == "database-failure.xml"
        )
    )
    assert saved_batch is not None
    assert saved_batch.status == "partial_failed"
    assert saved_batch.finished_at is not None
    assert saved_batch.inserted == 2
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 2
