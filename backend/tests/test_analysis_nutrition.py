"""Contract tests for the canonical, source-coherent Analysis nutrition read."""

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.nutrition_analysis import read_analysis_nutrition
from app.analytics.provider_selection import NutritionProviderSelection
from app.google_health.client import (
    NutritionLog,
    NutritionLogDataPoint,
    NutritionLogInterval,
    NutritionNutrient,
    NutritionQuantity,
)
from app.importers.apple_xml import AppleFoodCorrelation, AppleFoodNutrient
from app.models import GoogleHealthConnection, User
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.metrics import canonical_unit
from app.services.analysis_demo import demo_source_instance_id, seed_analysis_demo
from app.services.apple_health_nutrition_ingestion import (
    apple_health_source_instance_id,
    create_apple_health_nutrition_run,
    persist_apple_food_correlation,
)
from app.services.google_health_nutrition_ingestion import ingest_google_health_nutrition_logs

TODAY = date(2026, 9, 29)
START = TODAY - timedelta(days=89)
FIELDS = (
    "dietary_energy_kcal",
    "protein_g",
    "carbohydrates_g",
    "fat_g",
    "fiber_g",
    "sugar_g",
    "saturated_fat_g",
)
APPLE_FIELDS = (
    ("HKQuantityTypeIdentifierDietaryEnergyConsumed", "dietary_energy_kcal", "kcal", "600"),
    ("HKQuantityTypeIdentifierDietaryProtein", "protein_g", "g", "30"),
    ("HKQuantityTypeIdentifierDietaryCarbohydrates", "carbohydrates_g", "g", "80"),
    ("HKQuantityTypeIdentifierDietaryFatTotal", "fat_g", "g", "20"),
    ("HKQuantityTypeIdentifierDietaryFiber", "fiber_g", "g", "7"),
    ("HKQuantityTypeIdentifierDietarySugar", "sugar_g", "g", "10"),
    ("HKQuantityTypeIdentifierDietaryFatSaturated", "saturated_fat_g", "g", "5"),
)


def _demo(db: Session) -> tuple[User, NutritionProviderSelection]:
    demo_id = seed_analysis_demo(db, password="synthetic-analysis-test-password", today=TODAY)
    account = db.get(User, demo_id)
    assert account is not None
    source = demo_source_instance_id(demo_id)
    return account, NutritionProviderSelection("analysis_demo", source, (("analysis_demo", source),))


def _apple_meal(
    db: Session, owner: User, *, day: date, name: str | None,
    omit: str | None = None, zero: str | None = None, record_id: str | None = None,
    run: NutritionIngestionRun | None = None,
) -> None:
    instant = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    nutrients = tuple(
        AppleFoodNutrient(
            raw_type=raw_type,
            raw_value="0" if key == zero else value,
            value=Decimal("0" if key == zero else value),
            unit=unit,
            start_at=instant,
            end_at=instant,
            source_name="Apple",
            source_version="1",
        )
        for raw_type, key, unit, value in APPLE_FIELDS if key != omit
    )
    if run is None:
        run = create_apple_health_nutrition_run(db, user_id=owner.id)
    event = persist_apple_food_correlation(
        db, user_id=owner.id, run=run, timezone=owner.timezone,
        correlation=AppleFoodCorrelation(
            source_name="Apple", source_version="1", device=None,
            start_at=instant, end_at=instant, creation_at=instant,
            food_name=name, external_uuid=record_id or f"test-{owner.id}-{day}",
            metadata=(), nutrients=nutrients,
        ),
    )
    # The importer's ordinary PARTIAL marker reflects unknown export-wide coverage;
    # this synthetic fixture explicitly represents a fully closed food record.
    event.coverage_state = CoverageState.COMPLETE.value
    source = db.get(NutritionSourceObservation, event.source_observation_id)
    assert source is not None
    source.coverage_state = CoverageState.COMPLETE.value
    for field in db.scalars(select(NutritionFieldObservation).where(
        NutritionFieldObservation.user_id == owner.id,
        NutritionFieldObservation.source_observation_id == source.id,
    )):
        field.coverage_state = CoverageState.COMPLETE.value
    run.coverage_state = CoverageState.COMPLETE.value
    run.status = "completed"
    db.commit()


