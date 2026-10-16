"""Offline, scoped Analysis demo nutrition provider behavior in the guarded test DB."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.provider_selection import (
    NutritionProviderSelectionError,
    resolve_nutrition_provider,
)
from app.config import settings
from app.models import HealthSample, User, UserProviderPreference
from app.nutrition.enums import PresenceState, ProjectionGranularity
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
)
from app.nutrition.resolution import resolve_provider_period
from app.nutrition.resolution.providers import ProviderNotAvailableError
from app.nutrition.resolution.sources import (
    ProviderSourceBindings,
    ProviderSourceInstance,
    resolve_default_provider_sources,
    validate_provider_source_bindings,
)
from app.provider_preferences import (
    ACTIVITY_ENERGY_DATA_AREA,
    NUTRITION_DATA_AREA,
    WEIGHT_DATA_AREA,
    effective_provider_order,
)
from app.services.analysis_demo import demo_source_instance_id, seed_analysis_demo
from app.services.provider_preferences import provider_availability

TODAY = date(2026, 9, 29)
START = TODAY - timedelta(days=89)
METRICS = (
    "dietary_energy_kcal",
    "protein_g",
    "carbohydrates_g",
    "fat_g",
    "fiber_g",
    "sugar_g",
    "saturated_fat_g",
    "vitamin_a_ug",
    "vitamin_c_mg",
    "vitamin_d_ug",
    "folate_ug",
    "calcium_mg",
    "iron_mg",
    "potassium_mg",
    "magnesium_mg",
)


def _seed(db: Session) -> tuple[UUID, UUID]:
    demo_id = seed_analysis_demo(db, password="synthetic-offline-demo-password", today=TODAY)
    return demo_id, demo_source_instance_id(demo_id)


def _period(
    db: Session, demo_id: UUID, source_id: UUID, start: date, end: date, metric_keys=METRICS
):
    return resolve_provider_period(
        db,
        provider_key="analysis_demo",
        user_id=demo_id,
        source_instance_id=source_id,
        start=start,
        end=end,
        metric_keys=metric_keys,
    )


def test_fixed_breakfast_is_event_backed_and_contains_exact_meal_nutrients(
    db: Session, user: User
) -> None:
    demo_id, source_id = _seed(db)
    day = START + timedelta(days=10)
    matrix = _period(db, demo_id, source_id, day, day)
    assert set(matrix) == {day}
    assert set(matrix[day]) == set(METRICS)
    energy = matrix[day]["dietary_energy_kcal"]
    assert energy.value is not None and energy.value >= Decimal("420")
    assert energy.selected_granularity == ProjectionGranularity.EVENT
    assert energy.provider_key == "analysis_demo"
    assert energy.user_id == demo_id
    assert energy.local_date == day
    assert energy.source_lineage
    event = db.scalar(
        select(NutritionConsumptionEvent).where(
            NutritionConsumptionEvent.user_id == demo_id,
            NutritionConsumptionEvent.local_date == day,
            NutritionConsumptionEvent.daytime == "breakfast",
        )
    )
    assert event is not None
    meal_source = db.get(NutritionSourceObservation, event.source_observation_id)
    snapshot = db.get(NutritionFoodSnapshot, event.food_snapshot_id)
    assert meal_source is not None and snapshot is not None
    profile_source = db.get(NutritionSourceObservation, snapshot.source_observation_id)
    assert profile_source is not None
    assert profile_source.id != meal_source.id
    assert meal_source.observation_kind == "consumption_event"
    assert meal_source.local_date == day
    assert profile_source.observation_kind == "product_profile"
    assert profile_source.local_date is None
    assert matrix[day]["protein_g"].value is not None
    assert matrix[day]["vitamin_a_ug"].value is not None


def test_selected_zero_nutrient_is_not_a_missing_date(
    db: Session, user: User
) -> None:
    demo_id, source_id = _seed(db)
    events = list(db.scalars(select(NutritionConsumptionEvent).where(NutritionConsumptionEvent.user_id == demo_id)))
    fields = list(db.scalars(select(NutritionFieldObservation).where(NutritionFieldObservation.user_id == demo_id)))
    by_observation = {event.source_observation_id: event.local_date for event in events}
    by_day_metric: dict[tuple[date, str], list[Decimal]] = {}
    for field in fields:
        day = by_observation.get(field.source_observation_id)
        if day is not None and field.metric_key in METRICS and field.canonical_value is not None:
            by_day_metric.setdefault((day, field.metric_key), []).append(field.canonical_value)
    selected_zero = next(
        ((day, metric) for (day, metric), values in by_day_metric.items()
         if values and all(value == Decimal("0") for value in values)),
        None,
    )
    assert selected_zero is not None, "the seed needs a selected, event-backed explicit zero"
    zero_day, zero_metric = selected_zero
    zero = _period(db, demo_id, source_id, zero_day, zero_day, (zero_metric,))[zero_day][zero_metric]
    assert zero.value == Decimal("0")
    assert zero.presence_state == PresenceState.EXPLICIT_ZERO
    assert zero.selected_granularity == ProjectionGranularity.EVENT
    assert zero.source_lineage
    assert any(
        field.canonical_value == Decimal("0")
        and field.presence_state == PresenceState.EXPLICIT_ZERO.value
        for field in fields
        if by_observation.get(field.source_observation_id) == zero_day
        and field.metric_key == zero_metric
    )

    covered_dates = {event.local_date for event in events}
    missing_day = next(
        (START + timedelta(days=offset) for offset in range(90)
         if START + timedelta(days=offset) not in covered_dates),
        None,
    )
    assert missing_day is not None, "the seed must retain genuine gaps"
    missing = _period(db, demo_id, source_id, missing_day, missing_day, (zero_metric,))[missing_day][zero_metric]
    assert missing.value is None
    assert missing.presence_state == PresenceState.MISSING
    assert not missing.source_lineage


def test_demo_period_is_complete_for_requested_dates_and_bounded_to_31_days(
    db: Session, user: User
) -> None:
    demo_id, source_id = _seed(db)
    start = START + timedelta(days=6)
    matrix = _period(db, demo_id, source_id, start, start + timedelta(days=30))
    assert set(matrix) == {start + timedelta(days=offset) for offset in range(31)}
    assert all(set(day) == set(METRICS) for day in matrix.values())
    assert all(
        candidate.user_id == demo_id and candidate.local_date == day and candidate.metric_key == metric
        for day, values in matrix.items() for metric, candidate in values.items()
    )
    with pytest.raises(ValueError):
        _period(db, demo_id, source_id, start, start + timedelta(days=31))


@pytest.mark.parametrize("wrong_identity", ["user", "source", "source_and_user"])
def test_demo_resolver_rejects_foreign_accounts_and_source_ids_even_with_existing_rows(
    db: Session, user: User, wrong_identity: str
) -> None:
    demo_id, source_id = _seed(db)
    assert db.scalar(select(NutritionSourceObservation.id).where(NutritionSourceObservation.user_id == demo_id)) is not None
    requested_user = user.id if wrong_identity in {"user", "source_and_user"} else demo_id
    requested_source = uuid4() if wrong_identity in {"source", "source_and_user"} else source_id
    day = START + timedelta(days=10)
    with pytest.raises((ValueError, ProviderNotAvailableError)):
        _period(db, requested_user, requested_source, day, day, ("dietary_energy_kcal",))
    # Chunked reads may bypass redundant source lookup, never account/source ownership.
    from app.nutrition.resolution import period_reader

    with pytest.raises((ValueError, ProviderNotAvailableError)):
        period_reader._resolve_provider_period(
            db,
            provider_key="analysis_demo",
            user_id=requested_user,
            source_instance_id=requested_source,
            start=day,
            end=day,
            metric_keys=("dietary_energy_kcal",),
            skip_source_instance_validation=True,
        )


def test_demo_seed_and_nutrition_reads_never_open_provider_connections(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import httpx

    def forbid_network(*_args, **_kwargs):
        raise AssertionError("offline demo attempted a network/provider request")

    monkeypatch.setattr(httpx.Client, "request", forbid_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    demo_id, source_id = _seed(db)
    day = START + timedelta(days=10)
    assert _period(db, demo_id, source_id, day, day)[day]["dietary_energy_kcal"].value >= Decimal("420")
    assert resolve_nutrition_provider(
        db, user_id=demo_id, include_analysis_demo=True,
    ).source_instance_id == source_id


@pytest.mark.parametrize("area", [NUTRITION_DATA_AREA, WEIGHT_DATA_AREA, ACTIVITY_ENERGY_DATA_AREA])
def test_demo_provider_is_available_only_to_its_owned_account(
    db: Session, user: User, area: str
) -> None:
    demo_id, source_id = _seed(db)
    demo_status = {item.provider_key: item for item in provider_availability(db, user_id=demo_id, data_area=area)}
    ordinary_status = {item.provider_key: item for item in provider_availability(db, user_id=user.id, data_area=area)}
    assert demo_status["analysis_demo"].available is True
    assert "analysis_demo" not in ordinary_status or not ordinary_status["analysis_demo"].available
    assert "analysis_demo" in effective_provider_order(area, (), include_missing=True)
    if area == NUTRITION_DATA_AREA:
        selected = resolve_nutrition_provider(db, user_id=demo_id, include_analysis_demo=True)
        assert selected is not None and selected.provider_key == "analysis_demo"
        assert selected.source_instance_id == source_id
        ordinary = resolve_nutrition_provider(db, user_id=user.id, include_analysis_demo=True)
        assert ordinary is None or ordinary.provider_key != "analysis_demo"
        bindings = resolve_default_provider_sources(db, user_id=demo_id, provider_keys=("analysis_demo",))
        assert bindings.for_provider("analysis_demo") == ProviderSourceInstance("analysis_demo", source_id)
        validate_provider_source_bindings(db, user_id=demo_id, bindings=bindings)
        with pytest.raises(ValueError):
            validate_provider_source_bindings(db, user_id=user.id, bindings=bindings)


def test_production_does_not_expose_surviving_demo_provider_or_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_id, source_id = _seed(db)
    assert db.scalar(select(NutritionSourceObservation.id).where(NutritionSourceObservation.user_id == demo_id)) is not None
    monkeypatch.setattr(settings, "environment", "production")
    for area in (NUTRITION_DATA_AREA, WEIGHT_DATA_AREA, ACTIVITY_ENERGY_DATA_AREA):
        assert "analysis_demo" not in effective_provider_order(area, (), include_missing=True)
        assert "analysis_demo" not in {
            item.provider_key for item in provider_availability(db, user_id=demo_id, data_area=area)
            if item.available
        }
    try:
        selected = resolve_nutrition_provider(db, user_id=demo_id, include_analysis_demo=True)
    except NutritionProviderSelectionError:
        pass
    else:
        assert selected is None or selected.provider_key != "analysis_demo"
    with pytest.raises((ValueError, ProviderNotAvailableError)):
        _period(db, demo_id, source_id, START + timedelta(days=10), START + timedelta(days=10))
    with pytest.raises(ValueError):
        validate_provider_source_bindings(
            db,
            user_id=demo_id,
            bindings=ProviderSourceBindings((ProviderSourceInstance("analysis_demo", source_id),)),
        )


def test_seeded_demo_is_readable_through_existing_analysis_apis(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    demo_id, _source_id = _seed(db)
    demo = db.get(User, demo_id)
    assert demo is not None
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: demo)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)

    with TestClient(app) as client:
        for days in (7, 30):
            start = TODAY - timedelta(days=days - 1)
            params = {"start": start.isoformat(), "end": TODAY.isoformat()}

            weight = client.get("/api/v1/analytics/weight", params=params)
            assert weight.status_code == 200, weight.text
            weight_body = weight.json()
            assert weight_body["selected_provider"]["provider_key"] == "analysis_demo"
            assert weight_body["points"]

            activity = client.get(
                "/api/v1/analytics/verification/activity",
                params={**params, "view": "canonical"},
            )
            assert activity.status_code == 200, activity.text
            activity_days = activity.json()["days"]
            assert any(
                item["canonical"]["provider_key"] == "analysis_demo"
                and item["canonical"]["status"] == "available"
                and "analysis_demo_offline" in item["canonical"]["source_types"]
                for item in activity_days
                if item["canonical"] is not None
            )

            all_activity = client.get(
                "/api/v1/analytics/verification/activity",
                params={**params, "view": "all"},
            )
            assert all_activity.status_code == 200, all_activity.text
            all_days = all_activity.json()["days"]
            current_demo = next(
                record for record in all_days[-1]["providers"]
                if record["provider_key"] == "analysis_demo"
            )
            assert current_demo["status"] == "available"
            assert current_demo["active_energy_kcal"] == 0
            assert current_demo["source_types"] == ["analysis_demo_offline"]
            if days == 7:
                gap = next(
                    item for item in all_days
                    if item["date"] == (TODAY - timedelta(days=3)).isoformat()
                )
                gap_demo = next(
                    record for record in gap["providers"]
                    if record["provider_key"] == "analysis_demo"
                )
                assert gap_demo["status"] == "no_data"
                assert gap_demo["active_energy_kcal"] is None
            if days == 30:
                assert any(
                    item["canonical"] is not None
                    and item["canonical"]["status"] == "no_data"
                    and item["canonical"]["active_energy_kcal"] is None
                    for item in activity_days
                )

            metric_key = "vitamin_a_ug" if days == 7 else "vitamin_d_ug"
            micronutrients = client.get(
                "/api/v1/analytics/nutrition/micronutrients",
                params={**params, "metric_key": metric_key},
            )
            assert micronutrients.status_code == 200, micronutrients.text
            micro_body = micronutrients.json()
            assert micro_body["selected_provider"]["provider_key"] == "analysis_demo"
            metric = next(row for row in micro_body["metrics"] if row["metric_key"] == metric_key)
            assert metric["days_with_data"] > 0
            if metric_key == "vitamin_d_ug":
                values = [row["value"] for row in micro_body["series"]]
                assert 0 in values
                assert None in values


def test_canonical_demo_activity_ignores_other_demo_source_identifiers(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    demo_id, source_id = _seed(db)
    demo = db.get(User, demo_id)
    assert demo is not None
    seeded_samples = db.scalars(
        select(HealthSample).where(
            HealthSample.user_id == demo_id,
            HealthSample.source_type == "analysis_demo_offline",
            HealthSample.source_identifier == str(source_id),
            HealthSample.local_date == TODAY,
            HealthSample.metric_type.like("%active%energy%"),
        )
    ).all()
    assert seeded_samples
    expected_energy = sum((sample.value for sample in seeded_samples), Decimal("0"))
    reference = seeded_samples[0]
    db.add(
        HealthSample(
            user_id=demo_id,
            import_batch_id=reference.import_batch_id,
            external_sample_id=f"foreign-demo-{uuid4()}",
            fingerprint=uuid4().hex,
            source_type="analysis_demo_offline",
            source_name=reference.source_name,
            source_identifier=str(uuid4()),
            metric_type=reference.metric_type,
            value=Decimal("1234"),
            unit=reference.unit,
            original_value=Decimal("1234"),
            original_unit=reference.original_unit,
            start_at=reference.start_at,
            end_at=reference.end_at,
            local_date=TODAY,
            timezone=reference.timezone,
        )
    )
    db.flush()
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: demo)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)

    params = {"start": TODAY.isoformat(), "end": TODAY.isoformat()}
    with TestClient(app) as client:
        canonical_response = client.get(
            "/api/v1/analytics/verification/activity",
            params={**params, "view": "canonical"},
        )
        all_response = client.get(
            "/api/v1/analytics/verification/activity",
            params={**params, "view": "all"},
        )

    assert canonical_response.status_code == 200, canonical_response.text
    assert all_response.status_code == 200, all_response.text
    canonical = canonical_response.json()["days"][0]["canonical"]
    all_demo = next(
        provider
        for provider in all_response.json()["days"][0]["providers"]
        if provider["provider_key"] == "analysis_demo"
    )
    assert canonical is not None
    assert canonical["provider_key"] == "analysis_demo"
    assert canonical["status"] == "available"
    assert Decimal(str(all_demo["active_energy_kcal"])) == expected_energy
    assert Decimal(str(canonical["active_energy_kcal"])) == expected_energy
    assert canonical["source_types"] == all_demo["source_types"] == ["analysis_demo_offline"]


def test_daily_nutrition_verification_includes_demo_only_in_canonical_view(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    demo_id, _source_id = _seed(db)
    demo = db.get(User, demo_id)
    assert demo is not None
    db.add(
        UserProviderPreference(
            user_id=demo_id,
            data_area=NUTRITION_DATA_AREA,
            provider_key="analysis_demo",
        )
    )
    db.flush()

    # Default provider selection stays closed; the verification route opts in behind the demo gate.
    assert resolve_nutrition_provider(db, user_id=demo_id) is None

    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: demo)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)
    populated_day = START + timedelta(days=10)
    empty_day = START + timedelta(days=4)
    with TestClient(app, raise_server_exceptions=False) as client:
        canonical = client.get(
            "/api/v1/analytics/verification/nutrition",
            params={"date": populated_day.isoformat(), "view": "canonical"},
        )
        empty_canonical = client.get(
            "/api/v1/analytics/verification/nutrition",
            params={"date": empty_day.isoformat(), "view": "canonical"},
        )
        all_sources = client.get(
            "/api/v1/analytics/verification/nutrition",
            params={"date": populated_day.isoformat(), "view": "all"},
        )
        analysis = client.get(
            "/api/v1/analytics/nutrition/analysis",
            params={"start": populated_day.isoformat(), "end": populated_day.isoformat()},
        )
        micronutrients = client.get(
            "/api/v1/analytics/nutrition/micronutrients",
            params={
                "start": populated_day.isoformat(),
                "end": populated_day.isoformat(),
                "metric_key": "vitamin_a_ug",
            },
        )
        monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: user)
        ordinary_user = client.get(
            "/api/v1/analytics/verification/nutrition",
            params={"date": populated_day.isoformat(), "view": "canonical"},
        )

    assert canonical.status_code == 200, canonical.text
    canonical_group = canonical.json()["canonical"]
    assert canonical_group is not None
    assert canonical_group["provider_key"] == "analysis_demo"
    assert canonical_group["status"] == "available"
    assert canonical_group["record_count"] == 4
    assert [event["display_name"] for event in canonical_group["events"]] == [
        "Haferflocken mit Beeren",
        "Linsensalat",
        "Gemüsepfanne mit Reis",
        "Apfel und Mandeln",
    ]
    assert canonical.json()["providers"] == []
    assert empty_canonical.status_code == 200, empty_canonical.text
    empty_group = empty_canonical.json()["canonical"]
    assert empty_group is not None
    assert empty_group["provider_key"] == "analysis_demo"
    assert empty_group["status"] == "no_data"
    assert empty_group["record_count"] == 0
    assert empty_group["events"] == []
    assert ordinary_user.status_code == 200, ordinary_user.text
    assert ordinary_user.json()["canonical"] is None
    assert ordinary_user.json()["providers"] == []
    assert all_sources.status_code == 200, all_sources.text
    assert all_sources.json()["canonical"] is None
    assert all(
        provider["provider_key"] != "analysis_demo"
        for provider in all_sources.json()["providers"]
    )
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["selected_provider"]["provider_key"] == "analysis_demo"
    assert micronutrients.status_code == 200, micronutrients.text
    assert micronutrients.json()["selected_provider"]["provider_key"] == "analysis_demo"


def _freeze_analytics_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    from app.api import analytics

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(day.year, day.month, day.day, 12, 0, tzinfo=tz)

    monkeypatch.setattr(analytics, "datetime", FixedDatetime)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_overview_reads_same_demo_nutrition_as_analysis_and_writes_no_legacy_samples(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, environment: str,
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    monkeypatch.setattr(settings, "environment", environment)
    demo_id, source_id = _seed(db)
    demo = db.get(User, demo_id)
    assert demo is not None
    day = START + timedelta(days=10)
    _freeze_analytics_today(monkeypatch, day)
    selected = resolve_nutrition_provider(
        db, user_id=demo_id, include_analysis_demo=True,
    )
    assert selected is not None
    assert selected.provider_key == "analysis_demo"
    assert selected.source_instance_id == source_id

    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: demo)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)
    sample_query = (
        select(
            HealthSample.id,
            HealthSample.metric_type,
            HealthSample.value,
            HealthSample.local_date,
        )
        .where(HealthSample.user_id == demo_id)
        .order_by(HealthSample.id)
    )
    before_samples = tuple(db.execute(sample_query).all())
    assert {row.metric_type for row in before_samples} <= {
        "weight_kg",
        "active_energy_kcal",
    }

    with TestClient(app) as client:
        summary = client.get("/api/v1/dashboard/summary")
        trends = client.get(
            "/api/v1/analytics/trends",
            params={
                "start": day.isoformat(),
                "end": day.isoformat(),
                "include_incomplete": "true",
            },
        )
        analysis = client.get(
            "/api/v1/analytics/nutrition/analysis",
            params={"start": day.isoformat(), "end": day.isoformat()},
        )

    assert summary.status_code == 200, summary.text
    assert trends.status_code == 200, trends.text
    assert analysis.status_code == 200, analysis.text
    summary_day = summary.json()["today"]
    trend_day = next(
        point for point in trends.json()["points"]
        if point["date"] == day.isoformat()
    )
    analysis_day = analysis.json()["days"][0]
    assert analysis_day["provider_key"] == "analysis_demo"

    def nutrition_values(
        point: dict[str, object],
        energy_key: str,
        carbohydrate_key: str,
    ) -> dict[str, Decimal]:
        return {
            "dietary_energy_kcal": Decimal(str(point[energy_key])),
            "protein_g": Decimal(str(point["protein_g"])),
            "carbohydrates_g": Decimal(str(point[carbohydrate_key])),
            "fat_g": Decimal(str(point["fat_g"])),
        }

    expected = {
        "dietary_energy_kcal": Decimal("1760"),
        "protein_g": Decimal("69"),
        "carbohydrates_g": Decimal("223"),
        "fat_g": Decimal("61"),
    }
    assert nutrition_values(summary_day, "calories_kcal", "carbs_g") == expected
    assert nutrition_values(trend_day, "calories_kcal", "carbs_g") == expected
    assert nutrition_values(analysis_day, "dietary_energy_kcal", "carbohydrates_g") == expected
    assert tuple(db.execute(sample_query).all()) == before_samples


@pytest.mark.parametrize("environment", ["development", "test"])
def test_overview_does_not_read_demo_nutrition_for_another_user(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, environment: str,
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    monkeypatch.setattr(settings, "environment", environment)
    demo_id, _source_id = _seed(db)
    day = START + timedelta(days=10)
    _freeze_analytics_today(monkeypatch, day)
    db.add(
        UserProviderPreference(
            user_id=user.id,
            data_area=NUTRITION_DATA_AREA,
            provider_key="analysis_demo",
        )
    )
    db.flush()
    assert resolve_nutrition_provider(
        db, user_id=user.id, include_analysis_demo=True,
    ) is None
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: user)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)

    with TestClient(app) as client:
        summary = client.get("/api/v1/dashboard/summary")
        trends = client.get(
            "/api/v1/analytics/trends",
            params={"start": day.isoformat(), "end": day.isoformat()},
        )

    assert summary.status_code == 200, summary.text
    assert trends.status_code == 200, trends.text
    assert summary.json()["today"]["calories_kcal"] is None
    assert trends.json()["points"][0]["calories_kcal"] is None
    assert db.scalar(
        select(HealthSample.id).where(HealthSample.user_id == user.id)
    ) is None
    assert db.scalar(
        select(HealthSample.id).where(HealthSample.user_id == demo_id)
    ) is not None


def test_overview_does_not_read_demo_nutrition_in_production(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    demo_id, _source_id = _seed(db)
    demo = db.get(User, demo_id)
    assert demo is not None
    day = START + timedelta(days=10)
    _freeze_analytics_today(monkeypatch, day)
    monkeypatch.setattr(settings, "environment", "production")
    assert resolve_nutrition_provider(
        db, user_id=demo_id, include_analysis_demo=True,
    ) is None
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: demo)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)

    with TestClient(app) as client:
        summary = client.get("/api/v1/dashboard/summary")
        trends = client.get(
            "/api/v1/analytics/trends",
            params={"start": day.isoformat(), "end": day.isoformat()},
        )

    assert summary.status_code == 200, summary.text
    assert trends.status_code == 200, trends.text
    assert summary.json()["today"]["calories_kcal"] is None
    assert trends.json()["points"][0]["calories_kcal"] is None


def test_weight_reader_ignores_other_demo_source_identifiers(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.api import analytics
    from app.main import app

    demo_id, source_id = _seed(db)
    demo = db.get(User, demo_id)
    assert demo is not None
    seeded_weights = db.scalars(
        select(HealthSample).where(
            HealthSample.user_id == demo_id,
            HealthSample.source_type == "analysis_demo_offline",
            HealthSample.source_identifier == str(source_id),
            HealthSample.metric_type == "weight_kg",
            HealthSample.local_date == TODAY,
        )
    ).all()
    assert seeded_weights
    expected = max(seeded_weights, key=lambda sample: sample.start_at)
    db.add(
        HealthSample(
            user_id=demo_id,
            import_batch_id=expected.import_batch_id,
            external_sample_id=f"foreign-demo-weight-{uuid4()}",
            fingerprint=uuid4().hex,
            source_type="analysis_demo_offline",
            source_name=expected.source_name,
            source_identifier=str(uuid4()),
            metric_type="weight_kg",
            value=Decimal("1234"),
            unit=expected.unit,
            original_value=Decimal("1234"),
            original_unit=expected.original_unit,
            start_at=expected.start_at + timedelta(hours=1),
            end_at=expected.end_at + timedelta(hours=1),
            local_date=TODAY,
            timezone=expected.timezone,
        )
    )
    db.flush()
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: demo)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/analytics/weight",
            params={"start": TODAY.isoformat(), "end": TODAY.isoformat()},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    expected_point = {"date": TODAY.isoformat(), "weight_kg": float(expected.value)}
    assert body["selected_provider"]["provider_key"] == "analysis_demo"
    assert body["points"] == [expected_point]
    assert body["summary"] == {
        "measurement_count": 1,
        "latest": expected_point,
        "change_kg": None,
        "average_kg": float(expected.value),
        "minimum_kg": float(expected.value),
        "maximum_kg": float(expected.value),
    }
