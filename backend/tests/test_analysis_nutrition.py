"""Contract tests for the canonical, source-coherent Analysis nutrition read."""

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.analytics.nutrition_analysis as nutrition_analysis
from app.analytics.nutrition_analysis import AnalysisNutritionReadError, read_analysis_nutrition
from app.analytics.provider_selection import NutritionProviderSelection
from app.google_health.client import (
    NutritionLog,
    NutritionLogDataPoint,
    NutritionLogInterval,
    NutritionNutrient,
    NutritionQuantity,
)
from app.importers.apple_xml import AppleFoodCorrelation, AppleFoodNutrient
from app.models import GoogleHealthConnection, ImportBatch, User, UserAnalysisPreference
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
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionFieldObservation,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
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
    omit: str | None = None, omit_metrics: tuple[str, ...] = (),
    zero: str | None = None, record_id: str | None = None,
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
        for raw_type, key, unit, value in APPLE_FIELDS
        if key != omit and key not in omit_metrics
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


def test_partial_secondary_keeps_primary_day_and_winning_meals_without_blending(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo, demo_selection = _demo(db)
    first = START + timedelta(days=10)
    second = first + timedelta(days=1)
    _apple_meal(
        db, demo, day=first, name="Apple bowl with unresolved secondary metrics",
        omit_metrics=("fiber_g", "sugar_g", "saturated_fat_g"),
    )
    _apple_meal(db, demo, day=second, name="Complete Apple bowl")
    apple_source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection(
        "apple_health", apple_source,
        (("apple_health", apple_source), ("analysis_demo", demo_selection.source_instance_id)),
    )

    original_period_chunks = nutrition_analysis.iter_provider_period_chunks

    def with_partial_secondary(*args, **kwargs):
        for chunk_start, chunk_end, matrix in original_period_chunks(*args, **kwargs):
            modified = {
                candidate_day: dict(values) for candidate_day, values in matrix.items()
            }
            if kwargs["provider_key"] == "apple_health" and first in modified:
                for metric in ("fiber_g", "sugar_g", "saturated_fat_g"):
                    modified[first][metric] = SimpleNamespace(
                        value=Decimal("999"),
                        coverage_state=CoverageState.PARTIAL,
                        resolution_state=ResolutionState.UNRESOLVED,
                        presence_state=PresenceState.SUPPLIED,
                    )
            yield chunk_start, chunk_end, modified

    monkeypatch.setattr(
        nutrition_analysis, "iter_provider_period_chunks", with_partial_secondary,
    )
    response = _read(db, demo, selection, first, second)

    assert response.start_date == first
    assert response.end_date == second
    assert response.period_days == 2
    assert response.selected_provider.provider_key == "apple_health"
    partial_secondary, preferred = response.days
    assert partial_secondary.provider_key == "apple_health"
    assert tuple(getattr(partial_secondary, field) for field in FIELDS) == (
        Decimal("600"), Decimal("30"), Decimal("80"), Decimal("20"), None, None, None,
    )
    assert [(meal.provider_key, meal.display_name) for meal in partial_secondary.meals] == [
        ("apple_health", "Apple bowl with unresolved secondary metrics"),
    ]
    assert tuple(getattr(partial_secondary.meals[0], field) for field in FIELDS) == (
        Decimal("600"), Decimal("30"), Decimal("80"), Decimal("20"), None, None, None,
    )
    assert preferred.provider_key == "apple_health"
    assert tuple(getattr(preferred, field) for field in FIELDS) == (
        Decimal("600"), Decimal("30"), Decimal("80"), Decimal("20"),
        Decimal("7"), Decimal("10"), Decimal("5"),
    )
    assert [(meal.provider_key, meal.display_name) for meal in preferred.meals] == [
        ("apple_health", "Complete Apple bowl"),
    ]


def test_explicit_zero_is_present_but_missing_or_secondary_only_days_have_no_winner(
    db: Session, user: User,
) -> None:
    demo, _ = _demo(db)
    missing = START + timedelta(days=4)  # The offline seed deliberately has no events here.
    secondary_only = missing + timedelta(days=1)
    zero_day = missing + timedelta(days=2)
    _apple_meal(
        db, demo, day=secondary_only, name="Secondary-only food",
        omit_metrics=("dietary_energy_kcal", "protein_g", "carbohydrates_g", "fat_g"),
    )
    _apple_meal(db, demo, day=zero_day, name="Zero energy food", zero="dietary_energy_kcal")
    source = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection("apple_health", source, (("apple_health", source),))

    empty, secondary, present = _read(db, demo, selection, missing, zero_day).days

    assert empty.date == missing
    assert empty.provider_key is None
    assert all(getattr(empty, field) is None for field in FIELDS)
    assert empty.meals == []
    assert secondary.date == secondary_only
    assert secondary.provider_key is None
    assert all(getattr(secondary, field) is None for field in FIELDS)
    assert secondary.meals == []
    assert present.provider_key == "apple_health"
    assert present.dietary_energy_kcal == Decimal("0")
    assert present.meals[0].dietary_energy_kcal == Decimal("0")
    assert present.protein_g == Decimal("30")


@pytest.mark.parametrize(
    "resolution_state",
    (ResolutionState.CONFLICT, ResolutionState.DUPLICATE_CANDIDATE),
)
def test_unsafe_secondary_candidate_still_fails_closed_with_complete_primary(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
    resolution_state: ResolutionState,
) -> None:
    day = START + timedelta(days=10)
    candidates = {
        key: SimpleNamespace(
            value=Decimal("1"),
            coverage_state=CoverageState.COMPLETE,
            resolution_state=ResolutionState.RESOLVED,
            presence_state=PresenceState.SUPPLIED,
        )
        for key in FIELDS
    }
    candidates["fiber_g"] = SimpleNamespace(
        value=None,
        coverage_state=CoverageState.PARTIAL,
        resolution_state=resolution_state,
        presence_state=PresenceState.MISSING,
    )
    source = apple_health_source_instance_id(user.id)
    selection = NutritionProviderSelection("apple_health", source, (("apple_health", source),))

    def unsafe_secondary(*_args, **_kwargs):
        yield day, day, {day: candidates}

    monkeypatch.setattr(nutrition_analysis, "iter_provider_period_chunks", unsafe_secondary)

    with pytest.raises(AnalysisNutritionReadError):
        _read(db, user, selection, day, day)


def test_current_day_seed_supplement_is_visible_to_analysis_meals(
    db: Session, user: User,
) -> None:
    anchor = TODAY - timedelta(days=5)
    demo_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=anchor,
    )
    account = db.get(User, demo_id)
    assert account is not None
    account.active_card_ids = [
        "nutrition", "activity", "weight", "micronutrients", "dietary_energy_kcal",
    ]
    account.summary_order = ["nutrition", "activity", "weight", "micronutrients"]
    db.commit()

    def protected_base_state():
        db.expire_all()
        account = db.get(User, demo_id)
        batch = db.scalar(select(ImportBatch).where(ImportBatch.user_id == demo_id))
        base_run = db.scalar(select(NutritionIngestionRun).where(
            NutritionIngestionRun.user_id == demo_id,
            NutritionIngestionRun.connector_variant == "analysis-demo-v2",
        ))
        assert account is not None and batch is not None and base_run is not None
        sources = list(db.scalars(select(NutritionSourceObservation).where(
            NutritionSourceObservation.user_id == demo_id,
            NutritionSourceObservation.ingestion_run_id == base_run.id,
        )))
        projections = list(db.scalars(select(NutritionDailyProjection).where(
            NutritionDailyProjection.user_id == demo_id,
            NutritionDailyProjection.local_date <= anchor,
        )))
        projection_ids = [item.id for item in projections]
        facts = list(db.scalars(select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id.in_(projection_ids),
        )))
        heads = list(db.scalars(select(NutritionProjectionHead).where(
            NutritionProjectionHead.user_id == demo_id,
            NutritionProjectionHead.local_date <= anchor,
        )))
        preferences = list(db.scalars(select(UserAnalysisPreference).where(
            UserAnalysisPreference.user_id == demo_id,
        )))
        return (
            (
                account.password_hash, account.timezone, tuple(account.active_card_ids),
                tuple(account.summary_order),
            ),
            (batch.id.hex, batch.client_identifier, batch.status, batch.received, batch.inserted),
            (
                base_run.id.hex, base_run.status, base_run.requested_start_date,
                base_run.requested_end_date, tuple(sorted(base_run.provider_metadata.items())),
            ),
            tuple(sorted(
                (item.id.hex, item.source_record_id, item.observation_fingerprint)
                for item in sources
            )),
            tuple(sorted(
                (item.id.hex, item.local_date, item.input_watermark, item.projection_status)
                for item in projections
            )),
            tuple(sorted(
                (item.local_date, str(item.current_projection_id))
                for item in heads
            )),
            tuple(sorted(
                (
                    item.projection_id.hex, item.metric_key, str(item.value), item.unit,
                    item.selected_provider_key,
                )
                for item in facts
            )),
            tuple(sorted((item.analysis_key, item.position) for item in preferences)),
        )
    base_state_before = protected_base_state()
    seeded_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_current_day_supplement=True,
    )
    assert seeded_id == demo_id
    assert protected_base_state() == base_state_before
    replayed_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_current_day_supplement=True,
    )
    assert replayed_id == demo_id
    assert protected_base_state() == base_state_before
    assert len(list(db.scalars(select(ImportBatch).where(ImportBatch.user_id == demo_id)))) == 1
    assert len(list(db.scalars(
        select(NutritionIngestionRun).where(NutritionIngestionRun.user_id == demo_id)
    ))) == 2
    demo = db.get(User, demo_id)
    assert demo is not None
    source = demo_source_instance_id(demo_id)
    selection = NutritionProviderSelection(
        "analysis_demo", source, (("analysis_demo", source),),
    )

    result = _read(db, demo, selection, TODAY, TODAY)
    day = result.days[0]

    assert result.selected_provider.provider_key == "analysis_demo"
    assert day.provider_key == "analysis_demo"
    assert [meal.display_name for meal in day.meals] == [
        "Haferflocken mit Beeren", "Linsensalat", "Lachs mit Kartoffeln",
    ]
    assert [meal.meal_type for meal in day.meals] == ["breakfast", "lunch", "dinner"]
    assert tuple(getattr(day, field) for field in FIELDS) == (
        Decimal("1575"), Decimal("79"), Decimal("171"), Decimal("57"),
        Decimal("29"), Decimal("27"), Decimal("10"),
    )


