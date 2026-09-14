from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.analytics.daily_point_parity import (
    CanonicalDailyPointReason,
    CanonicalDailyPointResultState,
    _build_canonical_daily_point,
)
from app.analytics.nutrition_projection import (
    CanonicalNutritionDay,
    CanonicalNutritionFact,
    NutritionProjectionReadError,
    NutritionProjectionReadState,
)
from app.models import TrackingOverride, User
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
