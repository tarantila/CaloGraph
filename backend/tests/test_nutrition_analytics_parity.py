from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.analytics import nutrition_parity as parity_module
from app.analytics.nutrition_parity import (
    CANONICAL_PARITY_METRICS,
    MAX_PARITY_DAYS,
    NutritionDayParity,
    NutritionLegacyDay,
    NutritionLegacyMetric,
    NutritionMetricParity,
    NutritionParityClassification,
    NutritionRangeParity,
    NutritionLegacySourceBreakdown,
    compare_nutrition_day,
    compare_nutrition_range,
    read_legacy_nutrition_day,
)
from app.analytics.nutrition_projection import NutritionProjectionReadError
from app.models import HealthSample, ImportBatch, User
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.models import NutritionDailyProjection
from app.nutrition.repositories import create_projection, create_projection_fact, set_projection_head
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

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
    assert {
        "NutritionDayParity",
        "NutritionMetricParity",
        "NutritionParityClassification",
        "compare_nutrition_day",
        "NutritionRangeParity",
        "compare_nutrition_range",
    } <= set(parity_module.__all__)

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


def _priority_policy(db: Session, user: User):
    return create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(
            PriorityRuleSpec(
                data_area="nutrition",
                metric_key=None,
                provider_key="yazio",
                priority_rank=1,
            ),
        ),
    )


def _ready_projection(
    db: Session,
    user: User,
    *,
    values: dict[str, Decimal | None] | None = None,
    providers: dict[str, str | None] | None = None,
    presence_states: dict[str, PresenceState] | None = None,
    status: ProjectionStatus = ProjectionStatus.READY,
) -> NutritionDailyProjection:
    policy = _priority_policy(db, user)
    projection = create_projection(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="parity-test-watermark",
        projection_status=status.value,
    )
    values = (
        {metric_key: Decimal("10") for metric_key in CANONICAL_METRICS}
        if values is None
        else values
    )
    providers = {} if providers is None else providers
    presence_states = {} if presence_states is None else presence_states
    for metric_key, definition in CANONICAL_METRICS.items():
        value = values.get(metric_key)
        if value is None:
            defaults: dict[str, object] = {
                "value": None,
                "unit": definition.canonical_unit,
                "selected_provider_key": None,
                "selected_granularity": None,
                "presence_state": PresenceState.UNKNOWN.value,
                "coverage_state": CoverageState.UNKNOWN.value,
                "resolution_state": ResolutionState.UNRESOLVED.value,
                "lineage_state": LineageState.UNKNOWN.value,
            }
        else:
            defaults = {
                "value": value,
                "unit": definition.canonical_unit,
                "selected_provider_key": providers.get(metric_key, "yazio"),
                "selected_granularity": ProjectionGranularity.SUMMARY.value,
                "presence_state": presence_states.get(
                    metric_key, PresenceState.SUPPLIED
                ).value,
                "coverage_state": CoverageState.COMPLETE.value,
                "resolution_state": ResolutionState.RESOLVED.value,
                "lineage_state": LineageState.CONFIRMED.value,
            }
        create_projection_fact(
            db,
            user_id=user.id,
            projection_id=projection.id,
            metric_key=metric_key,
            **defaults,
        )
    set_projection_head(db, user.id, LOCAL_DATE, projection.id)
    db.commit()
    return projection


def _legacy_metric_sample(
    db: Session,
    user: User,
    *,
    value: str,
    source_type: str = "yazio_export_v1",
    metric_type: str = "dietary_energy_kcal",
    suffix: str = "classification",
) -> None:
    batch = _batch(db, user, source_type=source_type)
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type=metric_type,
        value=value,
        source_type=source_type,
        suffix=suffix,
    )
    db.flush()


def _classification(day: NutritionDayParity, metric_key: str = "dietary_energy_kcal"):
    return next(metric for metric in day.metrics if metric.metric_key == metric_key)


def test_classification_contracts_are_immutable_and_expose_safe_fields() -> None:
    for contract in (NutritionMetricParity, NutritionDayParity):
        assert is_dataclass(contract)
        assert getattr(contract, "__dataclass_params__").frozen
        assert getattr(contract, "__slots__")


