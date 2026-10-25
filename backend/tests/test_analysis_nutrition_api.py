"""HTTP contract for source-coherent, read-only Analysis nutrition days and meals."""

from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.provider_selection import NutritionProviderSelection
from app.api import analytics
from app.main import app
from app.models import User, UserProviderPreference, YazioConnection
from app.nutrition.enums import ObservationRole
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.metrics import canonical_unit
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



def _add_detail_event(
    db: Session,
    user: User,
    *,
    provider_key: str,
    source_instance_id: UUID,
    day: date,
    key: str,
    source_namespace: str,
    event_kind: str,
    event_metadata: dict[str, object] | None = None,
    source_metadata: dict[str, object] | None = None,
    snapshot_name: str | None = None,
    nutrients: dict[str, Decimal] | None = None,
) -> NutritionConsumptionEvent:
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="reader-test",
        requested_start_date=day,
        requested_end_date=day,
        covered_start_date=day,
        covered_end_date=day,
        status="completed",
        coverage_state="complete",
    )
    db.add(run)
    db.flush()
    source = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        connector_variant="reader-test",
        observation_kind="consumption_event",
        source_namespace=source_namespace,
        source_record_id=key,
        source_revision=1,
        observation_fingerprint=sha256(key.encode()).hexdigest(),
        local_date=day,
        timezone_source="provider",
        presence_state="supplied",
        coverage_state="complete",
        resolution_state="resolved",
        lineage_state="confirmed",
        provider_metadata=source_metadata or {},
    )
    db.add(source)
    db.flush()
    snapshot_id = None
    if snapshot_name is not None:
        profile = NutritionFoodProfile(
            user_id=user.id,
            provider_key=provider_key,
            source_instance_id=source_instance_id,
        )
        db.add(profile)
        db.flush()
        snapshot = NutritionFoodSnapshot(
            user_id=user.id,
            food_profile_id=profile.id,
            source_observation_id=source.id,
            content_hash=sha256(f"{key}:{snapshot_name}".encode()).hexdigest(),
            name=snapshot_name,
            is_deleted=False,
        )
        db.add(snapshot)
        db.flush()
        snapshot_id = snapshot.id
    event = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=source.id,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        event_kind=event_kind,
        logical_event_key=key,
        revision=1,
        food_snapshot_id=snapshot_id,
        local_date=day,
        amount=None,
        amount_unit=None,
        presence_state="supplied",
        coverage_state="complete",
        resolution_state="resolved",
        lineage_state="confirmed",
        provider_metadata=event_metadata or {},
    )
    db.add(event)
    db.flush()
    for metric_key, value in (nutrients or {}).items():
        unit = canonical_unit(metric_key)
        db.add(NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=source.id,
            provider_field_path=f"fixture.{metric_key}",
            provider_raw_value_decimal=value,
            provider_raw_unit=unit,
            metric_key=metric_key,
            canonical_value=value,
            canonical_unit=unit,
            observation_role=ObservationRole.PROVIDER.value,
            presence_state="supplied",
            coverage_state="complete",
            resolution_state="resolved",
            lineage_state="confirmed",
        ))
    db.flush()
    return event


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