def _read(db: Session, owner: User, selection: NutritionProviderSelection, first: date, last: date):
    return read_analysis_nutrition(
        db, user_id=owner.id, selection=selection, start=first, end=last,
    )


def test_incomplete_primary_falls_back_with_exact_winning_meals_and_next_day_prefers_first(
    db: Session, user: User,
) -> None:
    demo, demo_selection = _demo(db)
    first = START + timedelta(days=10)
    second = first + timedelta(days=1)
    _apple_meal(db, demo, day=first, name="Incomplete Apple bowl", omit="fiber_g")
    _apple_meal(db, demo, day=second, name="Complete Apple bowl")
    apple_source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection(
        "apple_health", apple_source,
        (("apple_health", apple_source), ("analysis_demo", demo_selection.source_instance_id)),
    )

    response = _read(db, demo, selection, first, second)

    assert response.start_date == first
    assert response.end_date == second
    assert response.period_days == 2
    assert response.selected_provider.provider_key == "apple_health"
    fallback, preferred = response.days
    assert fallback.provider_key == "analysis_demo"
    assert tuple(getattr(fallback, field) for field in FIELDS) == (
        Decimal("1760"), Decimal("69"), Decimal("223"), Decimal("61"),
        Decimal("38"), Decimal("50"), Decimal("10"),
    )
    assert [meal.display_name for meal in fallback.meals] == [
        "Haferflocken mit Beeren", "Linsensalat", "Gemüsepfanne mit Reis", "Apfel und Mandeln",
    ]
    assert [meal.meal_type for meal in fallback.meals] == ["breakfast", "lunch", "dinner", "snack"]
    assert {meal.provider_key for meal in fallback.meals} == {"analysis_demo"}
    assert tuple(getattr(fallback.meals[0], field) for field in FIELDS) == (
        Decimal("420"), Decimal("18"), Decimal("62"), Decimal("12"),
        Decimal("9"), Decimal("14"), Decimal("2"),
    )
    assert fallback.meals[0].local_time is not None
    assert preferred.provider_key == "apple_health"
    assert tuple(getattr(preferred, field) for field in FIELDS) == (
        Decimal("600"), Decimal("30"), Decimal("80"), Decimal("20"),
        Decimal("7"), Decimal("10"), Decimal("5"),
    )
    assert [(meal.provider_key, meal.display_name) for meal in preferred.meals] == [
        ("apple_health", "Complete Apple bowl"),
    ]
    assert "Incomplete Apple bowl" not in [meal.display_name for day in response.days for meal in day.meals]


def test_explicit_zero_is_present_but_missing_day_has_no_provider_or_meals(
    db: Session, user: User,
) -> None:
    demo, _ = _demo(db)
    missing = START + timedelta(days=4)  # The offline seed deliberately has no events here.
    zero_day = missing + timedelta(days=1)
    _apple_meal(db, demo, day=zero_day, name="Zero sugar food", zero="sugar_g")
    source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection("apple_health", source, (("apple_health", source),))

    empty, present = _read(db, demo, selection, missing, zero_day).days

    assert empty.date == missing
    assert empty.provider_key is None
    assert all(getattr(empty, field) is None for field in FIELDS)
    assert empty.meals == []
    assert present.provider_key == "apple_health"
    assert present.sugar_g == Decimal("0")
    assert present.meals[0].sugar_g == Decimal("0")
    assert present.protein_g == Decimal("30")


def test_unnamed_event_does_not_gain_an_invented_food_name(db: Session, user: User) -> None:
    demo, _ = _demo(db)
    day = START + timedelta(days=4)
    _apple_meal(db, demo, day=day, name=None)
    source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection("apple_health", source, (("apple_health", source),))

    result = _read(db, demo, selection, day, day).days[0]

    assert result.provider_key == "apple_health"
    assert result.dietary_energy_kcal == Decimal("600")
    assert result.meals == []