@pytest.mark.parametrize(
    ("legacy_value", "projection_value", "expected"),
    (
        ("10", "10", NutritionParityClassification.MATCH),
        (None, None, NutritionParityClassification.BOTH_MISSING),
        ("10", None, NutritionParityClassification.LEGACY_ONLY),
        (None, "10", NutritionParityClassification.PROJECTION_ONLY),
        ("10", "11", NutritionParityClassification.VALUE_MISMATCH),
    ),
)
def test_classification_covers_presence_and_value_states(
    db: Session,
    user: User,
    legacy_value: str | None,
    projection_value: str | None,
    expected: NutritionParityClassification,
) -> None:
    if legacy_value is not None:
        _legacy_metric_sample(db, user, value=legacy_value)
    _ready_projection(
        db,
        user,
        values={
            metric_key: (None if metric_key != "dietary_energy_kcal" else
                         (None if projection_value is None else Decimal(projection_value)))
            for metric_key in CANONICAL_METRICS
        },
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    metric = _classification(result)
    assert metric.classification is expected
    assert metric.legacy_present is (legacy_value is not None)
    assert metric.projection_present is (projection_value is not None)
    assert result.comparable is True
    assert result.match_count == (
        7
        if expected
        in {
            NutritionParityClassification.MATCH,
            NutritionParityClassification.BOTH_MISSING,
        }
        else 6
    )
    assert result.mismatch_count == (
        1
        if expected
        in {
            NutritionParityClassification.LEGACY_ONLY,
            NutritionParityClassification.PROJECTION_ONLY,
            NutritionParityClassification.VALUE_MISMATCH,
        }
        else 0
    )
    assert result.expected_difference_count == 0


def test_explicit_zero_zero_is_match_and_presence_is_not_truthiness(db: Session, user: User) -> None:
    _legacy_metric_sample(db, user, value="0", suffix="zero-legacy")
    _ready_projection(
        db,
        user,
        values={metric_key: (Decimal("0") if metric_key == "dietary_energy_kcal" else None)
                for metric_key in CANONICAL_METRICS},
        presence_states={"dietary_energy_kcal": PresenceState.EXPLICIT_ZERO},
    )

    metric = _classification(
        compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)
    )

    assert metric.classification is NutritionParityClassification.MATCH
    assert metric.legacy_value == Decimal("0")
    assert metric.projection_value == Decimal("0")
    assert metric.legacy_present is True
    assert metric.projection_present is True


@pytest.mark.parametrize(
    ("legacy_value", "projection_value", "expected"),
    (
        ("0", None, NutritionParityClassification.LEGACY_ONLY),
        (None, "0", NutritionParityClassification.PROJECTION_ONLY),
    ),
)
def test_zero_and_missing_are_distinct_in_both_directions(
    db: Session,
    user: User,
    legacy_value: str | None,
    projection_value: str | None,
    expected: NutritionParityClassification,
) -> None:
    if legacy_value is not None:
        _legacy_metric_sample(db, user, value=legacy_value, suffix="zero-missing")
    _ready_projection(
        db,
        user,
        values={
            metric_key: (None if metric_key != "dietary_energy_kcal" else
                         (None if projection_value is None else Decimal(projection_value)))
            for metric_key in CANONICAL_METRICS
        },
        presence_states=(
            {"dietary_energy_kcal": PresenceState.EXPLICIT_ZERO}
            if projection_value == "0"
            else None
        ),
    )

    metric = _classification(
        compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)
    )

    assert metric.classification is expected


def test_missing_projection_head_is_not_projected_and_non_comparable(
    db: Session, user: User
) -> None:
    _legacy_metric_sample(db, user, value="10", suffix="missing-head")

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.projection_state.value == "not_projected"
    assert result.comparable is False
    assert all(
        metric.classification is NutritionParityClassification.NOT_PROJECTED
        for metric in result.metrics
    )
    assert result.match_count == result.mismatch_count == result.expected_difference_count == 0


