from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE
from app.analytics import daily_point_parity as parity_module
from app.analytics.daily_point_parity import (
    CanonicalDailyPointReason,
    CanonicalDailyPointResult,
    CanonicalDailyPointResultState,
    DailyPointFieldDifference,
    DailyPointFieldParity,
    DailyPointParity,
    DailyPointParityClassification,
    DailyPointRangeParity,
    DailyPointTrackingParity,
    _build_canonical_daily_point,
    _read_daily_point_range_inputs,
    compare_daily_point,
    compare_daily_point_range,
)
from app.analytics.nutrition_parity import (
    MAX_PARITY_DAYS,
    NutritionDayParity,
    NutritionMetricParity,
    NutritionParityClassification,
)
from app.analytics.nutrition_projection import (
    CanonicalNutritionDay,
    CanonicalNutritionFact,
    NutritionProjectionReadError,
    NutritionProjectionReadState,
)
from app.analytics.service import daily_points
from app.config import settings
from app.google_health.client import (
    NutritionDataSource,
    NutritionDataSourceApplication,
    NutritionDataSourceDevice,
    NutritionLog,
    NutritionLogDataPoint,
    NutritionLogInterval,
    NutritionNutrient,
    NutritionQuantity,
)
from app.models import (
    GoogleHealthConnection,
    HealthSample,
    ImportBatch,
    NutritionTarget,
    TrackingOverride,
    User,
    UserProviderPriority,
    YazioConnection,
)
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionProjectionHead,
)
from app.nutrition.projection import ProjectionPersistenceStatus
from app.nutrition.projection.orchestration import rebuild_nutrition_day
from app.nutrition.repositories import (
    create_projection,
    create_projection_fact,
    set_projection_head,
)
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS, DAILY_PROJECTION_METRICS
from app.schemas import DailyPoint
from app.services.credential_crypto import encrypt_credential
from app.services.google_health_nutrition_ingestion import ingest_google_health_nutrition_logs
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

_D3B_PRIVACY_MARKERS = (
    "d3b-yazio-source",
    "d3b-yazio",
    "d3b-google-legacy",
    "d3b-google-day",
    "d3b-apple-only",
    "d3b-precision-within",
    "d3b-precision-outside",
    "d3b-isolation-own",
    "d3b-isolation-other",
    "d3b-test-watermark",
    "com.example.app",
    "web-client-id",
    "google-web-client-id",
    "Example Manufacturer",
    "Example Device",
    "encrypted-email",
    "encrypted-password",
    "encrypted-refresh-token",
    "raw payload",
)
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
        unit=CANONICAL_NUTRITION_METRICS[metric_key].canonical_unit,
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
            **(fact_overrides if metric_key == "dietary_energy_kcal" else {}),
        )
        for metric_key in DAILY_PROJECTION_METRICS
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