def test_foreign_user_evidence_cannot_join_an_owned_winning_day(db: Session, user: User) -> None:
    demo, demo_selection = _demo(db)
    day = START + timedelta(days=4)
    _apple_meal(db, demo, day=day, name="Owned Apple food")
    _apple_meal(db, user, day=day, name="Foreign private food")
    apple_source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection(
        "apple_health", apple_source,
        (("apple_health", apple_source), ("analysis_demo", demo_selection.source_instance_id)),
    )

    result = _read(db, demo, selection, day, day).days[0]

    assert result.provider_key == "apple_health"
    assert result.protein_g == Decimal("30")
    assert [meal.display_name for meal in result.meals] == ["Owned Apple food"]
    assert "Foreign private food" not in [meal.display_name for meal in result.meals]
    assert db.scalar(select(NutritionConsumptionEvent.id).where(
        NutritionConsumptionEvent.user_id == user.id,
        NutritionConsumptionEvent.local_date == day,
    )) is not None


def test_old_observation_tombstone_does_not_hide_current_source_revision_meal(
    db: Session, user: User,
) -> None:
    demo, _ = _demo(db)
    day = START + timedelta(days=4)
    _apple_meal(db, demo, day=day, name="Current revised Apple food")
    current = db.scalar(select(NutritionSourceObservation).where(
        NutritionSourceObservation.user_id == demo.id,
        NutritionSourceObservation.provider_key == "apple_health",
        NutritionSourceObservation.local_date == day,
    ))
    assert current is not None
    assert current.source_record_id is not None
    current.source_revision = 2
    db.flush()
    old = NutritionSourceObservation(
        user_id=demo.id,
        ingestion_run_id=current.ingestion_run_id,
        provider_key=current.provider_key,
        source_instance_id=current.source_instance_id,
        observation_kind=current.observation_kind,
        source_namespace=current.source_namespace,
        source_record_id=current.source_record_id,
        source_revision=1,
        observation_fingerprint="a" * 64,
        local_date=day,
        presence_state=current.presence_state,
        coverage_state=current.coverage_state,
        resolution_state=current.resolution_state,
        lineage_state=current.lineage_state,
    )
    db.add(old)
    db.flush()
    db.add(NutritionSourceTombstone(
        user_id=demo.id,
        provider_key=current.provider_key,
        source_instance_id=current.source_instance_id,
        source_namespace=current.source_namespace,
        source_record_id=current.source_record_id,
        source_observation_id=old.id,
        tombstone_kind="deleted",
    ))
    db.commit()
    selection = NutritionProviderSelection(
        "apple_health", current.source_instance_id,
        (("apple_health", current.source_instance_id),),
    )

    result = _read(db, demo, selection, day, day).days[0]

    assert result.provider_key == "apple_health"
    assert result.dietary_energy_kcal == Decimal("600")
    assert [meal.display_name for meal in result.meals] == ["Current revised Apple food"]


def test_record_wide_tombstone_prevents_partial_day_meals(
    db: Session, user: User,
) -> None:
    demo, _ = _demo(db)
    day = START + timedelta(days=4)
    run = create_apple_health_nutrition_run(db, user_id=demo.id)
    _apple_meal(db, demo, day=day, name="Deleted Apple food", record_id="deleted-food", run=run)
    deleted = db.scalar(select(NutritionSourceObservation).where(
        NutritionSourceObservation.user_id == demo.id,
        NutritionSourceObservation.provider_key == "apple_health",
        NutritionSourceObservation.ingestion_run_id == run.id,
        NutritionSourceObservation.source_record_id == "deleted-food",
    ))
    assert deleted is not None
    assert deleted.source_record_id is not None
    _apple_meal(db, demo, day=day, name="Surviving Apple food", record_id="surviving-food", run=run)
    db.add(NutritionSourceTombstone(
        user_id=demo.id,
        provider_key="apple_health",
        source_instance_id=deleted.source_instance_id,
        source_namespace=deleted.source_namespace,
        source_record_id=deleted.source_record_id,
        source_observation_id=None,
        tombstone_kind="deleted",
    ))
    db.commit()
    source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection("apple_health", source, (("apple_health", source),))

    result = _read(db, demo, selection, day, day).days[0]

    assert result.provider_key is None
    assert all(getattr(result, field) is None for field in FIELDS)
    assert result.meals == []


