from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.analytics.nutrition_parity import (
    CANONICAL_PARITY_METRICS,
    MAX_PARITY_DAYS,
    NutritionLegacyDay,
    NutritionLegacyMetric,
    NutritionParityClassification,
    NutritionLegacySourceBreakdown,
    read_legacy_nutrition_day,
)
from app.models import HealthSample, ImportBatch, User

LOCAL_DATE = date(2026, 9, 11)


def _other_user(db: Session) -> User:
    other = User(username="legacy-parity-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    return other


def _batch(db: Session, user: User, source_type: str = "test") -> ImportBatch:
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    return batch


def _sample(
    db: Session,
    *,
    user: User,
    batch: ImportBatch,
    metric_type: str,
    value: str,
    source_type: str = "test",
    local_date: date = LOCAL_DATE,
    suffix: str,
) -> None:
    at = datetime.combine(local_date, datetime.min.time(), tzinfo=UTC)
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=f"{suffix}-external",
            fingerprint=f"{suffix}-fingerprint",
            source_type=source_type,
            source_name=source_type,
            source_identifier=f"{suffix}-source",
            metric_type=metric_type,
            value=Decimal(value),
            unit="kcal" if metric_type == "dietary_energy_kcal" else "g",
            original_value=Decimal(value),
            original_unit="kcal" if metric_type == "dietary_energy_kcal" else "g",
            start_at=at,
            end_at=at,
            local_date=local_date,
            timezone="UTC",
        )
    )


def test_canonical_parity_contract_is_exact_and_immutable() -> None:
    assert CANONICAL_PARITY_METRICS == (
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    )
    assert len(CANONICAL_PARITY_METRICS) == 7
    assert MAX_PARITY_DAYS == 366
    assert NutritionParityClassification.MATCH.value == "match"
    assert NutritionParityClassification.NOT_PROJECTED.value == "not_projected"

    for contract in (
        NutritionLegacySourceBreakdown,
        NutritionLegacyMetric,
        NutritionLegacyDay,
    ):
        assert is_dataclass(contract)
        assert getattr(contract, "__dataclass_params__").frozen
        assert getattr(contract, "__slots__")

    metric = NutritionLegacyMetric(
        metric_key="protein_g",
        total=Decimal("1"),
        present=True,
        source_breakdown=(
            NutritionLegacySourceBreakdown(source_type="test", value=Decimal("1")),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        metric.total = Decimal("2")  # type: ignore[misc]

    assert {field.name for field in fields(NutritionLegacyDay)} == {
        "user_id",
        "local_date",
        "metrics",
    }


def test_legacy_reader_scopes_and_sums_decimal_values_by_source(db: Session, user: User) -> None:
    batch = _batch(db, user)
    other = _other_user(db)
    other_batch = _batch(db, other)

    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="dietary_energy_kcal",
        value="100.123456",
        source_type="yazio",
        suffix="energy-yazio-1",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="dietary_energy_kcal",
        value="2.000000",
        source_type="yazio",
        suffix="energy-yazio-2",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="dietary_energy_kcal",
        value="0.500000",
        source_type="google",
        suffix="energy-google",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="protein_g",
        value="0",
        source_type="yazio",
        suffix="protein-zero",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="salt",
        value="99",
        source_type="unrelated",
        suffix="salt",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="protein_g",
        value="7",
        source_type="test",
        local_date=date(2026, 9, 10),
        suffix="other-date",
    )
    _sample(
        db,
        user=other,
        batch=other_batch,
        metric_type="dietary_energy_kcal",
        value="999",
        source_type="yazio",
        suffix="other-user",
    )
    db.commit()

    day = read_legacy_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert day.user_id == user.id
    assert day.local_date == LOCAL_DATE
    assert type(day.metrics) is tuple
    assert tuple(metric.metric_key for metric in day.metrics) == CANONICAL_PARITY_METRICS
    assert len(day.metrics) == 7

    by_metric = {metric.metric_key: metric for metric in day.metrics}
    energy = by_metric["dietary_energy_kcal"]
    assert energy.total == Decimal("102.623456")
    assert type(energy.total) is Decimal
    assert energy.present is True
    assert type(energy.source_breakdown) is tuple
    assert energy.source_breakdown == (
        NutritionLegacySourceBreakdown(source_type="google", value=Decimal("0.500000")),
        NutritionLegacySourceBreakdown(source_type="yazio", value=Decimal("102.123456")),
    )

    protein = by_metric["protein_g"]
    assert protein.total == Decimal("0")
    assert type(protein.total) is Decimal
    assert protein.present is True
    assert protein.source_breakdown == (
        NutritionLegacySourceBreakdown(source_type="yazio", value=Decimal("0.000000")),
    )

    for metric_key in (
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    ):
        metric = by_metric[metric_key]
        assert metric.total is None
        assert metric.present is False
        assert metric.source_breakdown == ()