def test_google_activity_samples_are_connection_scoped_in_both_parity_loaders(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = GoogleHealthConnection(
        client_id="daily-parity-client",
        encrypted_client_secret=encrypt_credential("daily-parity-client-secret"),
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        state="active",
        granted_scopes=["https://www.googleapis.com/auth/fitness.activity.read"],
    )
    db.add(connection)
    db.flush()
    target = NutritionTarget(
        user_id=user.id,
        valid_from=LOCAL_DATE,
        calories_kcal=Decimal("2000"),
        protein_g=Decimal("120"),
        activity_mode="full",
        activity_source_type=GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE,
    )
    db.add(target)
    batch = _range_batch(db, user)
    for source_identifier, value, suffix in (
        ("wrong-connection", "999", "wrong-connection"),
        (str(connection.id), "250", "matching-connection"),
    ):
        at = datetime.combine(LOCAL_DATE, datetime.min.time(), tzinfo=UTC)
        db.add(
            HealthSample(
                user_id=user.id,
                import_batch_id=batch.id,
                external_sample_id=f"{suffix}-external",
                fingerprint=f"{suffix}-fingerprint",
                source_type=GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE,
                source_name="Google Health Connect",
                source_identifier=source_identifier,
                metric_type=ACTIVE_ENERGY_METRIC,
                value=Decimal(value),
                unit="kcal",
                original_value=Decimal(value),
                original_unit="kcal",
                start_at=at,
                end_at=at,
                local_date=LOCAL_DATE,
                timezone="UTC",
            )
        )
    db.commit()
    projection_day = _projection_day()
    monkeypatch.setattr(
        "app.analytics.daily_point_parity.read_canonical_nutrition_day",
        lambda db, user_id, local_date: projection_day,
    )

    canonical = _build_canonical_daily_point(db, user.id, LOCAL_DATE)
    (
        _totals,
        _counts,
        active_by_source,
        _active_sources,
        _legacy,
        _targets,
        _overrides,
    ) = _read_daily_point_range_inputs(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
    )

    assert canonical.point is not None
    assert canonical.point.active_energy_kcal == Decimal("250")
    assert active_by_source[(LOCAL_DATE, GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE)] == Decimal("250")



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
        lambda db, user_id, local_date: (_ for _ in ()).throw(RuntimeError("database unavailable")),
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
        deviation_kcal=(None if calories is None or target is None else calories - target),
        activity_mode=activity_mode,
        activity_source_type=activity_source,
        active_energy_kcal=active_energy,
        activity_credit_kcal=activity_credit,
        activity_data_status="credited" if active_energy is not None else "missing",
        effective_budget_kcal=(None if target is None else target + activity_credit),
        effective_maintenance_kcal=(None if maintenance is None else maintenance + activity_credit),
        effective_deviation_kcal=(
            None if calories is None or target is None else calories - target - activity_credit
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
                calorie_legacy_value if metric_key == "dietary_energy_kcal" else Decimal("1")
            ),
            legacy_present=(
                calorie_legacy_present if metric_key == "dietary_energy_kcal" else True
            ),
            projection_value=(
                calorie_projection_value if metric_key == "dietary_energy_kcal" else Decimal("1")
            ),
            projection_present=(
                calorie_projection_present if metric_key == "dietary_energy_kcal" else True
            ),
            projection_provider_key="yazio",
            projection_coverage_state=(
                calorie_coverage if metric_key == "dietary_energy_kcal" else CoverageState.COMPLETE
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
            metric.classification is NutritionParityClassification.VALUE_MISMATCH
            for metric in metrics
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
    assert (
        result.tracking.classification
        is DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE
    )
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

    assert (
        result.classification is DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    )
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
    ("coverage", "resolution", "expected"),
    (
        (
            CoverageState.PARTIAL,
            ResolutionState.RESOLVED,
            DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE,
        ),
        (
            CoverageState.COMPLETE,
            ResolutionState.UNRESOLVED,
            DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE,
        ),
        (
            CoverageState.UNKNOWN,
            ResolutionState.RESOLVED,
            DailyPointParityClassification.UNEXPLAINED_MISMATCH,
        ),
        (
            CoverageState.COMPLETE,
            ResolutionState.CONFLICT,
            DailyPointParityClassification.UNEXPLAINED_MISMATCH,
        ),
        (
            CoverageState.COMPLETE,
            ResolutionState.DUPLICATE_CANDIDATE,
            DailyPointParityClassification.UNEXPLAINED_MISMATCH,
        ),
    ),
)
def test_canonical_quality_difference_explains_tracking_quality_only(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    coverage: CoverageState,
    resolution: ResolutionState,
    expected: DailyPointParityClassification,
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

    assert result.classification is expected
    assert result.tracking.classification is expected
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


def test_target_mismatch_prevents_calorie_explanation_for_deviation(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(calories=Decimal("1800"), target=Decimal("2100"))
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(NutritionParityClassification.LEGACY_MULTI_SOURCE),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    by_field = {difference.field_name: difference for difference in result.fields.differences}
    assert by_field["calories_kcal"].classification is (
        DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    )
    assert by_field["deviation_kcal"].classification is (
        DailyPointParityClassification.UNEXPLAINED_MISMATCH
    )
    assert by_field["effective_deviation_kcal"].classification is (
        DailyPointParityClassification.UNEXPLAINED_MISMATCH
    )


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
    assert {difference.field_name for difference in result.fields.differences} >= {
        "target_kcal",
        "activity_source_type",
    }
    assert all(
        difference.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
        for difference in result.fields.differences
    )


def test_explicit_zero_score_difference_remains_expected_when_override_masks_status(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point(
        calories=None,
        tracking_status="probably_incomplete",
        tracking_score=0,
    )
    canonical = _daily_point(
        calories=Decimal("0"),
        tracking_status="probably_incomplete",
        tracking_score=1,
    )
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

    assert result.tracking.classification is (
        DailyPointParityClassification.EXPLICIT_ZERO_SEMANTIC_DIFFERENCE
    )


def test_quality_score_difference_remains_expected_when_override_masks_status(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point(tracking_status="probably_complete", tracking_score=1)
    canonical = _daily_point(tracking_status="probably_complete", tracking_score=0)
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(calorie_coverage=CoverageState.PARTIAL),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.tracking.classification is (
        DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE
    )


def test_multi_source_quality_combination_keeps_tracking_quality_narrow(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(
        calories=Decimal("1800"),
        tracking_status="incomplete",
        tracking_score=0,
    )
    _stub_daily_point_comparison(
        monkeypatch,
        legacy=legacy,
        canonical=canonical,
        nutrition=_nutrition_day(
            NutritionParityClassification.LEGACY_MULTI_SOURCE,
            calorie_coverage=CoverageState.PARTIAL,
        ),
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.tracking.classification is (
        DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE
    )
    by_field = {difference.field_name: difference for difference in result.fields.differences}
    assert by_field["calories_kcal"].classification is (
        DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    )
    assert result.classification is DailyPointParityClassification.CANONICAL_QUALITY_DIFFERENCE


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


def test_daily_point_range_returns_inclusive_immutable_results(db: Session, user: User) -> None:
    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE + timedelta(days=1),
    )

    assert isinstance(result, DailyPointRangeParity)
    assert tuple(day.local_date for day in result.days) == (
        LOCAL_DATE,
        LOCAL_DATE + timedelta(days=1),
    )
    assert result.days_compared == 0
    assert result.days_not_comparable == 2


def _range_batch(db: Session, user: User) -> ImportBatch:
    batch = ImportBatch(user_id=user.id, source_type="range-test", status="completed")
    db.add(batch)
    db.flush()
    return batch


def _range_sample(
    db: Session,
    *,
    user: User,
    batch: ImportBatch,
    local_date: date,
    metric_type: str,
    value: str,
    suffix: str,
    source_type: str = "range-test",
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


def _not_projected_day(user_id, local_date: date) -> CanonicalNutritionDay:
    return CanonicalNutritionDay(
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
    )


def _stub_range_projection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_id,
    projection_days: dict[date, CanonicalNutritionDay],
    nutrition_days: dict[date, NutritionDayParity],
) -> None:
    monkeypatch.setattr(
        parity_module,
        "read_canonical_nutrition_day",
        lambda db, requested_user_id, local_date: projection_days.get(
            local_date, _not_projected_day(requested_user_id, local_date)
        ),
    )
    monkeypatch.setattr(
        parity_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, user_id: nutrition_days[legacy_day.local_date],
    )


def test_daily_point_range_aggregates_expected_tracking_differences(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    next_date = LOCAL_DATE + timedelta(days=1)
    batch = _range_batch(db, user)
    _range_sample(
        db,
        user=user,
        batch=batch,
        local_date=LOCAL_DATE,
        metric_type="dietary_energy_kcal",
        value="0",
        suffix="range-zero-calories",
    )
    _range_sample(
        db,
        user=user,
        batch=batch,
        local_date=next_date,
        metric_type="dietary_energy_kcal",
        value="1900",
        suffix="range-quality-calories",
    )
    db.commit()
    zero_projection = _projection_day(
        values={
            "dietary_energy_kcal": Decimal("0"),
            "protein_g": None,
            "carbohydrates_g": None,
            "fat_g": None,
        }
    )
    quality_projection = _projection_day(
        calorie_coverage_complete=False,
        calorie_resolution_resolved=True,
        calorie_usable=False,
    )
    _stub_range_projection(
        monkeypatch,
        user_id=user.id,
        projection_days={LOCAL_DATE: zero_projection, next_date: quality_projection},
        nutrition_days={
            LOCAL_DATE: _nutrition_day(
                calorie_legacy_value=Decimal("0"),
                calorie_projection_value=Decimal("0"),
            ),
            next_date: _nutrition_day(calorie_coverage=CoverageState.PARTIAL),
        },
    )

    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=next_date,
    )

    assert result.days_compared == 2
    assert result.days_not_comparable == 0
    assert result.status_matches == 0
    assert result.expected_tracking_differences == 2
    assert result.unexplained_tracking_mismatches == 0
    assert tuple(day.local_date for day in result.days) == (LOCAL_DATE, next_date)
    with pytest.raises(FrozenInstanceError):
        result.status_matches = 0  # type: ignore[misc]


def test_daily_point_range_bundles_legacy_reads_and_does_not_call_daily_points(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original_scalars = Session.scalars

    def scalars_spy(self, statement, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original_scalars(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalars", scalars_spy)
    monkeypatch.setattr(
        parity_module,
        "daily_points",
        lambda *args, **kwargs: pytest.fail("range must not call daily_points"),
    )
    _stub_range_projection(monkeypatch, user_id=user.id, projection_days={}, nutrition_days={})
    monkeypatch.setattr(
        parity_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, user_id: NutritionDayParity(
            local_date=legacy_day.local_date,
            projection_state=NutritionProjectionReadState.NOT_PROJECTED,
            metrics=(),
            match_count=0,
            mismatch_count=0,
            expected_difference_count=0,
            comparable=False,
        ),
    )

    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE + timedelta(days=2),
    )

    assert len(result.days) == 3
    assert calls == 3


def test_daily_point_range_rejects_reversed_and_oversized_ranges() -> None:
    with pytest.raises(ValueError):
        compare_daily_point_range(
            object(),  # type: ignore[arg-type]
            user_id=object(),  # type: ignore[arg-type]
            start=LOCAL_DATE + timedelta(days=1),
            end=LOCAL_DATE,
        )
    with pytest.raises(ValueError):
        compare_daily_point_range(
            object(),  # type: ignore[arg-type]
            user_id=object(),  # type: ignore[arg-type]
            start=LOCAL_DATE,
            end=LOCAL_DATE + timedelta(days=MAX_PARITY_DAYS),
        )


def test_daily_point_range_accepts_the_366_day_inclusive_limit(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    end = LOCAL_DATE + timedelta(days=MAX_PARITY_DAYS - 1)
    _stub_range_projection(monkeypatch, user_id=user.id, projection_days={}, nutrition_days={})
    monkeypatch.setattr(
        parity_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, user_id: NutritionDayParity(
            local_date=legacy_day.local_date,
            projection_state=NutritionProjectionReadState.NOT_PROJECTED,
            metrics=(),
            match_count=0,
            mismatch_count=0,
            expected_difference_count=0,
            comparable=False,
        ),
    )

    result = compare_daily_point_range(db, user_id=user.id, start=LOCAL_DATE, end=end)

    assert len(result.days) == MAX_PARITY_DAYS
    assert result.days[0].local_date == LOCAL_DATE
    assert result.days[-1].local_date == end


def test_daily_point_range_counts_unexpected_target_activity_mismatch(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(target=Decimal("2100"))
    _stub_range_projection(monkeypatch, user_id=user.id, projection_days={}, nutrition_days={})
    monkeypatch.setattr(
        parity_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, user_id: _nutrition_day(),
    )
    monkeypatch.setattr(
        parity_module,
        "_build_daily_point",
        lambda **kwargs: legacy,
    )
    monkeypatch.setattr(
        parity_module,
        "_build_canonical_daily_point_with_inputs",
        lambda *args, **kwargs: CanonicalDailyPointResult(
            state=CanonicalDailyPointResultState.READY,
            point=canonical,
        ),
    )

    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
    )

    assert result.days_compared == 1
    assert result.unexpected_target_activity_mismatches == 1
    assert result.unexplained_tracking_mismatches == 0


def test_daily_point_range_isolates_legacy_inputs_by_user(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = User(username="range-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    other_batch = _range_batch(db, other)
    _range_sample(
        db,
        user=other,
        batch=other_batch,
        local_date=LOCAL_DATE,
        metric_type="dietary_energy_kcal",
        value="1900",
        suffix="range-other-calories",
    )
    db.commit()
    _stub_range_projection(monkeypatch, user_id=user.id, projection_days={}, nutrition_days={})
    monkeypatch.setattr(
        parity_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, user_id: NutritionDayParity(
            local_date=legacy_day.local_date,
            projection_state=NutritionProjectionReadState.NOT_PROJECTED,
            metrics=(),
            match_count=0,
            mismatch_count=0,
            expected_difference_count=0,
            comparable=False,
        ),
    )

    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
    )

    assert result.days[0].tracking.legacy_status == "no_data"


def test_daily_point_range_counts_unexplained_tracking_mismatch(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _daily_point()
    canonical = _daily_point(tracking_status="probably_complete")
    _stub_range_projection(monkeypatch, user_id=user.id, projection_days={}, nutrition_days={})
    monkeypatch.setattr(
        parity_module,
        "_compare_nutrition_day_with_legacy",
        lambda db, legacy_day, user_id: _nutrition_day(),
    )
    monkeypatch.setattr(parity_module, "_build_daily_point", lambda **kwargs: legacy)
    monkeypatch.setattr(
        parity_module,
        "_build_canonical_daily_point_with_inputs",
        lambda *args, **kwargs: CanonicalDailyPointResult(
            state=CanonicalDailyPointResultState.READY,
            point=canonical,
        ),
    )

    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
    )

    assert result.unexplained_tracking_mismatches == 1


def test_daily_point_range_preserves_activity_source_asymmetry_and_missing_state(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = NutritionTarget(
        user_id=user.id,
        valid_from=LOCAL_DATE,
        calories_kcal=Decimal("2000"),
        protein_g=Decimal("120"),
        activity_mode="full",
        activity_source_type="apple_health_xml",
    )
    db.add(target)
    batch = _range_batch(db, user)
    _range_sample(
        db,
        user=user,
        batch=batch,
        local_date=LOCAL_DATE,
        metric_type="dietary_energy_kcal",
        value="1900",
        suffix="range-missing-activity-calories",
    )
    _range_sample(
        db,
        user=user,
        batch=batch,
        local_date=LOCAL_DATE,
        metric_type="active_energy_burned",
        value="300",
        source_type="google",
        suffix="range-missing-activity-google",
    )
    db.commit()
    _stub_range_projection(
        monkeypatch,
        user_id=user.id,
        projection_days={LOCAL_DATE: _projection_day()},
        nutrition_days={LOCAL_DATE: _nutrition_day()},
    )

    result = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
    )

    day = result.days[0]
    assert day.tracking.classification is DailyPointParityClassification.MATCH
    assert day.fields.comparable is True
    assert {
        difference.field_name
        for difference in day.fields.differences
        if difference.field_name
        in {"activity_mode", "activity_source_type", "active_energy_kcal", "activity_data_status"}
    } == set()


def _d3b_values(base: str = "2100") -> dict[str, Decimal]:
    return {
        "dietary_energy_kcal": Decimal(base),
        "protein_g": Decimal("120"),
        "carbohydrates_g": Decimal("230"),
        "fat_g": Decimal("70"),
        "fiber_g": Decimal("30"),
        "sugar_g": Decimal("50"),
        "saturated_fat_g": Decimal("20"),
    }


def _d3b_sample(
    db: Session,
    *,
    user: User,
    batch: ImportBatch,
    metric_type: str,
    value: Decimal,
    source_type: str,
    suffix: str,
) -> None:
    at = datetime.combine(LOCAL_DATE, datetime.min.time(), tzinfo=UTC)
    unit = "kcal" if metric_type == "dietary_energy_kcal" else "g"
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
            value=value,
            unit=unit,
            original_value=value,
            original_unit=unit,
            start_at=at,
            end_at=at,
            local_date=LOCAL_DATE,
            timezone="UTC",
        )
    )


def _d3b_legacy_day(
    db: Session,
    user: User,
    values: dict[str, Decimal],
    *,
    source_type: str,
    suffix: str,
) -> None:
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    for metric_key, value in values.items():
        _d3b_sample(
            db,
            user=user,
            batch=batch,
            metric_type=metric_key,
            value=value,
            source_type=source_type,
            suffix=f"{suffix}-{metric_key}",
        )
    db.flush()


def _d3b_yazio_connection(db: Session, user: User) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="d3b-yazio-source",
    )
    db.add(connection)
    db.flush()
    return connection


def _d3b_yazio_diary(values: dict[str, Decimal]) -> YazioFoodDiary:
    return YazioFoodDiary(
        requested_start_day=LOCAL_DATE,
        requested_end_day=LOCAL_DATE,
        consumed_products=(),
        consumed_simple_products=(),
        product_profiles=(),
        daily_summaries=(
            YazioDailyNutrientSummary(
                local_date=LOCAL_DATE,
                nutrients=YazioNutrientValues(
                    energy=values["dietary_energy_kcal"],
                    protein=values["protein_g"],
                    carb=values["carbohydrates_g"],
                    fat=values["fat_g"],
                    fiber=values["fiber_g"],
                    sugar=values["sugar_g"],
                    saturated_fat=values["saturated_fat_g"],
                ),
                energy_goal=None,
            ),
        ),
    )


def _d3b_google_connection(db: Session, user: User) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        client_id="daily-parity-client",
        encrypted_client_secret=encrypt_credential("daily-parity-client-secret"),
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=["https://www.googleapis.com/auth/googlehealth.nutrition.readonly"],
        state="active",
    )
    db.add(connection)
    db.flush()
    return connection


def _d3b_google_point(values: dict[str, Decimal]) -> NutritionLogDataPoint:
    start = datetime(2024, 1, 2, 10, tzinfo=UTC)
    interval = NutritionLogInterval(
        start_time=start,
        end_time=datetime(2024, 1, 2, 10, 30, tzinfo=UTC),
        start_utc_offset="+01:00",
        end_utc_offset="+01:00",
        civil_start_time=datetime(2024, 1, 2, 11),
        civil_end_time=datetime(2024, 1, 2, 11, 30),
    )
    return NutritionLogDataPoint(
        name="d3b-google-day",
        nutrition_log=NutritionLog(
            interval=interval,
            nutrients=(
                NutritionNutrient("PROTEIN", NutritionQuantity(values["protein_g"], "g")),
                NutritionNutrient("DIETARY_FIBER", NutritionQuantity(values["fiber_g"], "g")),
                NutritionNutrient("SUGAR", NutritionQuantity(values["sugar_g"], "g")),
                NutritionNutrient(
                    "SATURATED_FAT", NutritionQuantity(values["saturated_fat_g"], "g")
                ),
            ),
            energy=NutritionQuantity(values["dietary_energy_kcal"], "kcal"),
            total_carbohydrate=NutritionQuantity(values["carbohydrates_g"], "g"),
            total_fat=NutritionQuantity(values["fat_g"], "g"),
        ),
        data_source=NutritionDataSource(
            recording_method="manual",
            platform="android",
            device=NutritionDataSourceDevice(
                form_factor="phone",
                manufacturer="Example Manufacturer",
                display_name="Example Device",
            ),
            application=NutritionDataSourceApplication(
                package_name="com.example.app",
                web_client_id="web-client-id",
                google_web_client_id="google-web-client-id",
            ),
        ),
    )


def _d3b_projection(
    db: Session,
    user: User,
    values: dict[str, Decimal | None],
    *,
    provider_key: str = "yazio",
) -> NutritionDailyProjection:
    policy = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2023, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, provider_key, 1),),
    )
    projection = create_projection(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark="d3b-test-watermark",
        projection_status=ProjectionStatus.READY.value,
    )
    for metric_key in DAILY_PROJECTION_METRICS:
        definition = CANONICAL_NUTRITION_METRICS[metric_key]
        value = values.get(metric_key)
        create_projection_fact(
            db,
            user_id=user.id,
            projection_id=projection.id,
            metric_key=metric_key,
            value=value,
            unit=definition.canonical_unit,
            selected_provider_key=provider_key if value is not None else None,
            selected_granularity=(
                ProjectionGranularity.SUMMARY.value if value is not None else None
            ),
            presence_state=(
                PresenceState.SUPPLIED.value if value is not None else PresenceState.UNKNOWN.value
            ),
            coverage_state=(
                CoverageState.COMPLETE.value if value is not None else CoverageState.UNKNOWN.value
            ),
            resolution_state=(
                ResolutionState.RESOLVED.value
                if value is not None
                else ResolutionState.UNRESOLVED.value
            ),
            lineage_state=(
                LineageState.CONFIRMED.value if value is not None else LineageState.UNKNOWN.value
            ),
        )
    set_projection_head(db, user.id, LOCAL_DATE, projection.id)
    db.commit()
    return projection


def _d3b_persisted_snapshot(db: Session) -> tuple[tuple[tuple[object, ...], ...], ...]:
    models = (
        HealthSample,
        NutritionDailyProjection,
        NutritionDailyProjectionFact,
        NutritionProjectionHead,
    )
    return tuple(
        tuple(
            tuple(getattr(row, column.key) for column in model.__table__.columns)
            for row in db.scalars(select(model).order_by(*model.__table__.primary_key.columns))
        )
        for model in models
    )


def _assert_identifier_free_daily_point_result(result: DailyPointParity) -> None:
    allowed_fields = {
        DailyPointTrackingParity: {
            "local_date",
            "legacy_status",
            "canonical_status",
            "legacy_score",
            "canonical_score",
            "legacy_reasons",
            "canonical_reasons",
            "comparable",
            "classification",
        },
        DailyPointFieldDifference: {
            "field_name",
            "legacy_value",
            "canonical_value",
            "classification",
        },
        DailyPointFieldParity: {"local_date", "differences", "comparable", "classification"},
        DailyPointParity: {
            "local_date",
            "tracking",
            "fields",
            "nutrition",
            "comparable",
            "classification",
        },
        NutritionDayParity: {
            "local_date",
            "projection_state",
            "metrics",
            "match_count",
            "mismatch_count",
            "expected_difference_count",
            "comparable",
        },
        NutritionMetricParity: {
            "metric_key",
            "legacy_value",
            "legacy_present",
            "projection_value",
            "projection_present",
            "projection_provider_key",
            "projection_coverage_state",
            "projection_resolution_state",
            "projection_lineage_state",
            "classification",
        },
    }
    for contract, expected_fields in allowed_fields.items():
        field_names = {field.name for field in fields(contract)}
        assert field_names <= expected_fields
        assert not any(
            field_name in {"id", "identifier", "source_identifier", "payload"}
            or field_name.endswith("_id")
            or "identifier" in field_name
            or "payload" in field_name
            for field_name in field_names
        )
    assert isinstance(result.tracking.legacy_reasons, tuple)
    assert isinstance(result.tracking.canonical_reasons, tuple)
    assert isinstance(result.fields.differences, tuple)
    assert isinstance(result.nutrition.metrics, tuple)
    rendered = repr(result)
    for marker in _D3B_PRIVACY_MARKERS:
        assert marker not in rendered


def _run_d3b_yazio_fixture(
    db: Session, user: User
) -> tuple[
    DailyPointParity,
    tuple[tuple[tuple[object, ...], ...], ...],
    tuple[tuple[tuple[object, ...], ...], ...],
]:
    values = _d3b_values()
    connection = _d3b_yazio_connection(db, user)
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=LOCAL_DATE,
        requested_end=LOCAL_DATE,
        diary=_d3b_yazio_diary(values),
    )
    _d3b_legacy_day(
        db,
        user,
        values,
        source_type="yazio_export_v1",
        suffix="d3b-yazio",
    )
    _d3b_legacy_day(
        db,
        user,
        values,
        source_type="google",
        suffix="d3b-google-legacy",
    )
    create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2023, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()
    projection_result = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        policy_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
    )
    assert projection_result.status is ProjectionPersistenceStatus.CREATED
    before_snapshot = _d3b_persisted_snapshot(db)
    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)
    after_snapshot = _d3b_persisted_snapshot(db)
    return result, before_snapshot, after_snapshot


