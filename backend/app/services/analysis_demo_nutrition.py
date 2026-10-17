"""Deterministic, offline canonical meal evidence for the seed-owned Analysis account."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import TypeVar
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import ImportBatch
from app.nutrition.enums import (
    ConsumptionEventKind,
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
    NutritionDailyProjectionLineage,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.projection.mapping import map_selection_to_fact
from app.nutrition.projection.orchestration import (
    _build_manifest,
    _collect_selections,
    rebuild_nutrition_day,
)
from app.nutrition.projection.watermark import compute_input_watermark
from app.nutrition.repositories import (
    create_ingestion_run,
    get_or_create_consumption_event,
    get_or_create_external_identity,
    get_or_create_food_profile,
    get_or_create_food_snapshot,
    get_or_create_source_observation,
)
from app.nutrition.resolution.metrics import DAILY_PROJECTION_METRICS, canonical_unit
from app.nutrition.resolution.sources import ProviderSourceBindings, ProviderSourceInstance
from app.source_priority.application import create_policy_with_rules, get_effective_policy_snapshot
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

_AuditRow = TypeVar(
    "_AuditRow",
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionExternalIdentity,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionExternalIdentityLink,
    NutritionDailyProjection,
    NutritionProjectionHead,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    SourcePriorityPolicy,
    SourcePriorityRule,
)

_PROVIDER = "analysis_demo"
_NAMESPACE = "analysis_demo.meals.v2"
_PRODUCT_NAMESPACE = "analysis_demo.products.v2"
_ID_NAMESPACE = UUID("cd99db78-a22a-49b6-9ad3-5db3d2e9b1ea")
_TIMEZONE = "Europe/Berlin"
_CURRENT_DAY_SUPPLEMENT_VARIANT = "analysis-demo-current-day-v1"
_FOLLOW_UP_SUPPLEMENT_VARIANT = "analysis-demo-follow-up-v1"
# Portions are complete food records, not day summaries. The fixed breakfast
# intentionally has exact source fields; all other days rotate real names.
_MEALS = (
    ("breakfast", "Haferflocken mit Beeren", (420, 18, 62, 12, 9, 14, 2), 180),
    ("lunch", "Linsensalat", (510, 24, 55, 19, 13, 8, 3), 120),
    ("dinner", "Gemüsepfanne mit Reis", (620, 21, 83, 20, 11, 12, 4), 210),
    ("snack", "Apfel und Mandeln", (210, 6, 23, 10, 5, 16, 1), 45),
    ("breakfast", "Vollkornbrot mit Ei", (390, 21, 37, 17, 7, 4, 5), 95),
    ("lunch", "Kichererbsen-Bowl", (550, 22, 70, 18, 15, 10, 2), 150),
    ("dinner", "Lachs mit Kartoffeln", (645, 37, 54, 26, 7, 5, 5), 125),
    ("snack", "Naturjoghurt mit Obst", (175, 10, 24, 4, 3, 17, 2), 65),
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _projection_snapshot_digest(
    db: Session, projection: NutritionDailyProjection,
) -> str:
    fact_ids = tuple(db.scalars(
        select(NutritionDailyProjectionFact.id).where(
            NutritionDailyProjectionFact.user_id == projection.user_id,
            NutritionDailyProjectionFact.projection_id == projection.id,
        )
    ))
    facts = list(db.scalars(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.user_id == projection.user_id,
            NutritionDailyProjectionFact.id.in_(fact_ids),
        )
    )) if fact_ids else []
    lineage = list(db.scalars(
        select(NutritionDailyProjectionLineage).where(
            NutritionDailyProjectionLineage.user_id == projection.user_id,
            NutritionDailyProjectionLineage.projection_fact_id.in_(fact_ids),
        )
    )) if fact_ids else []

    def values(row: object) -> dict[str, object]:
        state = inspect(row)
        assert state is not None
        return {
            attribute.key: getattr(row, attribute.key)
            for attribute in state.mapper.column_attrs
        }

    payload = {
        "projection": values(projection),
        "facts": sorted((values(row) for row in facts), key=lambda row: str(row["id"])),
        "lineage": sorted((values(row) for row in lineage), key=lambda row: str(row["id"])),
    }
    return _digest(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))



def _meal_rows(
    start: date, end: date,
) -> Iterator[tuple[date, int, str, str, dict[str, Decimal]]]:
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if offset != 10 and offset % 17 == 4:
            continue  # A genuine empty day, not a zero-valued summary.
        selected = _MEALS[:4] if offset == 10 else (
            _MEALS[(offset // 3) % 2 * 4],
            _MEALS[1 + (offset // 4) % 2 * 4],
            _MEALS[2 + (offset // 5) % 2 * 4],
            *((_MEALS[3 + (offset // 6) % 2 * 4],) if offset % 3 == 0 else ()),
        )
        for position, (daytime, name, macros, vitamin_a) in enumerate(selected):
            values = tuple(Decimal(value) for value in macros)
            if offset != 10:
                values = (
                    values[0] + (offset % 7 - 3) * 8,
                    values[1] + offset % 4,
                    values[2] + offset % 5,
                    values[3] + offset % 3,
                    values[4], values[5], values[6],
                )
            nutrients = dict(zip(DAILY_PROJECTION_METRICS, values, strict=True))
            nutrients["vitamin_a_ug"] = Decimal(vitamin_a)
            if position == 1:
                nutrients["vitamin_c_mg"] = Decimal("12")
                nutrients["folate_ug"] = Decimal("85")
                nutrients["iron_mg"] = Decimal("3")
            if position in (0, 3):
                nutrients["calcium_mg"] = Decimal("130")
            if position in (0, 2):
                nutrients["magnesium_mg"] = Decimal("45")
            nutrients["potassium_mg"] = Decimal("280")
            if position == 3 and offset % 5 == 0:
                nutrients["vitamin_d_ug"] = Decimal("0")
            yield day, position, daytime, name, nutrients


def _meal_nutrients(
    macros: tuple[int, ...], vitamin_a: int, position: int,
) -> dict[str, Decimal]:
    values = tuple(Decimal(value) for value in macros)
    nutrients = dict(zip(DAILY_PROJECTION_METRICS, values, strict=True))
    nutrients["vitamin_a_ug"] = Decimal(vitamin_a)
    if position == 1:
        nutrients["vitamin_c_mg"] = Decimal("12")
        nutrients["folate_ug"] = Decimal("85")
        nutrients["iron_mg"] = Decimal("3")
    if position == 0:
        nutrients["calcium_mg"] = Decimal("130")
    if position in (0, 2):
        nutrients["magnesium_mg"] = Decimal("45")
    nutrients["potassium_mg"] = Decimal("280")
    return nutrients


def _current_day_meal_rows(
    day: date,
) -> Iterator[tuple[date, int, str, str, dict[str, Decimal]]]:
    """Return one stable, source-backed synthetic day for the review card."""
    for position, recipe_index in enumerate((0, 1, 6)):
        daytime, name, macros, vitamin_a = _MEALS[recipe_index]
        yield day, position, daytime, name, _meal_nutrients(macros, vitamin_a, position)


def _follow_up_meal_rows(
    day: date,
) -> Iterator[tuple[date, int, str, str, dict[str, Decimal]]]:
    """Return a dessert after today's dinner and three meals for the next day."""
    pudding_macros = (220, 6, 32, 6, 1, 24, 3)
    yield day, 3, "snack", "Vanillepudding", _meal_nutrients(pudding_macros, 50, 3)
    tomorrow = day + timedelta(days=1)
    for position, recipe_index in enumerate((4, 5, 2)):
        daytime, name, macros, vitamin_a = _MEALS[recipe_index]
        yield tomorrow, position, daytime, name, _meal_nutrients(macros, vitamin_a, position)