def test_yazio_simple_product_name_enables_daily_detail_and_safe_nutrients(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.analytics.nutrition_analysis import nutrition_detail_available_ids

    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="nutrition-detail-test",
    )
    db.add(connection)
    db.flush()
    day = START + timedelta(days=10)
    named = _add_detail_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=connection.id,
        day=day,
        key="named-simple-product",
        source_namespace="yazio.simple_product",
        event_kind="simple_product",
        event_metadata={"name": " Chicken ", "is_ai_generated": False},
        source_metadata={"name": "Source metadata must not be used"},
        nutrients={"dietary_energy_kcal": Decimal("241"), "protein_g": Decimal("28.5")},
    )
    unnamed = _add_detail_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=connection.id,
        day=day,
        key="unnamed-simple-product",
        source_namespace="yazio.simple_product",
        event_kind="simple_product",
        event_metadata={"name": "   "},
        source_metadata={"name": "Source metadata must not be used"},
    )
    unsafe = _add_detail_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=connection.id,
        day=day,
        key="unsafe-simple-product",
        source_namespace="yazio.simple_product",
        event_kind="simple_product",
        event_metadata={"name": {"raw": "provider payload"}},
    )
    product = _add_detail_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=connection.id,
        day=day,
        key="snapshot-product",
        source_namespace="yazio.consumed_item",
        event_kind="product",
        event_metadata={"name": "Event metadata must not replace the snapshot"},
        snapshot_name="Snapshot product",
    )
    simple_snapshot = _add_detail_event(
        db,
        user,
        provider_key="yazio",
        source_instance_id=connection.id,
        day=day,
        key="snapshot-simple-product",
        source_namespace="yazio.simple_product",
        event_kind="simple_product",
        event_metadata={"name": "Event metadata must not replace the snapshot"},
        snapshot_name="Snapshot simple product",
    )
    assert nutrition_detail_available_ids(
        db, user_id=user.id, events=[named, unnamed, unsafe, product, simple_snapshot],
    ) == {named.id, product.id, simple_snapshot.id}

    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: user)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)
    client = TestClient(app)
    daily = client.get(
        "/api/v1/analytics/verification/nutrition",
        params={"date": day.isoformat(), "view": "all"},
    )
    assert daily.status_code == 200, daily.text
    yazio_group = next(group for group in daily.json()["providers"] if group["provider_key"] == "yazio")
    events = {event["event_id"]: event for event in yazio_group["events"]}
    assert events[str(named.id)]["display_name"] == "Chicken"
    assert events[str(named.id)]["detail_available"] is True
    assert events[str(unnamed.id)]["display_name"] == "Unbenannter Eintrag"
    assert events[str(unnamed.id)]["detail_available"] is False
    assert events[str(unsafe.id)]["display_name"] == "Unbenannter Eintrag"
    assert events[str(unsafe.id)]["detail_available"] is False
    assert events[str(product.id)]["display_name"] == "Snapshot product"
    assert events[str(product.id)]["detail_available"] is True
    assert events[str(simple_snapshot.id)]["display_name"] == "Snapshot simple product"
    assert events[str(simple_snapshot.id)]["detail_available"] is True

    detail = client.get(f"{PATH}/meals/{named.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["display_name"] == "Chicken"
    assert body["dietary_energy_kcal"] == 241
    assert body["protein_g"] == 28.5
    assert body["serving_amount"] is None
    assert body["serving_unit"] is None
    assert client.get(f"{PATH}/meals/{unnamed.id}").status_code == 404
    assert client.get(f"{PATH}/meals/{unsafe.id}").status_code == 404
    simple_snapshot_detail = client.get(f"{PATH}/meals/{simple_snapshot.id}")
    assert simple_snapshot_detail.status_code == 200, simple_snapshot_detail.text
    assert simple_snapshot_detail.json()["display_name"] == "Snapshot simple product"
    product_detail = client.get(f"{PATH}/meals/{product.id}")
    assert product_detail.status_code == 200, product_detail.text
    assert product_detail.json()["display_name"] == "Snapshot product"


@pytest.mark.parametrize(
    ("provider_key", "metadata_key", "name"),
    [
        ("google_health", "food_display_name", "Google metadata food"),
        ("apple_health", "food_name", "Apple metadata food"),
    ],
)
def test_google_and_apple_detail_metadata_name_fallbacks_remain_unchanged(
    db: Session,
    user: User,
    provider_key: str,
    metadata_key: str,
    name: str,
) -> None:
    from app.analytics.nutrition_analysis import read_nutrition_meal_detail

    day = START + timedelta(days=10)
    source_instance_id = uuid4()
    event = _add_detail_event(
        db,
        user,
        provider_key=provider_key,
        source_instance_id=source_instance_id,
        day=day,
        key=f"{provider_key}-metadata-name",
        source_namespace=f"{provider_key}.nutrition_log",
        event_kind="simple_product",
        source_metadata={metadata_key: name},
    )

    detail = read_nutrition_meal_detail(
        db,
        user_id=user.id,
        event_id=event.id,
        allowed_sources=((provider_key, source_instance_id),),
    )

    assert detail is not None
    assert detail.display_name == name