def test_current_day_seed_supplement_recovers_after_projection_interruption(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import analysis_demo_nutrition as nutrition

    anchor = TODAY - timedelta(days=5)
    demo_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=anchor,
    )
    rebuild = nutrition._rebuild_analysis_demo_days

    def interrupt_projection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated projection interruption")

    monkeypatch.setattr(nutrition, "_rebuild_analysis_demo_days", interrupt_projection)
    with pytest.raises(RuntimeError, match="simulated projection interruption"):
        seed_analysis_demo(
            db, password="synthetic-analysis-test-password", today=TODAY,
            include_current_day_supplement=True,
        )

    monkeypatch.setattr(nutrition, "_rebuild_analysis_demo_days", rebuild)
    seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_current_day_supplement=True,
    )

    demo = db.get(User, demo_id)
    assert demo is not None
    source = demo_source_instance_id(demo_id)
    selection = NutritionProviderSelection(
        "analysis_demo", source, (("analysis_demo", source),),
    )
    day = _read(db, demo, selection, TODAY, TODAY).days[0]

    assert day.provider_key == "analysis_demo"
    assert [meal.display_name for meal in day.meals] == [
        "Haferflocken mit Beeren", "Linsensalat", "Lachs mit Kartoffeln",
    ]


def test_follow_up_supplement_adds_evening_pudding_and_tomorrow_meals(
    db: Session, user: User,
) -> None:
    anchor = TODAY - timedelta(days=5)
    demo_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=anchor,
    )
    seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_current_day_supplement=True,
    )
    account = db.get(User, demo_id)
    assert account is not None
    account.active_card_ids = [
        "nutrition", "activity", "weight", "micronutrients", "dietary_energy_kcal",
    ]
    account.summary_order = ["nutrition", "activity", "weight", "micronutrients"]
    db.commit()
    preference_state = (
        tuple(account.active_card_ids), tuple(account.summary_order),
        tuple(sorted(
            (row.analysis_key, row.position)
            for row in db.scalars(select(UserAnalysisPreference).where(
                UserAnalysisPreference.user_id == demo_id,
            ))
        )),
    )

    old_projection = db.scalar(select(NutritionDailyProjection).where(
        NutritionDailyProjection.user_id == demo_id,
        NutritionDailyProjection.local_date == TODAY,
        NutritionDailyProjection.projection_version == 1,
    ))
    assert old_projection is not None
    old_facts = tuple(sorted(
        (row.id.hex, row.metric_key, str(row.value))
        for row in db.scalars(select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == old_projection.id,
        ))
    ))
    old_projection_state = (old_projection.id, old_projection.input_watermark, old_facts)

    seeded_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_follow_up_meals=True,
    )
    assert seeded_id == demo_id

    demo = db.get(User, demo_id)
    assert demo is not None
    source = demo_source_instance_id(demo_id)
    selection = NutritionProviderSelection(
        "analysis_demo", source, (("analysis_demo", source),),
    )
    today = _read(db, demo, selection, TODAY, TODAY).days[0]
    tomorrow = _read(db, demo, selection, TODAY + timedelta(days=1), TODAY + timedelta(days=1)).days[0]

    assert [meal.display_name for meal in today.meals] == [
        "Haferflocken mit Beeren", "Linsensalat", "Lachs mit Kartoffeln", "Vanillepudding",
    ]
    assert [meal.meal_type for meal in today.meals] == ["breakfast", "lunch", "dinner", "snack"]
    assert tuple(getattr(today, field) for field in FIELDS) == (
        Decimal("1795"), Decimal("85"), Decimal("203"), Decimal("63"),
        Decimal("30"), Decimal("51"), Decimal("13"),
    )
    assert [meal.display_name for meal in tomorrow.meals] == [
        "Vollkornbrot mit Ei", "Kichererbsen-Bowl", "Gemüsepfanne mit Reis",
    ]
    assert tuple(getattr(tomorrow, field) for field in FIELDS) == (
        Decimal("1560"), Decimal("64"), Decimal("190"), Decimal("55"),
        Decimal("33"), Decimal("26"), Decimal("11"),
    )

    runs = list(db.scalars(select(NutritionIngestionRun).where(
        NutritionIngestionRun.user_id == demo_id,
    )))
    assert len(runs) == 3
    projection_versions = list(db.scalars(select(NutritionDailyProjection.projection_version).where(
        NutritionDailyProjection.user_id == demo_id,
        NutritionDailyProjection.local_date == TODAY,
    ).order_by(NutritionDailyProjection.projection_version)))
    assert projection_versions == [1, 2]
    assert db.scalar(select(NutritionDailyProjection.projection_version).where(
        NutritionDailyProjection.user_id == demo_id,
        NutritionDailyProjection.local_date == TODAY + timedelta(days=1),
    )) == 1
    db.refresh(old_projection)
    assert (
        old_projection.id, old_projection.input_watermark,
        tuple(sorted(
            (row.id.hex, row.metric_key, str(row.value))
            for row in db.scalars(select(NutritionDailyProjectionFact).where(
                NutritionDailyProjectionFact.projection_id == old_projection.id,
            ))
        )),
    ) == old_projection_state
    db.refresh(account)
    assert (
        tuple(account.active_card_ids), tuple(account.summary_order),
        tuple(sorted(
            (row.analysis_key, row.position)
            for row in db.scalars(select(UserAnalysisPreference).where(
                UserAnalysisPreference.user_id == demo_id,
            ))
        )),
    ) == preference_state

    replayed_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_follow_up_meals=True,
    )
    assert replayed_id == demo_id
    assert len(list(db.scalars(select(NutritionIngestionRun).where(
        NutritionIngestionRun.user_id == demo_id,
    )))) == 3
    assert len(_read(
        db, demo, selection, TODAY, TODAY,
    ).days[0].meals) == 4
    assert len(_read(
        db, demo, selection, TODAY + timedelta(days=1), TODAY + timedelta(days=1),
    ).days[0].meals) == 3