def ensure_analysis_demo_policy(db: Session, *, user_id: UUID, start: date) -> None:
    """Create an ordinary user policy, so projections obey normal priority rules."""
    from app.services.analysis_demo import demo_user_id

    if user_id != demo_user_id():
        raise ValueError("analysis demo policy belongs to the dedicated account")
    if db.scalar(select(SourcePriorityPolicy.id).where(SourcePriorityPolicy.user_id == user_id)) is not None:
        raise ValueError("analysis demo account already has a priority policy")
    create_policy_with_rules(
        db, user_id=user_id, version=1,
        effective_from=datetime.combine(start, time.min, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, _PROVIDER, 1),),
    )


def persist_analysis_demo_nutrition(
    db: Session, *, user_id: UUID, source_instance_id: UUID, start: date, end: date,
) -> None:
    """Persist one stable run and source-backed named meals; project on clean sessions."""
    from app.models import ImportBatch
    from app.services.analysis_demo import (
        _require_disposable_environment,
        demo_source_instance_id,
        demo_user_id,
        is_owned_analysis_demo_account,
    )

    _require_disposable_environment()
    if user_id != demo_user_id() or source_instance_id != demo_source_instance_id(user_id):
        raise ValueError("analysis demo nutrition source does not belong to this account")
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError("invalid analysis demo nutrition interval")
    if (end - start).days != 89 or not is_owned_analysis_demo_account(db, user_id):
        raise ValueError("analysis demo nutrition interval must match its seed-owned account")
    batch = db.scalar(select(ImportBatch).where(ImportBatch.user_id == user_id))
    if batch is None or batch.client_identifier != f"analysis-demo:v2:{end.isoformat()}":
        raise ValueError("analysis demo nutrition interval must match the immutable seed anchor")
    marker = f"{start.isoformat()}:{end.isoformat()}"
    run_id = uuid5(_ID_NAMESPACE, f"analysis-demo:nutrition:run:v2:{user_id}:{marker}")
    run = db.get(NutritionIngestionRun, run_id)
    if run is not None:
        if not (
            run.user_id == user_id and run.provider_key == _PROVIDER
            and run.source_instance_id == source_instance_id
            and run.requested_start_date == start and run.requested_end_date == end
            and run.status in {"running", "completed"}
        ):
            raise ValueError("analysis demo nutrition run identity collision")
        missing = audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end)
        if run.status == "completed":
            return
        # The ownership/evidence/projection audit must finish its read
        # transaction before independent per-day projection sessions write.
        db.commit()
        _finish_analysis_demo_projections(
            db, run=run, user_id=user_id, source_instance_id=source_instance_id,
            start=start, end=end, days=missing,
        )
        return
    if db.scalar(select(NutritionIngestionRun.id).where(
        NutritionIngestionRun.user_id == user_id,
    ).limit(1)) is not None:
        raise ValueError("analysis demo nutrition already has a different ingestion run")
    run = create_ingestion_run(
        db, user_id=user_id, provider_key=_PROVIDER,
        source_instance_id=source_instance_id, connector_variant="analysis-demo-v2",
        requested_start_date=start, requested_end_date=end,
        covered_start_date=start, covered_end_date=end,
        status="running", coverage_state=CoverageState.PARTIAL.value,
        provider_metadata={"seed": marker},
    )
    run.id = run_id
    db.flush()
    zone = ZoneInfo(_TIMEZONE)
    for day, position, daytime, name, nutrients in _meal_rows(start, end):
        _persist_demo_meal(
            db, run=run, user_id=user_id, source_instance_id=source_instance_id,
            zone=zone, day=day, position=position, daytime=daytime,
            name=name, nutrients=nutrients,
        )
    db.flush()
    # Account, policy, run and all source evidence commit together. Projection
    # sessions cannot read uncommitted rows and may fail independently.
    db.commit()
    _finish_analysis_demo_projections(
        db, run=run, user_id=user_id, source_instance_id=source_instance_id,
        start=start, end=end, days={day for day, *_ in _meal_rows(start, end)},
    )