def test_ready_no_value_facts_are_comparable_both_missing(db: Session, user: User) -> None:
    _ready_projection(
        db,
        user,
        values={metric_key: None for metric_key in CANONICAL_METRICS},
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.projection_state.value == "ready"
    assert result.comparable is True
    assert all(
        metric.classification is NutritionParityClassification.BOTH_MISSING
        for metric in result.metrics
    )
    assert result.match_count == 7
    assert result.mismatch_count == 0
    assert result.expected_difference_count == 0


def test_non_ready_projection_is_projection_not_ready_without_error_details(
    db: Session, user: User
) -> None:
    _ready_projection(
        db,
        user,
        status=ProjectionStatus.FAILED,
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)
    rendered = repr(result)

    assert result.projection_state is None
    assert result.comparable is False
    assert all(
        metric.classification is NutritionParityClassification.PROJECTION_NOT_READY
        for metric in result.metrics
    )
    assert "current projection is not READY" not in rendered


def test_reader_error_is_fail_closed_and_reader_is_called_once(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def _error(*_args: object, **_kwargs: object):
        nonlocal calls
        calls += 1
        raise NutritionProjectionReadError("secret projection payload")

    monkeypatch.setattr(parity_module, "read_canonical_nutrition_day", _error)

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert calls == 1
    assert result.projection_state is None
    assert result.comparable is False
    assert all(
        metric.classification is NutritionParityClassification.PROJECTION_NOT_READY
        for metric in result.metrics
    )
    assert "secret projection payload" not in repr(result)


def test_projection_metadata_is_retained_per_metric_provider(db: Session, user: User) -> None:
    _legacy_metric_sample(
        db,
        user,
        value="10",
        metric_type="dietary_energy_kcal",
        suffix="provider-energy",
    )
    _legacy_metric_sample(
        db,
        user,
        value="20",
        metric_type="protein_g",
        suffix="provider-protein",
    )
    _ready_projection(
        db,
        user,
        values={
            **{metric_key: None for metric_key in CANONICAL_METRICS},
            "dietary_energy_kcal": Decimal("10"),
            "protein_g": Decimal("20"),
        },
        providers={"dietary_energy_kcal": "yazio", "protein_g": "google_health"},
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)
    energy = _classification(result)
    protein = _classification(result, "protein_g")

    assert energy.projection_provider_key == "yazio"
    assert protein.projection_provider_key == "google_health"
    assert energy.projection_coverage_state is CoverageState.COMPLETE
    assert energy.projection_resolution_state is ResolutionState.RESOLVED
    assert energy.projection_lineage_state is LineageState.CONFIRMED


def test_multi_source_yazio_mapping_is_expected_difference(db: Session, user: User) -> None:
    _legacy_metric_sample(
        db, user, value="2000", source_type="yazio_export_v1", suffix="multi-yazio"
    )
    _legacy_metric_sample(
        db, user, value="2000", source_type="google", suffix="multi-google"
    )
    _ready_projection(
        db,
        user,
        values={
            **{metric_key: None for metric_key in CANONICAL_METRICS},
            "dietary_energy_kcal": Decimal("2000"),
        },
        providers={"dietary_energy_kcal": "yazio"},
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)
    metric = _classification(result)

    assert metric.classification is NutritionParityClassification.LEGACY_MULTI_SOURCE
    assert result.match_count == 6
    assert result.mismatch_count == 0
    assert result.expected_difference_count == 1


def test_multi_source_selected_provider_mismatch_is_value_mismatch(
    db: Session, user: User
) -> None:
    _legacy_metric_sample(
        db, user, value="2000", source_type="yazio_export_v1", suffix="multi-bad-yazio"
    )
    _legacy_metric_sample(
        db, user, value="500", source_type="google", suffix="multi-bad-google"
    )
    _ready_projection(
        db,
        user,
        values={
            **{metric_key: None for metric_key in CANONICAL_METRICS},
            "dietary_energy_kcal": Decimal("1900"),
        },
        providers={"dietary_energy_kcal": "yazio"},
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert _classification(result).classification is NutritionParityClassification.VALUE_MISMATCH


def test_unmapped_legacy_source_is_not_treated_as_projection_provider_alias(
    db: Session, user: User
) -> None:
    _legacy_metric_sample(
        db, user, value="2000", source_type="apple_health_xml", suffix="unmapped-apple"
    )
    _legacy_metric_sample(
        db, user, value="2000", source_type="google", suffix="unmapped-google"
    )
    _ready_projection(
        db,
        user,
        values={
            **{metric_key: None for metric_key in CANONICAL_METRICS},
            "dietary_energy_kcal": Decimal("2000"),
        },
        providers={"dietary_energy_kcal": "apple_health_xml"},
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert _classification(result).classification is NutritionParityClassification.VALUE_MISMATCH


def test_projection_and_legacy_reads_are_user_scoped(db: Session, user: User) -> None:
    other = _other_user(db)
    _legacy_metric_sample(db, other, value="10", suffix="other-legacy")
    _ready_projection(
        db,
        other,
        values={
            **{metric_key: None for metric_key in CANONICAL_METRICS},
            "dietary_energy_kcal": Decimal("10"),
        },
    )

    result = compare_nutrition_day(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.projection_state.value == "not_projected"
    assert result.comparable is False
    assert all(
        metric.classification is NutritionParityClassification.NOT_PROJECTED
        for metric in result.metrics
    )


def test_nutrition_range_is_immutable_and_empty_ranges_are_safe(
    db: Session, user: User
) -> None:
    result = compare_nutrition_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE + timedelta(days=1),
    )

    assert isinstance(result, NutritionRangeParity)
    assert result.days == ()
    assert result.days_compared == 0
    assert result.days_not_comparable == 0
    assert dict(result.match_counts) == dict.fromkeys(CANONICAL_PARITY_METRICS, 0)
    assert dict(result.mismatch_counts) == dict.fromkeys(CANONICAL_PARITY_METRICS, 0)
    assert dict(result.expected_difference_counts) == dict.fromkeys(
        CANONICAL_PARITY_METRICS, 0
    )

    with pytest.raises(FrozenInstanceError):
        result.days_compared = 1  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.match_counts["protein_g"] = 1  # type: ignore[index]


def test_nutrition_range_rejects_reversed_and_oversized_ranges() -> None:
    with pytest.raises(ValueError):
        compare_nutrition_range(
            object(),  # type: ignore[arg-type]
            user_id=object(),  # type: ignore[arg-type]
            start=LOCAL_DATE + timedelta(days=1),
            end=LOCAL_DATE,
        )

    with pytest.raises(ValueError):
        compare_nutrition_range(
            object(),  # type: ignore[arg-type]
            user_id=object(),  # type: ignore[arg-type]
            start=LOCAL_DATE,
            end=LOCAL_DATE + timedelta(days=MAX_PARITY_DAYS),
        )


def test_nutrition_range_sorts_days_and_aggregates_metric_counts(
    db: Session, user: User
) -> None:
    next_date = LOCAL_DATE + timedelta(days=1)
    batch = _batch(db, user, source_type="yazio_export_v1")
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="dietary_energy_kcal",
        value="10",
        source_type="yazio_export_v1",
        local_date=LOCAL_DATE,
        suffix="range-energy-yazio",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="dietary_energy_kcal",
        value="10",
        source_type="google",
        local_date=LOCAL_DATE,
        suffix="range-energy-google",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="protein_g",
        value="20",
        source_type="yazio_export_v1",
        local_date=LOCAL_DATE,
        suffix="range-protein",
    )
    _sample(
        db,
        user=user,
        batch=batch,
        metric_type="dietary_energy_kcal",
        value="7",
        source_type="yazio_export_v1",
        local_date=next_date,
        suffix="range-not-projected",
    )
    _ready_projection(
        db,
        user,
        values={
            "dietary_energy_kcal": Decimal("10"),
            "protein_g": Decimal("10"),
            **{
                metric_key: None
                for metric_key in CANONICAL_METRICS
                if metric_key not in {"dietary_energy_kcal", "protein_g"}
            },
        },
        providers={"dietary_energy_kcal": "yazio"},
    )

    result = compare_nutrition_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=next_date,
    )

    assert tuple(day.local_date for day in result.days) == (LOCAL_DATE, next_date)
    assert result.days_compared == 1
    assert result.days_not_comparable == 1
    assert result.match_counts["dietary_energy_kcal"] == 0
    assert result.expected_difference_counts["dietary_energy_kcal"] == 1
    assert result.mismatch_counts["dietary_energy_kcal"] == 0
    assert result.match_counts["protein_g"] == 0
    assert result.expected_difference_counts["protein_g"] == 0
    assert result.mismatch_counts["protein_g"] == 1
    for metric_key in (
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    ):
        assert result.match_counts[metric_key] == 1
        assert result.expected_difference_counts[metric_key] == 0
        assert result.mismatch_counts[metric_key] == 0


def test_nutrition_range_excludes_another_users_identical_rows(
    db: Session, user: User
) -> None:
    other = _other_user(db)
    target_batch = _batch(db, user, source_type="yazio_export_v1")
    other_batch = _batch(db, other, source_type="yazio_export_v1")
    _sample(
        db,
        user=user,
        batch=target_batch,
        metric_type="dietary_energy_kcal",
        value="10",
        source_type="yazio_export_v1",
        suffix="range-target",
    )
    _sample(
        db,
        user=other,
        batch=other_batch,
        metric_type="dietary_energy_kcal",
        value="999",
        source_type="yazio_export_v1",
        suffix="range-other",
    )
    _ready_projection(
        db,
        user,
        values={
            "dietary_energy_kcal": Decimal("10"),
            **{
                metric_key: None
                for metric_key in CANONICAL_METRICS
                if metric_key != "dietary_energy_kcal"
            },
        },
    )

    result = compare_nutrition_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
    )

    assert tuple(day.local_date for day in result.days) == (LOCAL_DATE,)
    assert result.days_compared == 1
    assert result.days_not_comparable == 0
    assert result.match_counts["dietary_energy_kcal"] == 1
    assert result.mismatch_counts["dietary_energy_kcal"] == 0