def test_daily_point_yazio_multi_source_explanation_is_read_only_and_safe(
    db: Session, user: User
) -> None:
    result, before_snapshot, after_snapshot = _run_d3b_yazio_fixture(db, user)

    assert result.comparable is True
    assert (
        result.classification is DailyPointParityClassification.NUTRITION_VALUE_SEMANTIC_DIFFERENCE
    )
    assert result.tracking.classification is DailyPointParityClassification.MATCH
    assert result.nutrition.comparable is True
    assert result.nutrition.expected_difference_count == len(DAILY_PROJECTION_METRICS)
    calorie = next(
        metric for metric in result.nutrition.metrics if metric.metric_key == "dietary_energy_kcal"
    )
    assert calorie.classification is NutritionParityClassification.LEGACY_MULTI_SOURCE
    assert calorie.projection_provider_key == "yazio"
    assert calorie.legacy_value == Decimal("4200")
    assert calorie.projection_value == Decimal("2100")
    assert before_snapshot == after_snapshot
    _assert_identifier_free_daily_point_result(result)


def test_daily_point_google_only_projection_is_projection_only(db: Session, user: User) -> None:
    values = _d3b_values()
    connection = _d3b_google_connection(db, user)
    ingest_google_health_nutrition_logs(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=LOCAL_DATE,
        requested_end=LOCAL_DATE,
        data_points=(_d3b_google_point(values),),
    )
    create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2023, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, "google_health", 1),),
    )
    db.commit()
    projection_result = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=LOCAL_DATE,
        policy_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
    )
    assert projection_result.status is ProjectionPersistenceStatus.CREATED

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.comparable is True
    assert result.nutrition.projection_state is NutritionProjectionReadState.READY
    assert result.nutrition.match_count == 0
    assert result.nutrition.mismatch_count == len(DAILY_PROJECTION_METRICS)
    assert all(
        metric.classification is NutritionParityClassification.PROJECTION_ONLY
        and metric.legacy_present is False
        and metric.projection_present is True
        and metric.projection_provider_key == "google_health"
        for metric in result.nutrition.metrics
    )
    assert result.classification is DailyPointParityClassification.UNEXPLAINED_MISMATCH
    _assert_identifier_free_daily_point_result(result)


