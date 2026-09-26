from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.analytics import nutrition_micronutrients
from app.analytics.provider_selection import NutritionProviderSelection
from app.micronutrients import MICRONUTRIENTS
from app.models import User, YazioConnection
from app.nutrition.enums import CoverageState, PresenceState, ResolutionState
from app.nutrition.resolution import DAILY_PROJECTION_METRICS
from app.problem_types import PROVIDER_SELECTION_UNAVAILABLE

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_A = UUID("22222222-2222-2222-2222-222222222222")
SOURCE_B = UUID("33333333-3333-3333-3333-333333333333")
DAY = date(2026, 3, 28)


def test_analysis_response_serializes_decimal_measurements_as_json_numbers() -> None:
    from app.schemas import (
        AnalysisMicronutrientDayResponse,
        AnalysisMicronutrientMetricResponse,
        AnalysisMicronutrientResponse,
        AnalysisSelectedProviderResponse,
    )

    response = AnalysisMicronutrientResponse(
        start_date=DAY,
        end_date=DAY,
        selected_provider=AnalysisSelectedProviderResponse(provider_key="yazio"),
        period_days=1,
        metrics=[
            AnalysisMicronutrientMetricResponse(
                metric_key="vitamin_a_ug",
                label="Vitamin A",
                category="vitamin",
                unit="ug",
                eu_nrv=Decimal("800"),
                total=Decimal("0.04"),
                days_with_data=1,
                contributors=[],
            ),
        ],
        series=[
            AnalysisMicronutrientDayResponse(
                date=DAY, provider_key="yazio", value=Decimal("0.04")
            ),
        ],
    )

    payload = response.model_dump(mode="json")

    assert payload["metrics"][0]["eu_nrv"] == 800
    assert type(payload["metrics"][0]["eu_nrv"]) in {int, float}
    assert payload["metrics"][0]["total"] == 0.04
    assert type(payload["metrics"][0]["total"]) in {int, float}
    assert payload["series"][0]["value"] == 0.04
    assert type(payload["series"][0]["value"]) in {int, float}


def _candidate(
    metric_key: str,
    provider_key: str,
    *,
    local_date: date = DAY,
    value: Decimal | None = None,
    presence: PresenceState = PresenceState.MISSING,
    coverage: CoverageState = CoverageState.COMPLETE,
    resolution: ResolutionState = ResolutionState.RESOLVED,
) -> SimpleNamespace:
    return SimpleNamespace(
        provider_key=provider_key,
        user_id=USER_ID,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        presence_state=presence,
        coverage_state=coverage,
        resolution_state=resolution,
    )


def _matrix(
    provider_key: str,
    *,
    local_date: date = DAY,
    macro_coverage: CoverageState = CoverageState.COMPLETE,
    macro_evidence: bool = True,
    nutrient_value: Decimal | None = None,
    nutrient_presence: PresenceState = PresenceState.MISSING,
    nutrient_coverage: CoverageState = CoverageState.COMPLETE,
    nutrient_resolution: ResolutionState = ResolutionState.RESOLVED,
) -> dict[str, SimpleNamespace]:

    metric_keys = (*DAILY_PROJECTION_METRICS, *(item.metric_type for item in MICRONUTRIENTS))
    return {
        key: _candidate(
            key,
            provider_key,
            local_date=local_date,
            value=(Decimal("100") if macro_evidence else None)
            if key in DAILY_PROJECTION_METRICS
            else nutrient_value,
            presence=(PresenceState.SUPPLIED if macro_evidence else PresenceState.MISSING)
            if key in DAILY_PROJECTION_METRICS
            else nutrient_presence,
            coverage=macro_coverage if key in DAILY_PROJECTION_METRICS else nutrient_coverage,
            resolution=ResolutionState.RESOLVED
            if key in DAILY_PROJECTION_METRICS
            else nutrient_resolution,
        )
        for key in metric_keys
    }