def _persist_demo_meal(
    db: Session, *, run: NutritionIngestionRun, user_id: UUID,
    source_instance_id: UUID, zone: ZoneInfo, day: date, position: int,
    daytime: str, name: str, nutrients: dict[str, Decimal],
) -> None:
    record_key = f"{day.isoformat()}:{position}"
    payload = "|".join(
        (record_key, daytime, name, *(f"{key}={value}" for key, value in nutrients.items()))
    )
    fingerprint = _digest(payload)
    civil = datetime.combine(day, time(7 + position * 4, 30))
    canonical_at = civil.replace(tzinfo=zone).astimezone(UTC)
    profile_fingerprint = _digest(f"product|{record_key}|{daytime}|{name}")
    profile_observation = get_or_create_source_observation(
        db, user_id=user_id, ingestion_run_id=run.id,
        provider_key=_PROVIDER, source_instance_id=source_instance_id,
        source_namespace=_PRODUCT_NAMESPACE, source_record_id=record_key,
        source_revision=1, observation_fingerprint=profile_fingerprint,
        observation_kind=ObservationKind.PRODUCT_PROFILE.value,
        local_date=None, payload_hash=profile_fingerprint,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    observation = get_or_create_source_observation(
        db, user_id=user_id, ingestion_run_id=run.id,
        provider_key=_PROVIDER, source_instance_id=source_instance_id,
        source_namespace=_NAMESPACE, source_record_id=record_key,
        source_revision=1, observation_fingerprint=fingerprint,
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        local_date=day, provider_civil_datetime=civil,
        provider_timezone=_TIMEZONE, canonical_start_at=canonical_at,
        canonical_end_at=canonical_at, timezone_source="provider",
        time_confidence="exact", payload_hash=fingerprint,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    identity = get_or_create_external_identity(
        db, user_id=user_id, provider_key=_PROVIDER,
        namespace=_PRODUCT_NAMESPACE, identity_value=record_key,
        identity_kind="product", source_instance_id=source_instance_id,
    )
    profile = get_or_create_food_profile(
        db, user_id=user_id, provider_key=_PROVIDER,
        source_instance_id=source_instance_id, external_identity_id=identity.id,
    )
    snapshot = get_or_create_food_snapshot(
        db, user_id=user_id, food_profile_id=profile.id,
        source_observation_id=profile_observation.id, content_hash=profile_fingerprint,
        name=name, category=daytime, base_unit="portion", language="de",
        is_verified=True, is_private=True, is_deleted=False,
    )
    get_or_create_consumption_event(
        db, user_id=user_id, source_observation_id=observation.id,
        provider_key=_PROVIDER, source_instance_id=source_instance_id,
        event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value,
        logical_event_key=record_key, food_snapshot_id=snapshot.id,
        provider_civil_datetime=civil, provider_timezone=_TIMEZONE,
        canonical_start_at=canonical_at, canonical_end_at=canonical_at,
        local_date=day, daytime=daytime, amount=Decimal("1"),
        amount_unit="portion", presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
        content_hash=fingerprint,
    )
    db.add_all(
        NutritionFieldObservation(
            user_id=user_id, source_observation_id=observation.id,
            provider_field_path=f"nutrients.{metric}",
            provider_raw_value_decimal=value,
            provider_raw_unit=canonical_unit(metric),
            metric_key=metric, canonical_value=value,
            canonical_unit=canonical_unit(metric),
            observation_role=ObservationRole.CANONICAL.value,
            presence_state=(PresenceState.EXPLICIT_ZERO if value == 0 else PresenceState.SUPPLIED).value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
        for metric, value in nutrients.items()
    )


def _rebuild_analysis_demo_days(
    db: Session, *, user_id: UUID, source_instance_id: UUID,
    policy_at: datetime, days: set[date],
) -> None:
    from app.services.analysis_demo_nutrition_resolution import AnalysisDemoMetricResolver

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    bindings = ProviderSourceBindings((ProviderSourceInstance(_PROVIDER, source_instance_id),))
    registry = {_PROVIDER: AnalysisDemoMetricResolver()}
    for day in sorted(days):
        with factory() as projection_db:
            rebuild_nutrition_day(
                projection_db, user_id=user_id, local_date=day,
                policy_at=policy_at,
                provider_sources=bindings, resolver_registry=registry,
            )


def _finish_analysis_demo_projections(
    db: Session, *, run: NutritionIngestionRun, user_id: UUID,
    source_instance_id: UUID, start: date, end: date, days: set[date],
) -> None:
    from app.services.analysis_demo import AnalysisDemoOwnershipError

    _rebuild_analysis_demo_days(
        db, user_id=user_id, source_instance_id=source_instance_id,
        policy_at=datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC),
        days=days,
    )
    if audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end):
        raise AnalysisDemoOwnershipError("Demo-Projektionen sind unvollständig.")
    run.status = "completed"
    db.commit()




def persist_analysis_demo_current_day_supplement(
    db: Session, *, user_id: UUID, source_instance_id: UUID,
    start: date, end: date, target_day: date,
) -> None:
    """Add one immutable, audited current-day supplement without rewriting history."""
    from app.models import ImportBatch
    from app.services.analysis_demo import (
        AnalysisDemoOwnershipError,
        _require_disposable_environment,
        demo_source_instance_id,
        demo_user_id,
        is_owned_analysis_demo_account,
    )

    _require_disposable_environment()
    if (
        user_id != demo_user_id()
        or source_instance_id != demo_source_instance_id(user_id)
        or type(start) is not date
        or type(end) is not date
        or type(target_day) is not date
        or (end - start).days != 89
        or not is_owned_analysis_demo_account(db, user_id)
    ):
        raise ValueError("invalid analysis demo current-day supplement scope")
    batch = db.scalar(select(ImportBatch).where(ImportBatch.user_id == user_id))
    marker = f"{start.isoformat()}:{end.isoformat()}"
    if batch is None or batch.client_identifier != f"analysis-demo:v2:{end.isoformat()}":
        raise ValueError("analysis demo supplement must match its immutable seed anchor")

    missing = audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end)
    base_days = {day for day, *_ in _meal_rows(start, end)}
    supplements = [
        item
        for item in db.scalars(
            select(NutritionIngestionRun).where(NutritionIngestionRun.user_id == user_id)
        )
        if item.connector_variant == _CURRENT_DAY_SUPPLEMENT_VARIANT
    ]
    if len(supplements) > 1:
        raise AnalysisDemoOwnershipError("Mehrere Analyse-Demo-Tages-Supplements vorhanden.")
    if not supplements and target_day in base_days:
        if missing:
            raise AnalysisDemoOwnershipError("Demo-Grundprojektionen sind unvollständig.")
        return

    supplement_id = uuid5(
        _ID_NAMESPACE,
        f"analysis-demo:nutrition:supplement:v1:{user_id}:{marker}:{target_day.isoformat()}",
    )
    metadata = {
        "base_seed": marker,
        "supplement": "current-day-v1",
        "target_date": target_day.isoformat(),
    }
    if supplements:
        run = supplements[0]
        if (
            run.id != supplement_id
            or run.provider_key != _PROVIDER
            or run.source_instance_id != source_instance_id
            or run.requested_start_date != target_day
            or run.requested_end_date != target_day
            or run.covered_start_date != target_day
            or run.covered_end_date != target_day
            or run.provider_metadata != metadata
            or run.status not in {"running", "completed"}
        ):
            raise AnalysisDemoOwnershipError("Analyse-Demo-Tages-Supplement stimmt nicht mit dem Seed überein.")
        if run.status == "completed":
            return
        if missing - {target_day}:
            raise AnalysisDemoOwnershipError("Demo-Projektionen sind unvollständig.")
        db.commit()
    else:
        if missing:
            raise AnalysisDemoOwnershipError("Demo-Grundprojektionen sind unvollständig.")
        run = create_ingestion_run(
            db, user_id=user_id, provider_key=_PROVIDER,
            source_instance_id=source_instance_id,
            connector_variant=_CURRENT_DAY_SUPPLEMENT_VARIANT,
            requested_start_date=target_day, requested_end_date=target_day,
            covered_start_date=target_day, covered_end_date=target_day,
            status="running", coverage_state=CoverageState.COMPLETE.value,
            provider_metadata=metadata,
        )
        run.id = supplement_id
        db.flush()
        zone = ZoneInfo(_TIMEZONE)
        for day, position, daytime, name, nutrients in _current_day_meal_rows(target_day):
            _persist_demo_meal(
                db, run=run, user_id=user_id, source_instance_id=source_instance_id,
                zone=zone, day=day, position=position, daytime=daytime,
                name=name, nutrients=nutrients,
            )
        db.commit()

    _rebuild_analysis_demo_days(
        db, user_id=user_id, source_instance_id=source_instance_id,
        policy_at=datetime.combine(target_day + timedelta(days=1), time.min, tzinfo=UTC),
        days={target_day},
    )
    run.status = "completed"
    db.flush()
    if audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end):
        raise AnalysisDemoOwnershipError("Demo-Tagesprojektion ist unvollständig.")
    db.commit()