def test_daily_point_apple_legacy_only_is_not_comparable_without_reason_leak(
    db: Session, user: User
) -> None:
    values = _d3b_values()
    _d3b_legacy_day(
        db,
        user,
        values,
        source_type="apple_health_xml",
        suffix="d3b-apple-only",
    )
    db.commit()

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    assert result.comparable is False
    assert result.classification is DailyPointParityClassification.NOT_COMPARABLE
    assert result.tracking.canonical_status is None
    assert result.tracking.canonical_reasons == ()
    assert result.nutrition.projection_state is NutritionProjectionReadState.NOT_PROJECTED
    assert len(result.nutrition.metrics) == len(DAILY_PROJECTION_METRICS)
    assert tuple(metric.metric_key for metric in result.nutrition.metrics) == tuple(
        DAILY_PROJECTION_METRICS
    )
    assert tuple(metric.legacy_value for metric in result.nutrition.metrics) == tuple(
        values[metric_key] for metric_key in DAILY_PROJECTION_METRICS
    )
    assert all(
        metric.classification is NutritionParityClassification.NOT_PROJECTED
        and metric.legacy_present is True
        for metric in result.nutrition.metrics
    )
    _assert_identifier_free_daily_point_result(result)


def test_daily_point_decimal_precision_preserves_decimal_and_tolerance(
    db: Session, user: User
) -> None:
    legacy_value = Decimal("123.456789")
    _d3b_legacy_day(
        db,
        user,
        {"dietary_energy_kcal": legacy_value},
        source_type="yazio_export_v1",
        suffix="d3b-precision-within",
    )
    _d3b_projection(
        db,
        user,
        {
            metric_key: (
                Decimal("123.456789000001") if metric_key == "dietary_energy_kcal" else None
            )
            for metric_key in DAILY_PROJECTION_METRICS
        },
    )
    within = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)
    within_metric = next(
        metric for metric in within.nutrition.metrics if metric.metric_key == "dietary_energy_kcal"
    )

    outside_user = User(username="d3b-precision-outside", password_hash="synthetic-password-hash")
    db.add(outside_user)
    db.flush()
    _d3b_legacy_day(
        db,
        outside_user,
        {"dietary_energy_kcal": legacy_value},
        source_type="yazio_export_v1",
        suffix="d3b-precision-outside",
    )
    _d3b_projection(
        db,
        outside_user,
        {
            metric_key: (
                Decimal("123.456789000003") if metric_key == "dietary_energy_kcal" else None
            )
            for metric_key in DAILY_PROJECTION_METRICS
        },
    )
    outside = compare_daily_point(db, user_id=outside_user.id, local_date=LOCAL_DATE)
    outside_metric = next(
        metric for metric in outside.nutrition.metrics if metric.metric_key == "dietary_energy_kcal"
    )

    assert type(within_metric.legacy_value) is Decimal
    assert type(within_metric.projection_value) is Decimal
    assert within_metric.classification is NutritionParityClassification.MATCH
    assert type(outside_metric.legacy_value) is Decimal
    assert type(outside_metric.projection_value) is Decimal
    assert outside_metric.classification is NutritionParityClassification.VALUE_MISMATCH
    assert abs(outside_metric.legacy_value - outside_metric.projection_value) == Decimal(
        "0.000000000003"
    )
    _assert_identifier_free_daily_point_result(within)
    _assert_identifier_free_daily_point_result(outside)