def test_day_winner_does_not_fill_missing_vitamin_from_lower_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = NutritionProviderSelection(
        "google_health", SOURCE_A, (("google_health", SOURCE_A), ("yazio", SOURCE_B))
    )
    dates = (DAY, DAY + timedelta(days=1))

    def fake_period(
        _db: object,
        *,
        provider_key: str,
        start: date,
        end: date,
        **_kwargs: object,
    ) -> dict[date, dict[str, SimpleNamespace]]:
        if provider_key == "google_health":
            assert start == DAY and end == dates[-1]
            return {
                DAY: _matrix("google_health", local_date=DAY),
                dates[1]: _matrix(
                    "google_health",
                    local_date=dates[1],
                    macro_coverage=CoverageState.PARTIAL,
                    nutrient_value=Decimal("9"),
                    nutrient_presence=PresenceState.SUPPLIED,
                ),
            }
        assert start == dates[1] and end == dates[1]
        return {
            dates[1]: _matrix(
                "yazio",
                local_date=dates[1],
                nutrient_value=Decimal("2"),
                nutrient_presence=PresenceState.SUPPLIED,
            ),
        }

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    result = nutrition_micronutrients.read_analysis_micronutrients(
        object(), user_id=USER_ID, selection=selection, start=DAY,
        end=dates[-1], metric_key="vitamin_a_ug",
    )

    assert result.selected_provider.provider_key == "google_health"
    assert [(point.date, point.provider_key, point.value) for point in result.series] == [
        (DAY, "google_health", None),
        (dates[1], "yazio", Decimal("2")),
    ]
    assert result.metrics[0].total == Decimal("2")
    assert result.metrics[0].days_with_data == 1
    assert [(item.provider_key, item.days_with_data) for item in result.metrics[0].contributors] == [
        ("yazio", 1),
    ]


def test_selected_day_preserves_explicit_zero_and_missing_as_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = (DAY, DAY + timedelta(days=1))
    selection = NutritionProviderSelection("yazio", SOURCE_B, (("yazio", SOURCE_B),))


    def fake_period(_db: object, *, provider_key: str, start: date, end: date,
                    metric_keys: tuple[str, ...], **_kwargs: object):
        assert provider_key == "yazio"
        assert start == DAY and end == dates[-1]
        rows = {
            dates[0]: _matrix("yazio", macro_evidence=False),
            dates[1]: _matrix("yazio", nutrient_value=Decimal("0"),
                              nutrient_presence=PresenceState.EXPLICIT_ZERO),
        }
        return {day: {key: row[key] for key in metric_keys} for day, row in rows.items()}

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    result = nutrition_micronutrients.read_analysis_micronutrients(
        object(), user_id=USER_ID, selection=selection, start=DAY,
        end=dates[-1], metric_key="vitamin_a_ug",
    )

    assert [(point.date, point.provider_key, point.value) for point in result.series] == [
        (DAY, "yazio", None), (dates[1], "yazio", Decimal("0")),
    ]
    assert result.metrics[0].total == Decimal("0")
    assert result.metrics[0].days_with_data == 1
    assert [(item.provider_key, item.days_with_data) for item in result.metrics[0].contributors] == [
        ("yazio", 1),
    ]


def test_reader_rejects_unsafe_candidates_and_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = NutritionProviderSelection("yazio", SOURCE_B, (("yazio", SOURCE_B),))

    def fake_period(_db: object, *, metric_keys: tuple[str, ...], **_kwargs: object):
        return {DAY: {
            key: _candidate(key, "yazio", value=Decimal("100"),
                            presence=PresenceState.SUPPLIED,
                            resolution=ResolutionState.CONFLICT if key == "vitamin_a_ug"
                            else ResolutionState.RESOLVED)
            for key in metric_keys
        }}

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(), user_id=USER_ID, selection=selection, start=DAY, end=DAY,
            metric_key="vitamin_a_ug",
        )

    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(), user_id=USER_ID, selection=selection, start=DAY,
            end=DAY + timedelta(days=31), metric_key="vitamin_a_ug",
        )