def test_partial_google_run_prevents_analysis_day_winner(
    db: Session, user: User,
) -> None:
    day = START + timedelta(days=4)
    connection = GoogleHealthConnection(
        user_id=user.id,
        client_id="synthetic-analysis-google",
        encrypted_client_secret=b"synthetic-secret",
        encrypted_refresh_token=b"synthetic-refresh-token",
        granted_scopes=["https://www.googleapis.com/auth/googlehealth.nutrition.readonly"],
        state="active",
    )
    db.add(connection)
    db.flush()

    def run(coverage: str) -> NutritionIngestionRun:
        ingestion = NutritionIngestionRun(
            user_id=user.id,
            provider_key="google_health",
            source_instance_id=connection.id,
            connector_variant="google-health-api-v4",
            requested_start_date=day,
            requested_end_date=day,
            covered_start_date=day,
            covered_end_date=day,
            status="partial" if coverage == "partial" else "completed",
            coverage_state=coverage,
        )
        db.add(ingestion)
        db.flush()
        return ingestion

    def event(
        ingestion: NutritionIngestionRun, *, key: str, name: str,
        coverage: str, values: dict[str, Decimal],
    ) -> None:
        source = NutritionSourceObservation(
            user_id=user.id,
            ingestion_run_id=ingestion.id,
            provider_key="google_health",
            source_instance_id=connection.id,
            connector_variant="google-health-api-v4",
            observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
            source_namespace="google_health.nutrition_log",
            source_record_id=key,
            source_revision=1,
            observation_fingerprint=hashlib.sha256(key.encode("utf-8")).hexdigest(),
            local_date=day,
            timezone_source="provider",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=coverage,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
            provider_metadata={"food_display_name": name},
        )
        db.add(source)
        db.flush()
        db.add(NutritionConsumptionEvent(
            user_id=user.id,
            source_observation_id=source.id,
            provider_key="google_health",
            source_instance_id=connection.id,
            event_kind="simple_product",
            logical_event_key=key,
            revision=1,
            provider_civil_datetime=datetime(day.year, day.month, day.day, 12),
            local_date=day,
            amount=Decimal("1"),
            amount_unit="serving",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=coverage,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        ))
        for metric_key, value in values.items():
            db.add(NutritionFieldObservation(
                user_id=user.id,
                source_observation_id=source.id,
                provider_field_path=f"nutritionLog.{metric_key}",
                provider_raw_unit=canonical_unit(metric_key),
                metric_key=metric_key,
                canonical_value=value,
                canonical_unit=canonical_unit(metric_key),
                observation_role=ObservationRole.CANONICAL.value,
                presence_state=PresenceState.SUPPLIED.value,
                coverage_state=coverage,
                resolution_state=ResolutionState.RESOLVED.value,
                lineage_state=LineageState.CONFIRMED.value,
            ))
        db.flush()

    event(
        run(CoverageState.COMPLETE.value),
        key="complete-google-food", name="Complete Google food",
        coverage=CoverageState.COMPLETE.value,
        values=dict(zip(FIELDS, (
            Decimal("600"), Decimal("30"), Decimal("80"), Decimal("20"),
            Decimal("7"), Decimal("10"), Decimal("5"),
        ), strict=True)),
    )
    event(
        run(CoverageState.PARTIAL.value),
        key="partial-google-food", name="Partial Google food",
        coverage=CoverageState.PARTIAL.value,
        values={"dietary_energy_kcal": Decimal("900")},
    )
    db.commit()
    selection = NutritionProviderSelection(
        "google_health", connection.id, (("google_health", connection.id),),
    )

    result = _read(db, user, selection, day, day).days[0]

    assert result.provider_key is None
    assert all(getattr(result, field) is None for field in FIELDS)
    assert result.meals == []


