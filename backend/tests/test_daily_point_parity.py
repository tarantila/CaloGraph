from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.analytics.daily_point_parity import (
    CanonicalDailyPointReason,
    CanonicalDailyPointResult,
    CanonicalDailyPointResultState,
    DailyPointFieldParity,
    DailyPointParity,
    DailyPointParityClassification,
    DailyPointTrackingParity,
    _build_canonical_daily_point,
    compare_daily_point,
)
from app.analytics.nutrition_parity import (
    NutritionDayParity,
    NutritionMetricParity,
    NutritionParityClassification,
)
from app.analytics import daily_point_parity as parity_module
from app.analytics.nutrition_projection import (
    CanonicalNutritionDay,
    CanonicalNutritionFact,
    NutritionProjectionReadError,
    NutritionProjectionReadState,
)
from app.models import TrackingOverride, User
from app.schemas import DailyPoint
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.resolution.metrics import CANONICAL_METRICS

LOCAL_DATE = date(2024, 1, 2)


def _fact(
    metric_key: str,
    value: Decimal | None,
    *,
    presence_state: PresenceState = PresenceState.SUPPLIED,
    coverage_state: CoverageState = CoverageState.COMPLETE,
    resolution_state: ResolutionState = ResolutionState.RESOLVED,
    lineage_state: LineageState = LineageState.CONFIRMED,
) -> CanonicalNutritionFact:
    return CanonicalNutritionFact(
        metric_key=metric_key,
        unit=CANONICAL_METRICS[metric_key].canonical_unit,
        value=value,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        provider_key="test-provider",
        granularity=ProjectionGranularity.SUMMARY,
    )


def _projection_day(
    *,
    values: dict[str, Decimal | None] | None = None,
    has_primary_evidence: bool = True,
    calorie_usable: bool = True,
    calorie_coverage_complete: bool = True,
    calorie_resolution_resolved: bool = True,
    calorie_fact_overrides: dict[str, object] | None = None,
) -> CanonicalNutritionDay:
    values = values or {
        "dietary_energy_kcal": Decimal("1900"),
        "protein_g": Decimal("100"),
        "carbohydrates_g": Decimal("200"),
        "fat_g": Decimal("60"),
    }
    fact_overrides = calorie_fact_overrides or {}
    facts = tuple(
        _fact(
            metric_key,
            values.get(metric_key),
            **(
                fact_overrides
                if metric_key == "dietary_energy_kcal"
                else {}
            ),
        )
        for metric_key in CANONICAL_METRICS
    )
    return CanonicalNutritionDay(
        state=NutritionProjectionReadState.READY,
        user_id=uuid4(),
        local_date=LOCAL_DATE,
        projection_id=uuid4(),
        projection_version=1,
        projection_status=ProjectionStatus.READY,
        facts=facts,
        has_primary_evidence=has_primary_evidence,
        has_any_nutrition_value=any(value is not None for value in values.values()),
        calorie_value_available=values.get("dietary_energy_kcal") is not None,
        calorie_coverage_complete=calorie_coverage_complete,
        calorie_resolution_resolved=calorie_resolution_resolved,
        calorie_usable=calorie_usable,
    )


