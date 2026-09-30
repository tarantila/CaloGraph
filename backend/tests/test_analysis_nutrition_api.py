"""HTTP contract for source-coherent, read-only Analysis nutrition days and meals."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.provider_selection import NutritionProviderSelection
from app.api import analytics
from app.main import app
from app.models import User, UserProviderPreference
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.services.analysis_demo import seed_analysis_demo

PATH = "/api/v1/analytics/nutrition/analysis"
TODAY = date(2026, 9, 29)
START = TODAY - timedelta(days=89)
FIELDS = (
    "dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g",
    "fiber_g", "sugar_g", "saturated_fat_g",
)


def _demo_client(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    demo_id = seed_analysis_demo(db, password="synthetic-analysis-test-password", today=TODAY)
    account = db.get(User, demo_id)
    assert account is not None
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: account)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)
    return TestClient(app)


def _get(client: TestClient, first: date, last: date):
    return client.get(PATH, params={"start": first.isoformat(), "end": last.isoformat()})


def _nutrition_state(db: Session, owner_id):
    """Snapshot persisted evidence and projections, not request/mock call counts."""
    return tuple(
        (
            model.__tablename__,
            tuple(
                tuple(row)
                for row in db.execute(select(model.__table__).where(model.user_id == owner_id))
            ),
        )
        for model in (
            NutritionIngestionRun, NutritionSourceObservation, NutritionConsumptionEvent,
            NutritionFoodSnapshot, NutritionFieldObservation, NutritionDailyProjection,
            NutritionDailyProjectionFact, NutritionDailyProjectionLineage, NutritionProjectionHead,
        )
    )


def test_demo_json_exposes_persisted_seven_metric_totals_and_exact_named_meals(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    day = START + timedelta(days=10)

    response = _get(client, day, day)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start_date"] == day.isoformat()
    assert body["end_date"] == day.isoformat()
    assert body["period_days"] == 1
    assert body["selected_provider"]["provider_key"] == "analysis_demo"
    assert len(body["days"]) == 1
    row = body["days"][0]
    assert row["date"] == day.isoformat()
    assert row["provider_key"] == "analysis_demo"
    assert {key: row[key] for key in FIELDS} == {
        "dietary_energy_kcal": 1760, "protein_g": 69, "carbohydrates_g": 223,
        "fat_g": 61, "fiber_g": 38, "sugar_g": 50, "saturated_fat_g": 10,
    }
    assert [(meal["provider_key"], meal["display_name"], meal["meal_type"])
            for meal in row["meals"]] == [
        ("analysis_demo", "Haferflocken mit Beeren", "breakfast"),
        ("analysis_demo", "Linsensalat", "lunch"),
        ("analysis_demo", "Gemüsepfanne mit Reis", "dinner"),
        ("analysis_demo", "Apfel und Mandeln", "snack"),
    ]
    assert {key: row["meals"][0][key] for key in FIELDS} == {
        "dietary_energy_kcal": 420, "protein_g": 18, "carbohydrates_g": 62,
        "fat_g": 12, "fiber_g": 9, "sugar_g": 14, "saturated_fat_g": 2,
    }
    assert row["meals"][0]["local_time"] is not None


def test_missing_day_returns_200_null_metrics_and_no_meals_without_changing_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    demo = db.scalar(select(User).where(User.username == "calograph-analysis-demo"))
    assert demo is not None
    before = _nutrition_state(db, demo.id)
    day = START + timedelta(days=4)

    response = _get(client, day, day)

    assert response.status_code == 200, response.text
    row = response.json()["days"][0]
    assert row["date"] == day.isoformat()
    assert row["provider_key"] is None
    assert {key: row[key] for key in FIELDS} == dict.fromkeys(FIELDS)
    assert row["meals"] == []
    assert _nutrition_state(db, demo.id) == before
    populated_day = START + timedelta(days=10)
    populated = _get(client, populated_day, populated_day)
    assert populated.status_code == 200, populated.text
    assert populated.json()["days"][0]["meals"][0]["display_name"] == "Haferflocken mit Beeren"
    assert _nutrition_state(db, demo.id) == before


@pytest.mark.parametrize("days", [31, 90, 365])
def test_inclusive_limits_accept_long_ranges_and_preserve_chunk_edge_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, days: int,
) -> None:
    client = _demo_client(db, user, monkeypatch)

    response = _get(client, START, START + timedelta(days=days - 1))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period_days"] == days
    assert body["start_date"] == START.isoformat()
    assert body["end_date"] == (START + timedelta(days=days - 1)).isoformat()
    rows = body["days"]
    assert len(rows) == days
    assert rows[0]["date"] == START.isoformat()
    assert rows[-1]["date"] == body["end_date"]
    assert rows[0]["provider_key"] == "analysis_demo"
    assert rows[0]["meals"]
    for index in (30, 31, 61, 62):
        if index < days:
            assert rows[index]["date"] == (START + timedelta(days=index)).isoformat()
            assert rows[index]["provider_key"] == "analysis_demo"
            assert rows[index]["meals"]
    if days >= 90:
        assert rows[88]["provider_key"] == "analysis_demo"
        assert rows[88]["meals"]
        assert rows[89]["provider_key"] is None
        assert rows[89]["meals"] == []


def test_366_days_and_reversed_dates_are_rejected(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)

    assert _get(client, START, START + timedelta(days=365)).status_code == 422
    assert _get(client, TODAY, TODAY - timedelta(days=1)).status_code == 422


def test_authentication_is_required(client: TestClient, user: User) -> None:
    response = _get(client, START, START)
    assert response.status_code == 401


def test_no_configured_provider_is_unavailable(client: TestClient, user: User) -> None:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200

    response = _get(client, START, START)

    assert response.status_code == 503
    assert response.json()["type"]


def test_configured_but_unavailable_source_is_a_503_not_an_empty_day(
    client: TestClient, db: Session, user: User,
) -> None:
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="apple_health"))
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200

    response = _get(client, START, START)

    assert response.status_code == 503
    assert response.json()["type"]


def test_stale_configured_source_failure_is_503_not_a_missing_evidence_day(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    demo = db.scalar(select(User).where(User.username == "calograph-analysis-demo"))
    assert demo is not None
    selected = analytics.resolve_nutrition_provider(db, user_id=demo.id, include_analysis_demo=True)
    assert selected is not None
    stale_source = uuid4()
    monkeypatch.setattr(
        analytics, "resolve_nutrition_provider",
        lambda _db, *, user_id, include_analysis_demo=False: NutritionProviderSelection(
            selected.provider_key, stale_source, ((selected.provider_key, stale_source),),
        ),
    )

    response = _get(client, START + timedelta(days=10), START + timedelta(days=10))

    assert response.status_code == 503
    assert response.json()["type"]