def test_tombstoned_google_snapshot_source_hides_meal_but_preserves_day_totals(
    db: Session, user: User,
) -> None:
    day = START + timedelta(days=4)
    connection = GoogleHealthConnection(
        user_id=user.id,
        client_id="synthetic-snapshot-tombstone",
        encrypted_client_secret=b"synthetic-secret",
        encrypted_refresh_token=b"synthetic-refresh-token",
        granted_scopes=["https://www.googleapis.com/auth/googlehealth.nutrition.readonly"],
        state="active",
    )
    db.add(connection)
    db.flush()
    instant = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    point = NutritionLogDataPoint(
        name="snapshot-tombstone-log",
        nutrition_log=NutritionLog(
            interval=NutritionLogInterval(
                start_time=instant,
                end_time=instant,
                start_utc_offset="+00:00",
                end_utc_offset="+00:00",
                civil_start_time=instant.replace(tzinfo=None),
                civil_end_time=instant.replace(tzinfo=None),
            ),
            nutrients=(
                NutritionNutrient("PROTEIN", NutritionQuantity(30.0, "g")),
                NutritionNutrient("DIETARY_FIBER", NutritionQuantity(6.0, "g")),
                NutritionNutrient("SUGAR", NutritionQuantity(8.0, "g")),
                NutritionNutrient("SATURATED_FAT", NutritionQuantity(3.0, "g")),
            ),
            energy=NutritionQuantity(500.0, "kcal"),
            total_carbohydrate=NutritionQuantity(40.0, "g"),
            total_fat=NutritionQuantity(12.0, "g"),
            food="users/me/dataTypes/food/dataPoints/snapshot-tombstone-food",
            food_display_name="Named Google bowl",
            meal_type="LUNCH",
        ),
    )
    run = ingest_google_health_nutrition_logs(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=day,
        requested_end=day,
        data_points=(point,),
        coverage_state=CoverageState.COMPLETE.value,
    )
    event = db.scalar(select(NutritionConsumptionEvent).where(
        NutritionConsumptionEvent.user_id == user.id,
        NutritionConsumptionEvent.source_instance_id == connection.id,
        NutritionConsumptionEvent.logical_event_key == point.name,
    ))
    assert event is not None
    assert event.food_snapshot_id is not None
    source = db.get(NutritionSourceObservation, event.source_observation_id)
    snapshot = db.get(NutritionFoodSnapshot, event.food_snapshot_id)
    assert source is not None
    assert snapshot is not None
    snapshot_source = db.get(NutritionSourceObservation, snapshot.source_observation_id)
    assert snapshot_source is not None
    assert snapshot.name == "Named Google bowl"
    assert snapshot_source.id != source.id
    assert snapshot_source.source_record_id == point.nutrition_log.food
    assert source.source_record_id == point.name
    assert run.coverage_state == CoverageState.COMPLETE.value
    # Food-linked imports start unresolved until linked-product processing; the
    # complete direct event totals here are the resolved synthetic day evidence.
    event.resolution_state = ResolutionState.RESOLVED.value
    source.provider_metadata = {}
    db.flush()
    selection = NutritionProviderSelection(
        "google_health", connection.id, (("google_health", connection.id),),
    )
    baseline = _read(db, user, selection, day, day).days[0]
    assert baseline.provider_key == "google_health"
    assert [meal.display_name for meal in baseline.meals] == ["Named Google bowl"]
    db.add(NutritionSourceTombstone(
        user_id=user.id,
        provider_key=snapshot_source.provider_key,
        source_instance_id=snapshot_source.source_instance_id,
        source_namespace=snapshot_source.source_namespace,
        source_record_id=snapshot_source.source_record_id,
        source_observation_id=None,
        tombstone_kind="deleted",
    ))
    db.commit()

    result = _read(db, user, selection, day, day).days[0]

    assert result.provider_key == "google_health"
    assert tuple(getattr(result, field) for field in FIELDS) == (
        Decimal("500"), Decimal("30"), Decimal("40"), Decimal("12"),
        Decimal("6"), Decimal("8"), Decimal("3"),
    )
    assert result.meals == []