@pytest.mark.parametrize(
    (
        "description",
        "projection_day",
        "expected_status",
        "expected_score",
        "expected_calories",
    ),
    [
        (
            "no primary evidence",
            _projection_day(has_primary_evidence=False, calorie_usable=False),
            "no_data",
            0,
            Decimal("1900"),
        ),
        (
            "complete resolved calories",
            _projection_day(),
            "complete",
            1,
            Decimal("1900"),
        ),
        (
            "explicit zero",
            _projection_day(values={"dietary_energy_kcal": Decimal("0")}),
            "complete",
            1,
            Decimal("0"),
        ),
        (
            "missing calories",
            _projection_day(
                values={"dietary_energy_kcal": None, "protein_g": Decimal("100")},
                calorie_usable=False,
            ),
            "incomplete",
            0,
            None,
        ),
        (
            "partial coverage",
            _projection_day(
                calorie_usable=False,
                calorie_coverage_complete=False,
                calorie_fact_overrides={"coverage_state": CoverageState.PARTIAL},
            ),
            "incomplete",
            0,
            Decimal("1900"),
        ),
        (
            "unresolved resolution",
            _projection_day(
                calorie_usable=False,
                calorie_resolution_resolved=False,
                calorie_fact_overrides={"resolution_state": ResolutionState.UNRESOLVED},
            ),
            "incomplete",
            0,
            Decimal("1900"),
        ),
        (
            "uncertain lineage with usable calories",
            _projection_day(
                calorie_fact_overrides={"lineage_state": LineageState.UNCERTAIN},
            ),
            "complete",
            1,
            Decimal("1900"),
        ),
        (
            "secondary-only facts",
            _projection_day(
                values={"fiber_g": Decimal("25")},
                has_primary_evidence=False,
                calorie_usable=False,
            ),
            "no_data",
            0,
            None,
        ),
    ],
)
def test_canonical_tracking_uses_only_d1a_indicators(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    description: str,
    projection_day: CanonicalNutritionDay,
    expected_status: str,
    expected_score: int,
    expected_calories: Decimal | None,
) -> None:
    del description
    monkeypatch.setattr(
        "app.analytics.daily_point_parity.read_canonical_nutrition_day",
        lambda db, user_id, local_date: projection_day,
    )

    result = _build_canonical_daily_point(db, user.id, LOCAL_DATE)

    assert result.state is CanonicalDailyPointResultState.READY
    assert result.reason is None
    assert result.point is not None
    assert result.point.tracking_status == expected_status
    assert result.point.tracking_score == expected_score
    assert result.point.calories_kcal == expected_calories


def test_not_projected_is_not_comparable_without_health_sample_fallback(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.analytics.daily_point_parity.read_canonical_nutrition_day",
        lambda db, user_id, local_date: CanonicalNutritionDay(
            state=NutritionProjectionReadState.NOT_PROJECTED,
            user_id=user_id,
            local_date=local_date,
            projection_id=None,
            projection_version=None,
            projection_status=None,
            facts=(),
            has_primary_evidence=False,
            has_any_nutrition_value=False,
            calorie_value_available=False,
            calorie_coverage_complete=False,
            calorie_resolution_resolved=False,
            calorie_usable=False,
        ),
    )

    result = _build_canonical_daily_point(db, user.id, LOCAL_DATE)

    assert result.state is CanonicalDailyPointResultState.NOT_COMPARABLE
    assert result.point is None
    assert result.reason is CanonicalDailyPointReason.NOT_PROJECTED


def test_invalid_or_non_ready_projection_is_not_comparable_without_error_details(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.analytics.daily_point_parity.read_canonical_nutrition_day",
        lambda db, user_id, local_date: (_ for _ in ()).throw(
            NutritionProjectionReadError("secret projection payload")
        ),
    )

    result = _build_canonical_daily_point(db, user.id, LOCAL_DATE)

    assert result.state is CanonicalDailyPointResultState.NOT_COMPARABLE
    assert result.point is None
    assert result.reason is CanonicalDailyPointReason.PROJECTION_NOT_READY
    assert "secret" not in repr(result)