def test_follow_up_supplement_recovers_between_day_projections(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import analysis_demo_nutrition as nutrition

    anchor = TODAY - timedelta(days=5)
    demo_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=anchor,
    )
    seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_current_day_supplement=True,
    )
    rebuild = nutrition._rebuild_analysis_demo_days

    def interrupt_after_today(*args: object, **kwargs: object) -> None:
        days = kwargs["days"]
        assert isinstance(days, set)
        rebuild(*args, **{**kwargs, "days": {TODAY}})
        raise RuntimeError("simulated follow-up interruption")

    monkeypatch.setattr(nutrition, "_rebuild_analysis_demo_days", interrupt_after_today)
    with pytest.raises(RuntimeError, match="simulated follow-up interruption"):
        seed_analysis_demo(
            db, password="synthetic-analysis-test-password", today=TODAY,
            include_follow_up_meals=True,
        )

    monkeypatch.setattr(nutrition, "_rebuild_analysis_demo_days", rebuild)
    seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=TODAY,
        include_follow_up_meals=True,
    )
    runs = list(db.scalars(select(NutritionIngestionRun).where(
        NutritionIngestionRun.user_id == demo_id,
    )))
    assert len(runs) == 3
    assert sum(run.status == "completed" for run in runs) == 3
    demo = db.get(User, demo_id)
    assert demo is not None
    source = demo_source_instance_id(demo_id)
    selection = NutritionProviderSelection(
        "analysis_demo", source, (("analysis_demo", source),),
    )
    assert [meal.display_name for meal in _read(
        db, demo, selection, TODAY, TODAY,
    ).days[0].meals] == [
        "Haferflocken mit Beeren", "Linsensalat", "Lachs mit Kartoffeln", "Vanillepudding",
    ]
    assert [meal.display_name for meal in _read(
        db, demo, selection, TODAY + timedelta(days=1), TODAY + timedelta(days=1),
    ).days[0].meals] == [
        "Vollkornbrot mit Ei", "Kichererbsen-Bowl", "Gemüsepfanne mit Reis",
    ]


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
        (30, "Apple missing core 31", "protein_g"),
        (31, "Apple complete 32", None),
        (61, "Apple missing core 62", "protein_g"),
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
        (30, "analysis_demo", "Apple missing core 31"),
        (31, "apple_health", "Apple complete 32"),
        (61, "analysis_demo", "Apple missing core 62"),
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