def test_daily_point_result_is_user_scoped_and_immutable(db: Session, user: User) -> None:
    own_values = _d3b_values()
    other_values = _d3b_values("999")
    other = User(username="d3b-isolation-other", password_hash="synthetic-password-hash")
    db.add(other)
    db.flush()
    _d3b_legacy_day(
        db,
        user,
        own_values,
        source_type="yazio_export_v1",
        suffix="d3b-isolation-own",
    )
    _d3b_legacy_day(
        db,
        other,
        other_values,
        source_type="yazio_export_v1",
        suffix="d3b-isolation-other",
    )
    _d3b_projection(
        db,
        other,
        {metric_key: value for metric_key, value in other_values.items()},
    )
    _d3b_projection(
        db,
        user,
        {metric_key: value for metric_key, value in own_values.items()},
    )

    result = compare_daily_point(db, user_id=user.id, local_date=LOCAL_DATE)

    calorie = next(
        metric for metric in result.nutrition.metrics if metric.metric_key == "dietary_energy_kcal"
    )
    assert calorie.legacy_value == own_values["dietary_energy_kcal"]
    assert calorie.projection_value == own_values["dietary_energy_kcal"]
    assert result.classification is DailyPointParityClassification.MATCH
    with pytest.raises(FrozenInstanceError):
        result.local_date = LOCAL_DATE + timedelta(days=1)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.tracking.legacy_status = "changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.fields.differences.append(object())  # type: ignore[attr-defined]
    _assert_identifier_free_daily_point_result(result)