def test_unrelated_projection_read_errors_propagate(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.analytics.daily_point_parity.read_canonical_nutrition_day",
        lambda db, user_id, local_date: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        _build_canonical_daily_point(db, user.id, LOCAL_DATE)


def test_override_replaces_status_and_reasons_but_preserves_canonical_score(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.add(
        TrackingOverride(
            user_id=user.id,
            local_date=LOCAL_DATE,
            status="probably_incomplete",
            note="manual",
        )
    )
    db.commit()
    projection_day = _projection_day()
    monkeypatch.setattr(
        "app.analytics.daily_point_parity.read_canonical_nutrition_day",
        lambda db, user_id, local_date: projection_day,
    )

    result = _build_canonical_daily_point(db, user.id, LOCAL_DATE)

    assert result.point is not None
    assert result.point.tracking_status == "probably_incomplete"
    assert result.point.tracking_score == 1
    assert result.point.tracking_reasons == ["Manuell festgelegt"]
def _daily_point(
    *,
    calories: Decimal | None = Decimal("1900"),
    target: Decimal | None = Decimal("2000"),
    maintenance: Decimal | None = Decimal("2200"),
    tracking_status: str = "complete",
    tracking_score: int = 1,
    active_energy: Decimal | None = Decimal("300"),
    activity_credit: Decimal = Decimal("300"),
    activity_mode: str | None = "full",
    activity_source: str | None = "apple",
) -> DailyPoint:
    return DailyPoint(
        date=LOCAL_DATE,
        calories_kcal=calories,
        target_kcal=target,
        maintenance_kcal=maintenance,
        deviation_kcal=(
            None if calories is None or target is None else calories - target
        ),
        activity_mode=activity_mode,
        activity_source_type=activity_source,
        active_energy_kcal=active_energy,
        activity_credit_kcal=activity_credit,
        activity_data_status="credited" if active_energy is not None else "missing",
        effective_budget_kcal=(
            None if target is None else target + activity_credit
        ),
        effective_maintenance_kcal=(
            None if maintenance is None else maintenance + activity_credit
        ),
        effective_deviation_kcal=(
            None
            if calories is None or target is None
            else calories - target - activity_credit
        ),
        protein_g=Decimal("100"),
        carbs_g=Decimal("200"),
        fat_g=Decimal("60"),
        tracking_status=tracking_status,
        tracking_score=tracking_score,
        tracking_reasons=["legacy reason"],
    )


def _nutrition_day(
    classification: NutritionParityClassification = NutritionParityClassification.MATCH,
    *,
    calorie_legacy_value: Decimal | None = Decimal("1900"),
    calorie_projection_value: Decimal | None = Decimal("1900"),
    calorie_legacy_present: bool = True,
    calorie_projection_present: bool = True,
    calorie_coverage: CoverageState = CoverageState.COMPLETE,
    calorie_resolution: ResolutionState = ResolutionState.RESOLVED,
) -> NutritionDayParity:
    metrics = tuple(
        NutritionMetricParity(
            metric_key=metric_key,
            legacy_value=(
                calorie_legacy_value
                if metric_key == "dietary_energy_kcal"
                else Decimal("1")
            ),
            legacy_present=(
                calorie_legacy_present
                if metric_key == "dietary_energy_kcal"
                else True
            ),
            projection_value=(
                calorie_projection_value
                if metric_key == "dietary_energy_kcal"
                else Decimal("1")
            ),
            projection_present=(
                calorie_projection_present
                if metric_key == "dietary_energy_kcal"
                else True
            ),
            projection_provider_key="yazio",
            projection_coverage_state=(
                calorie_coverage
                if metric_key == "dietary_energy_kcal"
                else CoverageState.COMPLETE
            ),
            projection_resolution_state=(
                calorie_resolution
                if metric_key == "dietary_energy_kcal"
                else ResolutionState.RESOLVED
            ),
            projection_lineage_state=LineageState.CONFIRMED,
            classification=(
                classification
                if metric_key == "dietary_energy_kcal"
                else NutritionParityClassification.MATCH
            ),
        )
        for metric_key in ("dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g")
    )
    return NutritionDayParity(
        local_date=LOCAL_DATE,
        projection_state=NutritionProjectionReadState.READY,
        metrics=metrics,
        match_count=sum(
            metric.classification is NutritionParityClassification.MATCH for metric in metrics
        ),
        mismatch_count=sum(
            metric.classification is NutritionParityClassification.VALUE_MISMATCH for metric in metrics
        ),
        expected_difference_count=sum(
            metric.classification is NutritionParityClassification.LEGACY_MULTI_SOURCE
            for metric in metrics
        ),
        comparable=True,
    )


def _stub_daily_point_comparison(
    monkeypatch: pytest.MonkeyPatch,
    *,
    legacy: DailyPoint,
    canonical: DailyPoint | None,
    nutrition: NutritionDayParity,
) -> None:
    monkeypatch.setattr(
        parity_module,
        "daily_points",
        lambda db, user, start, end: [legacy],
    )
    monkeypatch.setattr(
        parity_module,
        "_build_canonical_daily_point",
        lambda db, user_id, local_date: (
            CanonicalDailyPointResult(
                state=CanonicalDailyPointResultState.READY,
                point=canonical,
            )
            if canonical is not None
            else CanonicalDailyPointResult(
                state=CanonicalDailyPointResultState.NOT_COMPARABLE,
                reason=CanonicalDailyPointReason.NOT_PROJECTED,
            )
        ),
    )
    monkeypatch.setattr(
        parity_module,
        "compare_nutrition_day",
        lambda db, **kwargs: nutrition,
    )


def test_daily_point_parity_contracts_are_frozen_and_tuple_backed() -> None:
    for contract in (
        DailyPointTrackingParity,
        DailyPointFieldParity,
        DailyPointParity,
    ):
        assert is_dataclass(contract)
        assert contract.__dataclass_params__.frozen
        assert contract.__slots__

    tracking = DailyPointTrackingParity(
        local_date=LOCAL_DATE,
        legacy_status="complete",
        canonical_status="complete",
        legacy_score=1,
        canonical_score=1,
        legacy_reasons=("legacy",),
        canonical_reasons=("canonical",),
        comparable=True,
        classification=DailyPointParityClassification.MATCH,
    )
    with pytest.raises(FrozenInstanceError):
        tracking.legacy_status = "changed"  # type: ignore[misc]
    assert isinstance(tracking.legacy_reasons, tuple)

def test_daily_point_parity_exact_match_keeps_reasons_separate(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    point = _daily_point()
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=point,
        canonical=point,
        nutrition=_nutrition_day(),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert isinstance(result, DailyPointParity)
    assert result.comparable is True
    assert result.classification is DailyPointParityClassification.MATCH
    assert isinstance(result.tracking, DailyPointTrackingParity)
    assert result.tracking.classification is DailyPointParityClassification.MATCH
    assert result.tracking.legacy_reasons == ("legacy reason",)
    assert result.tracking.canonical_reasons == ("legacy reason",)
    assert isinstance(result.fields, DailyPointFieldParity)
    assert result.fields.differences == ()


def test_daily_point_parity_explicit_zero_divergence_is_classified(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point(calories=None, tracking_status="no_data", tracking_score=0)
    canonical = _daily_point(calories=Decimal("0"))
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(
            calorie_legacy_value=Decimal("0"),
            calorie_projection_value=Decimal("0"),
        ),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.classification is DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE
    assert result.tracking.classification is DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE
    assert result.fields.differences[0].field_name == "calories_kcal"
    assert (
        result.fields.differences[0].classification
        is DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE
    )


@pytest.mark.parametrize(
    "nutrition_classification",
    (
        NutritionParityClassification.LEGACY_ONLY,
        NutritionParityClassification.PROJECTION_ONLY,
        NutritionParityClassification.VALUE_MISMATCH,
    ),
)
def test_unsupported_nutrition_differences_are_unexplained(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    nutrition_classification: NutritionParityClassification,
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(calories=Decimal("1901"))
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(nutrition_classification),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
    calorie_difference = next(
        difference
        for difference in result.fields.differences
        if difference.field_name == "calories_kcal"
    )
    assert calorie_difference.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH


def test_multi_source_nutrition_difference_explains_calorie_and_derived_fields(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(calories=Decimal("1800"))
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(NutritionParityClassification.LEGACY_MULTI_SOURCE),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.classification is DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    assert {
        difference.field_name
        for difference in result.fields.differences
        if difference.classification
        is DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    } == {
        "calories_kcal",
        "deviation_kcal",
        "effective_deviation_kcal",
    }


@pytest.mark.parametrize(
    ("coverage", "resolution"),
    (
        (CoverageState.PARTIAL, ResolutionState.RESOLVED),
        (CoverageState.COMPLETE, ResolutionState.UNRESOLVED),
    ),
)
def test_canonical_quality_difference_explains_tracking_quality_only(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    coverage: CoverageState,
    resolution: ResolutionState,
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(tracking_status="incomplete", tracking_score=0)
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(
            calorie_coverage=coverage,
            calorie_resolution=resolution,
        ),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.classification is DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE
    assert result.tracking.classification is DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE
    assert result.fields.differences == ()
def test_unexplained_tracking_status_mismatch_is_not_expected(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point(tracking_status="complete", tracking_score=1)
    canonical = _daily_point(tracking_status="probably_complete", tracking_score=1)
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.tracking.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
    assert result.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH



def test_target_and_activity_mismatches_remain_unexplained(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(target=Decimal("2100"), activity_source="google")
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
    assert {
        difference.field_name for difference in result.fields.differences
    } >= {"target_kcal", "activity_source_type"}
    assert all(
        difference.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
        for difference in result.fields.differences
    )


def test_non_comparable_canonical_candidate_short_circuits_fields(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=None,
        nutrition=NutritionDayParity(
            local_date=LOCAL_DATE,
            projection_state=NutritionProjectionReadState.NOT_PROJECTED,
            metrics=(),
            match_count=0,
            mismatch_count=0,
            expected_difference_count=0,
            comparable=False,
        ),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.comparable is False
    assert result.classification is DailyPointParityClassification.NOT_COMPARABLE
    assert result.fields.comparable is False
    assert result.fields.differences == ()
