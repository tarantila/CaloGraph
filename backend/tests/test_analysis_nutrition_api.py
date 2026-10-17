"""HTTP contract for source-coherent, read-only Analysis nutrition days and meals."""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sqlalchemy_event
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
    NutritionSourceTombstone,
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


def test_meal_detail_uses_persisted_event_and_shared_daily_identity(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    day = START + timedelta(days=10)
    analysis = _get(client, day, day)
    assert analysis.status_code == 200, analysis.text
    meal = analysis.json()["days"][0]["meals"][0]
    event_id = meal["event_id"]

    detail = client.get(f"/api/v1/analytics/nutrition/analysis/meals/{event_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["event_id"] == event_id
    assert body["local_date"] == day.isoformat()
    assert body["display_name"] == "Haferflocken mit Beeren"
    assert body["serving_amount"] is not None
    assert body["serving_unit"]
    assert body["dietary_energy_kcal"] == meal["dietary_energy_kcal"]
    assert body["protein_g"] == meal["protein_g"]
    assert body["carbohydrates_g"] == meal["carbohydrates_g"]
    assert body["fat_g"] == meal["fat_g"]
    assert body["fiber_g"] == meal["fiber_g"]
    assert body["sugar_g"] == meal["sugar_g"]
    assert body["saturated_fat_g"] == meal["saturated_fat_g"]

    daily = client.get(
        "/api/v1/analytics/verification/nutrition",
        params={"date": day.isoformat(), "view": "canonical"},
    )
    assert daily.status_code == 200, daily.text
    daily_meal = next(
        item for item in daily.json()["canonical"]["events"]
        if item["event_id"] == event_id
    )
    assert daily_meal["detail_available"] is True


def test_meal_detail_hides_events_from_other_users(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    day = START + timedelta(days=10)
    analysis = _get(client, day, day)
    assert analysis.status_code == 200, analysis.text
    event_id = analysis.json()["days"][0]["meals"][0]["event_id"]
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: user)

    response = client.get(f"/api/v1/analytics/nutrition/analysis/meals/{event_id}")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "unsafe",
    ["event_lineage", "source_lineage", "missing_name", "tombstone", "stale_event", "stale_source"],
)
def test_meal_detail_hides_unsafe_or_stale_evidence_and_disables_daily_links(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, unsafe: str,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    day = START + timedelta(days=10)
    analysis = _get(client, day, day)
    assert analysis.status_code == 200, analysis.text
    event_id = analysis.json()["days"][0]["meals"][0]["event_id"]
    item = db.get(NutritionConsumptionEvent, UUID(event_id))
    assert item is not None
    source = db.get(NutritionSourceObservation, item.source_observation_id)
    assert source is not None
    if unsafe == "event_lineage":
        item.lineage_state = "unknown"
    elif unsafe == "source_lineage":
        source.lineage_state = "unknown"
    elif unsafe == "missing_name":
        snapshot = db.get(NutritionFoodSnapshot, item.food_snapshot_id)
        assert snapshot is not None
        snapshot.is_deleted = True
    elif unsafe == "tombstone":
        db.add(NutritionSourceTombstone(
            user_id=item.user_id,
            provider_key=item.provider_key,
            source_instance_id=item.source_instance_id,
            source_namespace=source.source_namespace,
            source_observation_id=source.id,
            tombstone_kind="deleted",
        ))
    else:
        model = NutritionConsumptionEvent if unsafe == "stale_event" else NutritionSourceObservation
        original = item if unsafe == "stale_event" else source
        if unsafe == "stale_event":
            assert item.logical_event_key is not None
        else:
            assert source.source_record_id is not None
        values = dict(db.execute(
            select(model.__table__).where(model.id == original.id),
        ).mappings().one())
        revision_key = "revision" if unsafe == "stale_event" else "source_revision"
        values.update(id=uuid4(), **{revision_key: values[revision_key] + 1})
        db.execute(model.__table__.insert().values(**values))
    db.commit()

    response = client.get(f"{PATH}/meals/{event_id}")
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Eintrag nicht verfügbar"
    daily = client.get(
        "/api/v1/analytics/verification/nutrition",
        params={"date": day.isoformat(), "view": "canonical"},
    )
    assert daily.status_code == 200, daily.text
    matches = [
        row for row in daily.json()["canonical"]["events"] if row["event_id"] == event_id
    ]
    if unsafe != "stale_event":
        assert len(matches) == 1
        assert matches[0]["detail_available"] is False
    else:
        assert matches == []


def test_meal_detail_keeps_unsafe_individual_nutrients_null(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    day = START + timedelta(days=10)
    analysis = _get(client, day, day)
    assert analysis.status_code == 200, analysis.text
    meal = analysis.json()["days"][0]["meals"][0]
    item = db.get(NutritionConsumptionEvent, UUID(meal["event_id"]))
    assert item is not None
    field = db.scalar(select(NutritionFieldObservation).where(
        NutritionFieldObservation.source_observation_id == item.source_observation_id,
        NutritionFieldObservation.metric_key == "protein_g",
    ))
    assert field is not None
    field.lineage_state = "unknown"
    db.commit()

    response = client.get(f"{PATH}/meals/{meal['event_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["protein_g"] is None
    assert body["dietary_energy_kcal"] == 420
    assert body["serving_amount"] == meal["serving_amount"]
    assert body["serving_unit"] == meal["serving_unit"]
    assert "ingredients" not in body
    assert "photo" not in body


def test_meal_detail_missing_and_malformed_ids_share_neutral_not_found(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _demo_client(db, user, monkeypatch)
    for event_id in (str(uuid4()), "not-a-uuid"):
        response = client.get(f"{PATH}/meals/{event_id}")
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Eintrag nicht verfügbar"


def test_meal_detail_requires_authentication(client: TestClient, user: User) -> None:
    response = client.get(f"{PATH}/meals/{uuid4()}")
    assert response.status_code == 401


def test_daily_detail_eligibility_reads_all_events_in_one_bounded_batch(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.analytics.nutrition_analysis import nutrition_detail_available_ids

    _demo_client(db, user, monkeypatch)
    demo = db.scalar(select(User).where(User.username == "calograph-analysis-demo"))
    assert demo is not None
    day = START + timedelta(days=10)
    events = list(db.scalars(select(NutritionConsumptionEvent).where(
        NutritionConsumptionEvent.user_id == demo.id,
        NutritionConsumptionEvent.local_date == day,
    )))
    assert len(events) == 4
    queries: list[str] = []

    def count_query(_connection, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    connection = db.connection()
    sqlalchemy_event.listen(connection, "before_cursor_execute", count_query)
    try:
        eligible = nutrition_detail_available_ids(db, user_id=demo.id, events=events)
    finally:
        sqlalchemy_event.remove(connection, "before_cursor_execute", count_query)
    assert eligible == {item.id for item in events}
    assert len(queries) <= 3


def test_meal_detail_rejects_a_previously_selected_source_that_is_no_longer_permitted(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.analytics import verification

    client = _demo_client(db, user, monkeypatch)
    day = START + timedelta(days=10)
    analysis = _get(client, day, day)
    assert analysis.status_code == 200, analysis.text
    event_id = analysis.json()["days"][0]["meals"][0]["event_id"]
    # The demo is not a daily all-provider source; withdrawing its current
    # Analysis selection therefore removes it from the allowed server-side union.
    monkeypatch.setattr(verification, "resolve_nutrition_provider", lambda *_args, **_kwargs: None)

    response = client.get(
        f"{PATH}/meals/{event_id}",
        params={"user_id": str(user.id), "source": "analysis_demo", "date": day.isoformat()},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Eintrag nicht verfügbar"