def test_daily_endpoint_fails_closed_for_unavailable_provider_with_ready_projection(
    db: Session,
    user: User,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _d3b_values()
    _d3b_legacy_day(
        db,
        user,
        values,
        source_type="yazio_export_v1",
        suffix="d4b-endpoint",
    )
    _d3b_projection(db, user, values)
    db.add(
        UserProviderPriority(
            user_id=user.id,
            data_area="nutrition",
            priority=1,
            provider_key="yazio",
        )
    )
    db.commit()

    parity = compare_daily_point_range(
        db,
        user_id=user.id,
        start=LOCAL_DATE,
        end=LOCAL_DATE,
        max_days=31,
    )
    assert parity.days[0].classification is DailyPointParityClassification.MATCH
    assert len(parity.canonical_points) == 1
    assert parity.canonical_points[0].model_dump(mode="json") == daily_points(
        db, user, LOCAL_DATE, LOCAL_DATE
    )[0].model_dump(mode="json")

    monkeypatch.setattr(settings, "analytics_daily_canonical_read_enabled", False, raising=False)
    monkeypatch.setattr(settings, "analytics_daily_shadow_read_enabled", False)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    legacy_response = client.get(
        f"/api/v1/analytics/daily?start={LOCAL_DATE.isoformat()}&end={LOCAL_DATE.isoformat()}"
    )
    assert legacy_response.status_code == 503

    monkeypatch.setattr(settings, "analytics_daily_canonical_read_enabled", True)
    canonical_response = client.get(
        f"/api/v1/analytics/daily?start={LOCAL_DATE.isoformat()}&end={LOCAL_DATE.isoformat()}"
    )

    assert canonical_response.status_code == 503