def persist_analysis_demo_follow_up_meals(
    db: Session, *, user_id: UUID, source_instance_id: UUID,
    start: date, end: date, target_day: date,
) -> None:
    """Add one exact, recoverable follow-up run after the current-day supplement."""
    from app.services.analysis_demo import (
        AnalysisDemoOwnershipError,
        _require_disposable_environment,
        demo_source_instance_id,
        demo_user_id,
        is_owned_analysis_demo_account,
    )

    _require_disposable_environment()
    if (
        user_id != demo_user_id()
        or source_instance_id != demo_source_instance_id(user_id)
        or type(start) is not date
        or type(end) is not date
        or type(target_day) is not date
        or (end - start).days != 89
        or not is_owned_analysis_demo_account(db, user_id)
    ):
        raise ValueError("invalid analysis demo follow-up scope")
    batch = db.scalar(select(ImportBatch).where(ImportBatch.user_id == user_id))
    marker = f"{start.isoformat()}:{end.isoformat()}"
    if batch is None or batch.client_identifier != f"analysis-demo:v2:{end.isoformat()}":
        raise ValueError("analysis demo follow-up must match its immutable seed anchor")

    missing = audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end)
    all_runs = list(db.scalars(select(NutritionIngestionRun).where(
        NutritionIngestionRun.user_id == user_id,
    )))
    current_runs = [run for run in all_runs if run.connector_variant == _CURRENT_DAY_SUPPLEMENT_VARIANT]
    follow_up_runs = [run for run in all_runs if run.connector_variant == _FOLLOW_UP_SUPPLEMENT_VARIANT]
    tomorrow = target_day + timedelta(days=1)
    base_days = {day for day, *_ in _meal_rows(start, end)}
    if (
        len(current_runs) != 1
        or current_runs[0].status != "completed"
        or current_runs[0].requested_start_date != target_day
        or current_runs[0].provider_metadata != {
            "base_seed": marker, "supplement": "current-day-v1", "target_date": target_day.isoformat(),
        }
        or target_day in base_days or tomorrow in base_days
        or len(follow_up_runs) > 1
    ):
        raise AnalysisDemoOwnershipError("Voraussetzungen für den Analyse-Demo-Folgelauf stimmen nicht.")

    run = follow_up_runs[0] if follow_up_runs else None
    if run is None:
        if missing:
            raise AnalysisDemoOwnershipError("Demo-Grundprojektionen sind unvollständig.")
        head = db.scalar(select(NutritionProjectionHead).where(
            NutritionProjectionHead.user_id == user_id,
            NutritionProjectionHead.local_date == target_day,
        ))
        previous = (
            db.get(NutritionDailyProjection, head.current_projection_id)
            if head is not None and head.current_projection_id is not None else None
        )
        if (
            previous is None
            or previous.projection_version != 1
            or previous.local_date != target_day
        ):
            raise AnalysisDemoOwnershipError("Aktuelle Demo-Projektion kann nicht erweitert werden.")
        predecessor_digest = _projection_snapshot_digest(db, previous)
        metadata = {
            "base_seed": marker,
            "supplement": "follow-up-v1",
            "target_date": target_day.isoformat(),
            "next_date": tomorrow.isoformat(),
            "predecessor_projection_id": str(previous.id),
            "predecessor_projection_digest": predecessor_digest,
        }
        run = create_ingestion_run(
            db, user_id=user_id, provider_key=_PROVIDER,
            source_instance_id=source_instance_id,
            connector_variant=_FOLLOW_UP_SUPPLEMENT_VARIANT,
            requested_start_date=target_day, requested_end_date=tomorrow,
            covered_start_date=target_day, covered_end_date=tomorrow,
            status="running", coverage_state=CoverageState.COMPLETE.value,
            provider_metadata=metadata,
        )
        run.id = uuid5(
            _ID_NAMESPACE,
            f"analysis-demo:nutrition:follow-up:v1:{user_id}:{marker}:{target_day.isoformat()}:{predecessor_digest}",
        )
        db.flush()
        zone = ZoneInfo(_TIMEZONE)
        for day, position, daytime, name, nutrients in _follow_up_meal_rows(target_day):
            _persist_demo_meal(
                db, run=run, user_id=user_id, source_instance_id=source_instance_id,
                zone=zone, day=day, position=position, daytime=daytime,
                name=name, nutrients=nutrients,
            )
        db.commit()
    elif (
        run.status not in {"running", "completed"}
        or run.requested_start_date != target_day
        or run.provider_metadata.get("target_date") != target_day.isoformat()
    ):
        raise AnalysisDemoOwnershipError("Analyse-Demo-Folgelauf stimmt nicht mit dem Zieltag überein.")

    missing = audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end)
    if missing - {target_day, tomorrow}:
        raise AnalysisDemoOwnershipError("Demo-Projektionen sind unvollständig.")
    if run.status == "completed":
        if missing:
            raise AnalysisDemoOwnershipError("Abgeschlossener Demo-Folgelauf hat unvollständige Projektionen.")
        return
    db.commit()
    if missing:
        _rebuild_analysis_demo_days(
            db, user_id=user_id, source_instance_id=source_instance_id,
            policy_at=datetime.combine(target_day + timedelta(days=1), time.min, tzinfo=UTC),
            days=missing,
        )
    db.expire_all()
    if audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end):
        raise AnalysisDemoOwnershipError("Demo-Folgeprojektionen sind unvollständig.")
    run.status = "completed"
    db.flush()
    if audit_analysis_demo_nutrition(db, user_id=user_id, start=start, end=end):
        raise AnalysisDemoOwnershipError("Demo-Folgeprojektionen sind unvollständig.")
    db.commit()