def test_reader_returns_empty_catalog_for_readable_chain_and_accepts_31_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = NutritionProviderSelection("yazio", SOURCE_B, (("yazio", SOURCE_B),))
    end = DAY + timedelta(days=30)

    def fake_period(
        _db: object,
        *,
        start: date,
        end: date,
        metric_keys: tuple[str, ...],
        **_kwargs: object,
    ) -> dict[date, dict[str, SimpleNamespace]]:
        return {
            current: {
                key: _matrix("yazio", local_date=current, macro_evidence=False)[key]
                for key in metric_keys
            }
            for current in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
        }

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    result = nutrition_micronutrients.read_analysis_micronutrients(
        object(), user_id=USER_ID, selection=selection, start=DAY, end=end, metric_key=None
    )

    assert result.period_days == 31
    assert len(result.metrics) == len(MICRONUTRIENTS)
    assert all(item.total is None and item.days_with_data == 0 for item in result.metrics)
    assert all(item.contributors == [] for item in result.metrics)
    assert result.series == []

    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(), user_id=USER_ID, selection=selection, start=DAY,
            end=end + timedelta(days=1), metric_key=None,
        )


def test_reader_returns_one_point_per_local_date_across_dst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = tuple(DAY + timedelta(days=offset) for offset in range(3))
    selection = NutritionProviderSelection("yazio", SOURCE_B, (("yazio", SOURCE_B),))

    def fake_period(
        _db: object,
        *,
        metric_keys: tuple[str, ...],
        **_kwargs: object,
    ) -> dict[date, dict[str, SimpleNamespace]]:
        return {
            current: {
                key: _matrix(
                    "yazio",
                    local_date=current,
                    nutrient_value=Decimal("1"),
                    nutrient_presence=PresenceState.SUPPLIED,
                )[key]
                for key in metric_keys
            }
            for current in dates
        }

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    result = nutrition_micronutrients.read_analysis_micronutrients(
        object(), user_id=USER_ID, selection=selection, start=dates[0],
        end=dates[-1], metric_key="vitamin_a_ug",
    )

    assert [point.date for point in result.series] == list(dates)
    assert [point.value for point in result.series] == [Decimal("1")] * len(dates)


def test_analysis_micronutrient_api_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/analytics/nutrition/micronutrients")

    assert response.status_code == 401