def test_provider_winner_changes_on_both_sides_of_two_chunk_boundaries(
    db: Session, user: User,
) -> None:
    demo, demo_selection = _demo(db)
    for offset, name, omitted in (
        (30, "Apple incomplete 31", "fiber_g"),
        (31, "Apple complete 32", None),
        (61, "Apple incomplete 62", "fiber_g"),
        (62, "Apple complete 63", None),
    ):
        _apple_meal(db, demo, day=START + timedelta(days=offset), name=name, omit=omitted)
    apple_source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection(
        "apple_health", apple_source,
        (("apple_health", apple_source), ("analysis_demo", demo_selection.source_instance_id)),
    )

    rows = _read(db, demo, selection, START, START + timedelta(days=62)).days

    for offset, winner, meal_name in (
        (30, "analysis_demo", "Apple incomplete 31"),
        (31, "apple_health", "Apple complete 32"),
        (61, "analysis_demo", "Apple incomplete 62"),
        (62, "apple_health", "Apple complete 63"),
    ):
        row = rows[offset]
        assert row.date == START + timedelta(days=offset)
        assert row.provider_key == winner
        assert row.dietary_energy_kcal is not None
        assert row.meals
        assert {meal.provider_key for meal in row.meals} == {winner}
        names = [meal.display_name for meal in row.meals]
        if winner == "apple_health":
            assert names == [meal_name]
            assert row.dietary_energy_kcal == Decimal("600")
        else:
            assert meal_name not in names


@pytest.mark.parametrize("day", [date.min, date.max], ids=["date-min", "date-max"])
def test_named_apple_meal_is_read_at_date_boundary(
    db: Session, user: User, day: date,
) -> None:
    demo, _ = _demo(db)
    _apple_meal(db, demo, day=day, name="Boundary Apple meal")
    apple_source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection(
        "apple_health", apple_source, (("apple_health", apple_source),),
    )

    response = _read(db, demo, selection, day, day)

    assert response.start_date == day
    assert response.end_date == day
    assert len(response.days) == 1
    row = response.days[0]
    assert row.date == day
    assert row.provider_key == "apple_health"
    assert [meal.display_name for meal in row.meals] == ["Boundary Apple meal"]


@pytest.mark.parametrize("days", [31, 90, 365])
def test_inclusive_long_ranges_preserve_chunk_edge_evidence_and_missing_days(
    db: Session, user: User, days: int,
) -> None:
    demo, selection = _demo(db)
    first = START
    last = first + timedelta(days=days - 1)

    result = _read(db, demo, selection, first, last)

    assert result.period_days == days
    assert len(result.days) == days
    assert result.days[0].date == first
    assert result.days[-1].date == last
    assert result.days[0].provider_key == "analysis_demo"
    assert result.days[0].meals
    for offset in (30, 31, 61, 62):
        if offset < days:
            row = result.days[offset]
            assert row.date == first + timedelta(days=offset)
            assert row.provider_key == "analysis_demo"
            assert row.dietary_energy_kcal is not None
            assert row.meals
    if days >= 90:
        assert result.days[88].provider_key == "analysis_demo"
        assert result.days[88].meals
        assert result.days[89].provider_key is None
        assert result.days[89].meals == []
    if days == 365:
        assert result.days[-1].provider_key is None
        assert result.days[-1].meals == []