def audit_analysis_demo_nutrition(
    db: Session, *, user_id: UUID, start: date, end: date,
) -> set[date]:
    """Audit all evidence and every present projection; return only missing days."""
    from app.services.analysis_demo import (
        AnalysisDemoOwnershipError,
        _same_time,
        demo_source_instance_id,
    )

    source_id = demo_source_instance_id(user_id)

    def rows(model: type[_AuditRow]) -> list[_AuditRow]:
        return list(db.scalars(select(model).where(model.user_id == user_id)))

    run_id = uuid5(_ID_NAMESPACE, f"analysis-demo:nutrition:run:v2:{user_id}:{start.isoformat()}:{end.isoformat()}")
    runs = rows(NutritionIngestionRun)
    base_runs = [item for item in runs if item.id == run_id]
    other_runs = [item for item in runs if item.id != run_id]
    current_supplements = [
        item for item in other_runs if item.connector_variant == _CURRENT_DAY_SUPPLEMENT_VARIANT
    ]
    follow_up_runs = [
        item for item in other_runs if item.connector_variant == _FOLLOW_UP_SUPPLEMENT_VARIANT
    ]
    unknown_runs = [
        item for item in other_runs
        if item.connector_variant not in {
            _CURRENT_DAY_SUPPLEMENT_VARIANT, _FOLLOW_UP_SUPPLEMENT_VARIANT,
        }
    ]
    if (
        len(base_runs) != 1 or len(current_supplements) > 1
        or len(follow_up_runs) > 1 or unknown_runs
    ):
        raise AnalysisDemoOwnershipError("Demo-Ernährungsimport ist nicht seed-exklusiv.")
    base_run = base_runs[0]
    if not (
        base_run.provider_key == _PROVIDER
        and base_run.source_instance_id == source_id
        and base_run.requested_start_date == start and base_run.requested_end_date == end
        and base_run.covered_start_date == start and base_run.covered_end_date == end
        and base_run.coverage_state == CoverageState.PARTIAL.value
        and base_run.provider_metadata == {"seed": f"{start.isoformat()}:{end.isoformat()}"}
        and base_run.status in {"running", "completed"}
        and base_run.connector_variant == "analysis-demo-v2"
    ):
        raise AnalysisDemoOwnershipError("Demo-Ernährungsimport ist nicht seed-exklusiv.")
    marker = f"{start.isoformat()}:{end.isoformat()}"
    base_seed_days = {day for day, *_ in _meal_rows(start, end)}
    supplement_run = current_supplements[0] if current_supplements else None
    supplement_day = None
    if supplement_run is not None:
        supplement_metadata = supplement_run.provider_metadata
        if not isinstance(supplement_metadata, dict):
            raise AnalysisDemoOwnershipError("Demo-Tages-Supplement hat ungültige Metadaten.")
        target_value = supplement_metadata.get("target_date")
        if not isinstance(target_value, str):
            raise AnalysisDemoOwnershipError("Demo-Tages-Supplement hat kein gültiges Ziel-Datum.")
        try:
            supplement_day = date.fromisoformat(target_value)
        except ValueError:
            raise AnalysisDemoOwnershipError("Demo-Tages-Supplement hat kein gültiges Ziel-Datum.") from None
        expected_supplement_id = uuid5(
            _ID_NAMESPACE,
            f"analysis-demo:nutrition:supplement:v1:{user_id}:{marker}:{supplement_day.isoformat()}",
        )
        if not (
            supplement_run.id == expected_supplement_id
            and supplement_run.provider_key == _PROVIDER
            and supplement_run.source_instance_id == source_id
            and supplement_run.connector_variant == _CURRENT_DAY_SUPPLEMENT_VARIANT
            and supplement_run.requested_start_date == supplement_day
            and supplement_run.requested_end_date == supplement_day
            and supplement_run.covered_start_date == supplement_day
            and supplement_run.covered_end_date == supplement_day
            and supplement_run.coverage_state == CoverageState.COMPLETE.value
            and supplement_metadata == {
                "base_seed": marker,
                "supplement": "current-day-v1",
                "target_date": supplement_day.isoformat(),
            }
            and supplement_run.status in {"running", "completed"}
            and all(day != supplement_day for day, *_ in _meal_rows(start, end))
        ):
            raise AnalysisDemoOwnershipError("Demo-Tages-Supplement stimmt nicht mit dem Seed überein.")

    follow_up_run = follow_up_runs[0] if follow_up_runs else None
    follow_up_day = follow_up_tomorrow = None
    predecessor_projection_id = None
    predecessor_digest = None
    if follow_up_run is not None:
        metadata = follow_up_run.provider_metadata
        if (
            supplement_run is None or supplement_day is None or supplement_run.status != "completed"
            or not isinstance(metadata, dict)
            or not isinstance(metadata.get("target_date"), str)
            or not isinstance(metadata.get("next_date"), str)
            or not isinstance(metadata.get("predecessor_projection_id"), str)
            or not isinstance(metadata.get("predecessor_projection_digest"), str)
        ):
            raise AnalysisDemoOwnershipError("Demo-Folgelauf hat ungültige Voraussetzungen oder Metadaten.")
        try:
            follow_up_day = date.fromisoformat(metadata["target_date"])
            follow_up_tomorrow = date.fromisoformat(metadata["next_date"])
            predecessor_projection_id = UUID(metadata["predecessor_projection_id"])
        except ValueError:
            raise AnalysisDemoOwnershipError("Demo-Folgelauf hat ungültige Datums- oder Projektionsmetadaten.") from None
        predecessor_digest = metadata["predecessor_projection_digest"]
        expected_follow_up_id = uuid5(
            _ID_NAMESPACE,
            f"analysis-demo:nutrition:follow-up:v1:{user_id}:{marker}:{follow_up_day.isoformat()}:{predecessor_digest}",
        )
        if not (
            follow_up_day == supplement_day
            and follow_up_tomorrow == follow_up_day + timedelta(days=1)
            and follow_up_day not in base_seed_days
            and follow_up_tomorrow not in base_seed_days
            and follow_up_run.id == expected_follow_up_id
            and follow_up_run.provider_key == _PROVIDER
            and follow_up_run.source_instance_id == source_id
            and follow_up_run.connector_variant == _FOLLOW_UP_SUPPLEMENT_VARIANT
            and follow_up_run.requested_start_date == follow_up_day
            and follow_up_run.requested_end_date == follow_up_tomorrow
            and follow_up_run.covered_start_date == follow_up_day
            and follow_up_run.covered_end_date == follow_up_tomorrow
            and follow_up_run.coverage_state == CoverageState.COMPLETE.value
            and metadata == {
                "base_seed": marker,
                "supplement": "follow-up-v1",
                "target_date": follow_up_day.isoformat(),
                "next_date": follow_up_tomorrow.isoformat(),
                "predecessor_projection_id": str(predecessor_projection_id),
                "predecessor_projection_digest": predecessor_digest,
            }
            and len(predecessor_digest) == 64
            and all(character in "0123456789abcdef" for character in predecessor_digest)
            and follow_up_run.status in {"running", "completed"}
        ):
            raise AnalysisDemoOwnershipError("Demo-Folgelauf stimmt nicht mit dem Seed überein.")
    policies, rules = rows(SourcePriorityPolicy), rows(SourcePriorityRule)
    if (
        len(policies) != 1 or policies[0].version != 1
        or not _same_time(policies[0].effective_from, datetime.combine(start, time.min, tzinfo=UTC))
        or len(rules) != 1 or rules[0].policy_id != policies[0].id
        or rules[0].data_area != "nutrition" or rules[0].metric_key is not None
        or rules[0].provider_key != _PROVIDER or rules[0].priority_rank != 1
    ):
        raise AnalysisDemoOwnershipError("Demo-Quellenpriorität wurde verändert.")
    expected = {
        f"{day.isoformat()}:{position}": (day, position, daytime, name, nutrients, run_id)
        for day, position, daytime, name, nutrients in _meal_rows(start, end)
    }
    if supplement_run is not None and supplement_day is not None:
        for day, position, daytime, name, nutrients in _current_day_meal_rows(supplement_day):
            key = f"{day.isoformat()}:{position}"
            if key in expected:
                raise AnalysisDemoOwnershipError("Demo-Tages-Supplement überschneidet den Grundseed.")
            expected[key] = (day, position, daytime, name, nutrients, supplement_run.id)
    if follow_up_run is not None and follow_up_day is not None:
        for day, position, daytime, name, nutrients in _follow_up_meal_rows(follow_up_day):
            key = f"{day.isoformat()}:{position}"
            if key in expected:
                raise AnalysisDemoOwnershipError("Demo-Folgelauf überschneidet vorhandene Mahlzeiten.")
            expected[key] = (day, position, daytime, name, nutrients, follow_up_run.id)
    observations = rows(NutritionSourceObservation)
    by_key = {(row.source_namespace, row.source_record_id): row for row in observations}
    expected_sources = {
        (namespace, key)
        for key in expected
        for namespace in (_NAMESPACE, _PRODUCT_NAMESPACE)
    }
    if len(observations) != 2 * len(expected) or set(by_key) != expected_sources:
        raise AnalysisDemoOwnershipError("Demo-Ernährungsbelege fehlen oder sind fremd.")
    for key, (day, _position, daytime, name, nutrients, expected_run_id) in expected.items():
        event_source = by_key[_NAMESPACE, key]
        profile_source = by_key[_PRODUCT_NAMESPACE, key]
        payload = "|".join((key, daytime, name, *(f"{metric}={value}" for metric, value in nutrients.items())))
        digest = _digest(payload)
        profile_digest = _digest(f"product|{key}|{daytime}|{name}")
        civil = datetime.combine(day, time(7 + int(key.rsplit(":", 1)[1]) * 4, 30))
        canonical_at = civil.replace(tzinfo=ZoneInfo(_TIMEZONE)).astimezone(UTC)
        for source, expected_digest in ((event_source, digest), (profile_source, profile_digest)):
            if not (
                source.ingestion_run_id == expected_run_id and source.provider_key == _PROVIDER
                and source.source_instance_id == source_id and source.source_revision == 1
                and source.observation_fingerprint == expected_digest
                and source.payload_hash == expected_digest
                and source.connector_variant is None and source.provider_metadata == {}
                and source.presence_state == PresenceState.SUPPLIED.value
                and source.coverage_state == CoverageState.COMPLETE.value
                and source.resolution_state == ResolutionState.RESOLVED.value
                and source.lineage_state == LineageState.CONFIRMED.value
            ):
                raise AnalysisDemoOwnershipError("Demo-Ernährungsbeleg wurde verändert.")
        if not (
            event_source.observation_kind == ObservationKind.CONSUMPTION_EVENT.value
            and event_source.local_date == day
            and event_source.provider_civil_datetime == civil
            and event_source.provider_timezone == _TIMEZONE
            and event_source.timezone_source == "provider"
            and event_source.time_confidence == "exact"
            and event_source.canonical_start_at is not None
            and _same_time(event_source.canonical_start_at, canonical_at)
            and event_source.canonical_end_at is not None
            and _same_time(event_source.canonical_end_at, canonical_at)
            and profile_source.observation_kind == ObservationKind.PRODUCT_PROFILE.value
            and profile_source.local_date is None
            and profile_source.provider_civil_datetime is None
            and profile_source.provider_timezone is None
            and profile_source.canonical_start_at is None
            and profile_source.canonical_end_at is None
            and profile_source.timezone_source is None
            and profile_source.time_confidence is None
        ):
            raise AnalysisDemoOwnershipError("Demo-Ernährungsbeleg wurde verändert.")
    identities, profiles, snapshots = (
        rows(NutritionExternalIdentity), rows(NutritionFoodProfile), rows(NutritionFoodSnapshot)
    )
    events, fields, links = (
        rows(NutritionConsumptionEvent), rows(NutritionFieldObservation),
        rows(NutritionExternalIdentityLink),
    )
    if not (
        len(identities) == len(profiles) == len(snapshots) == len(events)
        == len(links) == len(expected)
        and len(fields) == sum(len(item[4]) for item in expected.values())
    ):
        raise AnalysisDemoOwnershipError("Demo-Ernährungsidentitäten sind nicht exklusiv.")
    identity_by_id = {item.id: item for item in identities}
    profile_by_id = {item.id: item for item in profiles}
    snapshot_by_id = {item.id: item for item in snapshots}
    event_by_source = {item.source_observation_id: item for item in events}
    field_by_source: dict[UUID, dict[str, NutritionFieldObservation]] = {}
    for field in fields:
        if field.metric_key is None:
            raise AnalysisDemoOwnershipError("Demo-Nährwertbeleg wurde verändert.")
        field_by_source.setdefault(field.source_observation_id, {})[field.metric_key] = field
    profile_links = {item.food_profile_id: item for item in links}
    if (
        len(event_by_source) != len(expected)
        or len(profile_links) != len(expected)
        or sum(map(len, field_by_source.values())) != len(fields)
    ):
        raise AnalysisDemoOwnershipError("Demo-Ernährungslinks sind nicht eindeutig.")
    matched_snapshots: set[UUID] = set()
    matched_profiles: set[UUID] = set()
    matched_links: set[UUID] = set()
    matched_identities: set[UUID] = set()
    for key, (day, _position, daytime, name, nutrients, _expected_run_id) in expected.items():
        source = by_key[_NAMESPACE, key]
        product_source = by_key[_PRODUCT_NAMESPACE, key]
        event = event_by_source.get(source.id)
        snapshot = (
            snapshot_by_id.get(event.food_snapshot_id)
            if event is not None and event.food_snapshot_id is not None else None
        )
        profile = profile_by_id.get(snapshot.food_profile_id) if snapshot is not None else None
        link = profile_links.get(profile.id) if profile is not None else None
        identity = identity_by_id.get(link.external_identity_id) if link is not None else None
        recorded = field_by_source.get(source.id, {})
        civil = datetime.combine(day, time(7 + int(key.rsplit(":", 1)[1]) * 4, 30))
        canonical_at = civil.replace(tzinfo=ZoneInfo(_TIMEZONE)).astimezone(UTC)
        if not (
            event is not None and snapshot is not None and profile is not None
            and link is not None and identity is not None
            and event.provider_key == _PROVIDER and event.source_instance_id == source_id
            and event.event_kind == ConsumptionEventKind.SIMPLE_PRODUCT.value
            and event.local_date == day and event.daytime == daytime
            and event.logical_event_key == key and event.revision == 1
            and event.supersedes_event_id is None and event.supersedes_revision is None
            and event.provider_civil_datetime == civil and event.provider_timezone == _TIMEZONE
            and event.canonical_start_at is not None and _same_time(event.canonical_start_at, canonical_at)
            and event.canonical_end_at is not None and _same_time(event.canonical_end_at, canonical_at)
            and event.amount == 1 and event.amount_unit == "portion"
            and event.presence_state == PresenceState.SUPPLIED.value
            and event.coverage_state == CoverageState.COMPLETE.value
            and event.resolution_state == ResolutionState.RESOLVED.value
            and event.lineage_state == LineageState.CONFIRMED.value
            and event.provider_metadata == {"content_hash": source.observation_fingerprint}
            and snapshot.source_observation_id == product_source.id and snapshot.name == name
            and snapshot.content_hash == product_source.observation_fingerprint
            and snapshot.category == daytime and snapshot.base_unit == "portion"
            and snapshot.language == "de" and snapshot.is_verified is True
            and snapshot.is_private is True and snapshot.is_deleted is False
            and snapshot.provider_revision is None and snapshot.producer is None
            and snapshot.provider_updated_at is None and snapshot.provider_metadata == {}
            and profile.provider_key == _PROVIDER and profile.source_instance_id == source_id
            and profile.current_snapshot_id == snapshot.id and profile.profile_status == "active"
            and link.link_role == "profile_identity" and link.link_revision == 1
            and link.supersedes_link_id is None and link.supersedes_link_revision is None
            and link.consumption_event_id is None and link.source_observation_id is None
            and identity.provider_key == _PROVIDER and identity.source_instance_id == source_id
            and identity.namespace == _PRODUCT_NAMESPACE and identity.identity_value == key
            and identity.identity_kind == "product"
            and identity.provider_metadata == {}
            and set(recorded) == set(nutrients)
        ):
            raise AnalysisDemoOwnershipError("Demo-Mahlzeit stimmt nicht mit dem Seed überein.")
        matched_snapshots.add(snapshot.id)
        matched_profiles.add(profile.id)
        matched_links.add(link.id)
        matched_identities.add(identity.id)
        for metric, value in nutrients.items():
            field = recorded[metric]
            if not (
                field.provider_field_path == f"nutrients.{metric}"
                and field.canonical_value == value and field.provider_raw_value_decimal == value
                and field.provider_raw_value_text is None
                and field.provider_raw_unit == canonical_unit(metric)
                and field.canonical_unit == canonical_unit(metric)
                and field.observation_role == ObservationRole.CANONICAL.value
                and field.derived_from_field_observation_id is None
                and field.presence_state == (PresenceState.EXPLICIT_ZERO if value == 0 else PresenceState.SUPPLIED).value
                and field.coverage_state == CoverageState.COMPLETE.value
                and field.resolution_state == ResolutionState.RESOLVED.value
                and field.lineage_state == LineageState.CONFIRMED.value
                and field.provider_metadata == {}
            ):
                raise AnalysisDemoOwnershipError("Demo-Nährwertbeleg wurde verändert.")
    if (
        matched_snapshots != set(snapshot_by_id) or matched_profiles != set(profile_by_id)
        or matched_links != {item.id for item in links}
        or matched_identities != set(identity_by_id)
        or set(field_by_source) != {by_key[_NAMESPACE, key].id for key in expected}
    ):
        raise AnalysisDemoOwnershipError("Demo-Ernährungsbelege sind nicht exklusiv.")
    projections, heads = rows(NutritionDailyProjection), rows(NutritionProjectionHead)
    facts, lineage = rows(NutritionDailyProjectionFact), rows(NutritionDailyProjectionLineage)
    expected_values: dict[tuple[date, str], Decimal] = {}
    for day, _position, _daytime, _name, nutrients, _expected_run_id in expected.values():
        for metric in DAILY_PROJECTION_METRICS:
            metric_day = (day, metric)
            expected_values[metric_day] = expected_values.get(metric_day, Decimal("0")) + nutrients[metric]
    base_days = base_seed_days
    expected_days = {item[0] for item in expected.values()}
    projections_by_day: dict[date, list[NutritionDailyProjection]] = {}
    for projection in projections:
        projections_by_day.setdefault(projection.local_date, []).append(projection)
    for history in projections_by_day.values():
        history.sort(key=lambda projection: projection.projection_version)
    head_by_day = {item.local_date: item for item in heads}
    present_days = set(projections_by_day)
    if (
        len(head_by_day) != len(heads)
        or not present_days.issubset(expected_days)
        or set(head_by_day) != present_days
    ):
        raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
    for day, history in projections_by_day.items():
        versions = [projection.projection_version for projection in history]
        allowed: tuple[list[int], ...]
        if follow_up_run is not None and day == follow_up_day:
            allowed = ([1], [1, 2])
            if follow_up_run.status == "completed" and versions != [1, 2]:
                raise AnalysisDemoOwnershipError("Abgeschlossene Demo-Folgeprojektion ist unvollständig.")
        else:
            allowed = ([1],)
        if versions not in allowed or any(
            projection.priority_policy_id != policies[0].id
            or projection.projection_status != "ready"
            for projection in history
        ):
            raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
        if head_by_day[day].current_projection_id != history[-1].id:
            raise AnalysisDemoOwnershipError("Demo-Projektionskopf ist nicht aktuell.")
    if follow_up_run is not None:
        if follow_up_day is None or follow_up_tomorrow is None or predecessor_projection_id is None:
            raise AnalysisDemoOwnershipError("Demo-Folgelauf hat ungültige Projektionsmetadaten.")
        predecessor = next(
            (projection for projection in projections if projection.id == predecessor_projection_id),
            None,
        )
        if (
            predecessor is None
            or predecessor.local_date != follow_up_day
            or predecessor.projection_version != 1
            or _projection_snapshot_digest(db, predecessor) != predecessor_digest
        ):
            raise AnalysisDemoOwnershipError("Vorherige Demo-Projektion wurde verändert.")
        tomorrow_history = projections_by_day.get(follow_up_tomorrow, [])
        if (
            (follow_up_run.status == "completed" and [item.projection_version for item in tomorrow_history] != [1])
            or (tomorrow_history and [item.projection_version for item in tomorrow_history] != [1])
        ):
            raise AnalysisDemoOwnershipError("Demo-Folgeprojektion für morgen stimmt nicht.")
    projection_by_day = {
        day: history[-1] for day, history in projections_by_day.items()
    }
    missing_projection_days: set[date] = set()
    for day in base_days:
        if day not in projection_by_day:
            if base_run.status == "completed":
                raise AnalysisDemoOwnershipError("Abgeschlossene Demo-Grundprojektion fehlt.")
            missing_projection_days.add(day)
    if supplement_run is not None and supplement_day is not None and supplement_day not in projection_by_day:
        if supplement_run.status == "completed":
            raise AnalysisDemoOwnershipError("Abgeschlossene Demo-Tagesprojektion fehlt.")
        missing_projection_days.add(supplement_day)
    if follow_up_run is not None and follow_up_day is not None and follow_up_tomorrow is not None:
        current = projection_by_day.get(follow_up_day)
        if current is None or current.projection_version != 2:
            if follow_up_run.status == "completed":
                raise AnalysisDemoOwnershipError("Abgeschlossene Demo-Tagesprojektion fehlt.")
            missing_projection_days.add(follow_up_day)
        if follow_up_tomorrow not in projection_by_day:
            if follow_up_run.status == "completed":
                raise AnalysisDemoOwnershipError("Abgeschlossene Demo-Morgenprojektion fehlt.")
            missing_projection_days.add(follow_up_tomorrow)
    facts_by_projection: dict[UUID, list[NutritionDailyProjectionFact]] = {}
    for fact in facts:
        facts_by_projection.setdefault(fact.projection_id, []).append(fact)
    lineage_by_fact: dict[UUID, list[NutritionDailyProjectionLineage]] = {}
    for item in lineage:
        lineage_by_fact.setdefault(item.projection_fact_id, []).append(item)
    if (
        set(facts_by_projection) - {projection.id for projection in projections}
        or set(lineage_by_fact) - {fact.id for fact in facts}
    ):
        raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")

    bindings = ProviderSourceBindings((ProviderSourceInstance(_PROVIDER, source_id),))
    from app.services.analysis_demo_nutrition_resolution import AnalysisDemoMetricResolver

    registry = {_PROVIDER: AnalysisDemoMetricResolver()}
    policy_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    policy = get_effective_policy_snapshot(db, user_id, policy_at)
    if policy is None or policy.policy_id != policies[0].id:
        raise AnalysisDemoOwnershipError("Demo-Quellenpriorität wurde verändert.")
    for day, projection in projection_by_day.items():
        if (
            follow_up_run is not None and day == follow_up_day
            and projection.projection_version == 1
        ):
            continue
        selections = _collect_selections(
            db, user_id=user_id, provider_sources=bindings, local_date=day,
            policy=policy, resolver_registry=registry,
        )
        manifest = _build_manifest(
            db, user_id=user_id, local_date=day, provider_sources=bindings,
            policy=policy, selections=selections,
        )
        if (
            projection.projection_algorithm_version != manifest.projection_algorithm_version
            or projection.input_watermark != compute_input_watermark(manifest)
        ):
            raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
        day_facts = facts_by_projection.get(projection.id, [])
        by_metric = {fact.metric_key: fact for fact in day_facts}
        if len(by_metric) != len(day_facts) or set(by_metric) != set(DAILY_PROJECTION_METRICS):
            raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
        for selection in selections:
            fact_payload = map_selection_to_fact(selection)
            fact = by_metric[fact_payload.metric_key]
            if not (
                fact.value == expected_values[day, fact_payload.metric_key] == fact_payload.value
                and fact.unit == canonical_unit(fact_payload.metric_key) == fact_payload.unit
                and fact.selected_provider_key == _PROVIDER == fact_payload.selected_provider_key
                and fact.selected_granularity == fact_payload.selected_granularity
                and fact.presence_state == fact_payload.presence_state
                and fact.coverage_state == fact_payload.coverage_state
                and fact.resolution_state == fact_payload.resolution_state
                and fact.lineage_state == fact_payload.lineage_state
                and fact.diagnostic_metadata == fact_payload.diagnostic_metadata
            ):
                raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
            actual_lineage = lineage_by_fact.get(fact.id, [])
            by_source = {item.source_observation_id: item for item in actual_lineage}
            expected_lineage = {item.source_observation_id: item for item in fact_payload.lineage}
            if (
                len(by_source) != len(actual_lineage)
                or len(expected_lineage) != len(fact_payload.lineage)
                or set(by_source) != set(expected_lineage)
            ):
                raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
            for source_id, item in by_source.items():
                wanted = expected_lineage[source_id]
                if not (
                    source_id in field_by_source
                    and item.provider_key == _PROVIDER == wanted.provider_key
                    and item.role == wanted.role.value
                    and item.granularity == wanted.granularity
                    and item.contribution_value == wanted.contribution_value
                    and item.presence_state == wanted.presence_state
                    and item.coverage_state == wanted.coverage_state
                    and item.reason_code == wanted.reason_code
                    and item.lineage_metadata == wanted.metadata
                ):
                    raise AnalysisDemoOwnershipError("Demo-Projektionen wurden verändert oder sind nicht exklusiv.")
    return missing_projection_days