def test_analysis_micronutrient_api_fails_closed_without_provider(
    client: TestClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import analytics as analytics_api

    calls = 0

    def no_selection(_db: object, *, user_id: UUID):
        nonlocal calls
        assert user_id is not None
        calls += 1
        return None

    monkeypatch.setattr(analytics_api, "resolve_nutrition_provider", no_selection)
    monkeypatch.setattr(
        analytics_api,
        "read_legacy_micronutrient_period",
        lambda *_args, **_kwargs: pytest.fail("legacy micronutrient read was called"),
    )
    monkeypatch.setattr(
        analytics_api,
        "read_canonical_micronutrient_period",
        lambda *_args, **_kwargs: pytest.fail("legacy endpoint reader was called"),
    )
    _login(client)
    response = client.get("/api/v1/analytics/nutrition/micronutrients")

    assert response.status_code == 503
    assert response.json()["type"] == PROVIDER_SELECTION_UNAVAILABLE
    assert calls == 1


def test_analysis_micronutrient_api_bounds_readable_provider_period(
    client: TestClient, db: Session, user: User
) -> None:
    db.add(
        YazioConnection(
            user_id=user.id,
            encrypted_email=b"test-email",
            encrypted_password=b"test-password",
            source_identifier=f"analysis-{uuid4().hex}",
        )
    )
    db.flush()
    _login(client)

    one_month = client.get(
        "/api/v1/analytics/nutrition/micronutrients",
        params={"start": "2026-03-01", "end": "2026-03-31"},
    )
    assert one_month.status_code == 200
    assert one_month.json()["period_days"] == 31
    assert all(item["days_with_data"] == 0 for item in one_month.json()["metrics"])

    too_long = client.get(
        "/api/v1/analytics/nutrition/micronutrients",
        params={"start": "2026-03-01", "end": "2026-04-01"},
    )
    assert too_long.status_code == 422
    inverted = client.get(
        "/api/v1/analytics/nutrition/micronutrients",
        params={"start": "2026-04-01", "end": "2026-03-01"},
    )
    assert inverted.status_code == 422
    invalid_metric = client.get(
        "/api/v1/analytics/nutrition/micronutrients",
        params={"start": "2026-03-01", "end": "2026-03-31", "metric_key": "protein_g"},
    )
    assert invalid_metric.status_code == 422

    default_period = client.get("/api/v1/analytics/nutrition/micronutrients")
    assert default_period.status_code == 200
    assert default_period.json()["period_days"] == 30


def test_analysis_micronutrient_api_ignores_other_users_provider_sources(
    client: TestClient, db: Session, user: User
) -> None:
    from app.auth.security import hash_password

    other_user = User(
        username="other-analysis-user",
        password_hash=hash_password("correct-horse-battery-staple"),
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    db.add(
        YazioConnection(
            user_id=other_user.id,
            encrypted_email=b"other-email",
            encrypted_password=b"other-password",
            source_identifier=f"analysis-other-{uuid4().hex}",
        )
    )
    db.flush()
    _login(client)

    response = client.get("/api/v1/analytics/nutrition/micronutrients")

    assert response.status_code == 503
    assert response.json()["type"] == PROVIDER_SELECTION_UNAVAILABLE

def test_reader_excludes_canonical_evidence_from_other_users_and_source_instances(
    db: Session, user: User
) -> None:
    from test_verification_analytics import _nutrition_event, _nutrition_provider_source

    from app.auth.security import hash_password
    from app.nutrition.models import NutritionFieldObservation

    selected_source_id = _nutrition_provider_source(db, user, "yazio")
    other_user = User(
        username=f"analysis-foreign-{uuid4().hex}",
        password_hash=hash_password("correct-horse-battery-staple"),
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    metrics = {key: Decimal("1") for key in DAILY_PROJECTION_METRICS}
    metrics["vitamin_a_ug"] = Decimal("987")
    foreign_event = _nutrition_event(
        db,
        other_user,
        provider_key="yazio",
        source_instance_id=selected_source_id,
        local_date=DAY,
        logical_event_key="foreign-canonical-event",
        metrics=metrics,
        source_namespace="yazio.simple_product",
    )
    vitamin_field = db.query(NutritionFieldObservation).filter_by(
        source_observation_id=foreign_event.source_observation_id,
        metric_key="vitamin_a_ug",
    ).one()
    vitamin_field.provider_raw_unit = "ug"
    vitamin_field.canonical_unit = "ug"
    db.flush()
    same_user_event = _nutrition_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=uuid4(),
        local_date=DAY,
        logical_event_key="other-instance-canonical-event",
        metrics=metrics,
        source_namespace="yazio.simple_product",
    )
    same_user_vitamin_field = db.query(NutritionFieldObservation).filter_by(
        source_observation_id=same_user_event.source_observation_id,
        metric_key="vitamin_a_ug",
    ).one()
    same_user_vitamin_field.provider_raw_unit = "ug"
    same_user_vitamin_field.canonical_unit = "ug"
    db.flush()

    result = nutrition_micronutrients.read_analysis_micronutrients(
        db,
        user_id=user.id,
        selection=NutritionProviderSelection(
            "yazio", selected_source_id, (("yazio", selected_source_id),)
        ),
        start=DAY,
        end=DAY,
        metric_key="vitamin_a_ug",
    )

    assert result.series[0].value is None
    assert result.metrics[0].total is None
    assert result.metrics[0].days_with_data == 0


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
