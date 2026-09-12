from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import GoogleHealthConnection, User, YazioConnection
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjectionFact,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.projection import ProjectionPersistenceStatus
from app.nutrition.projection.orchestration import rebuild_nutrition_day
from app.nutrition.resolution import PROVIDER_RESOLVERS, resolve_provider_metric
from app.nutrition.resolution.sources import (
    DEFAULT_SOURCE_RESOLVERS,
    resolve_default_provider_sources,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

GOOGLE = "google_health"
SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"
DAY = date(2026, 9, 1)
OTHER_DAY = date(2026, 9, 2)


def _connection(db, user: User, *, state: str = "active", scopes: list[str] | None = None):
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=list(scopes if scopes is not None else [SCOPE]),
        state=state,
    )
    db.add(connection)
    db.flush()
    return connection


def _run(db, user: User, connection: GoogleHealthConnection, *, coverage: str = "complete"):
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key=GOOGLE,
        source_instance_id=connection.id,
        connector_variant="google-health-api-v4",
        requested_start_date=DAY,
        requested_end_date=DAY,
        covered_start_date=DAY,
        covered_end_date=DAY,
        status="partial" if coverage == "partial" else "completed",
        coverage_state=coverage,
    )
    db.add(run)
    db.flush()
    return run


def _event(
    db,
    user: User,
    connection: GoogleHealthConnection,
    run: NutritionIngestionRun,
    *,
    key: str,
    value: Decimal | None,
    metric_key: str = "protein_g",
    local_date: date | None = DAY,
    revision: int = 1,
    source_revision: int = 1,
    supersedes: NutritionConsumptionEvent | None = None,
    coverage: str = "complete",
    lineage: str = "confirmed",
    event_kind: str = "simple_product",
):
    source = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key=GOOGLE,
        source_instance_id=connection.id,
        connector_variant="google-health-api-v4",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="google_health.nutrition_log",
        source_record_id=key,
        source_revision=source_revision,
        observation_fingerprint=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        local_date=local_date,
        timezone_source="provider",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=coverage,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=lineage,
    )
    db.add(source)
    db.flush()
    event = NutritionConsumptionEvent(
        user_id=user.id,
        source_observation_id=source.id,
        provider_key=GOOGLE,
        source_instance_id=connection.id,
        event_kind=event_kind,
        logical_event_key=key,
        supersedes_event_id=supersedes.id if supersedes is not None else None,
        supersedes_revision=supersedes.revision if supersedes is not None else None,
        revision=revision,
        provider_civil_datetime=datetime(2026, 9, 1, 12, tzinfo=None),
        local_date=local_date,
        amount=Decimal("1"),
        amount_unit="serving",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=coverage,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=lineage,
    )
    db.add(event)
    db.flush()
    if value is not None:
        field = NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=source.id,
            provider_field_path=f"nutritionLog.{metric_key}",
            provider_raw_value_decimal=value,
            provider_raw_unit="kcal" if metric_key == "dietary_energy_kcal" else "g",
            metric_key=metric_key,
            canonical_value=value,
            canonical_unit="kcal" if metric_key == "dietary_energy_kcal" else "g",
            observation_role=ObservationRole.CANONICAL.value,
            presence_state=(
                PresenceState.EXPLICIT_ZERO.value if value == Decimal("0") else PresenceState.SUPPLIED.value
            ),
            coverage_state=coverage,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=lineage,
        )
        db.add(field)
        db.flush()
    return event, source


def test_google_health_is_registered_in_production_resolver_registry() -> None:
    assert set(PROVIDER_RESOLVERS) == {"google_health", "yazio"}
    assert PROVIDER_RESOLVERS[GOOGLE].provider_key == GOOGLE


def test_google_health_source_resolver_uses_connection_id_even_when_reauth_required(db, user) -> None:
    connection = _connection(db, user, state="reauth_required")

    bindings = resolve_default_provider_sources(
        db,
        user_id=user.id,
        provider_keys=(GOOGLE,),
    )

    assert bindings.for_provider(GOOGLE) is not None
    assert bindings.for_provider(GOOGLE).source_instance_id == connection.id
    assert DEFAULT_SOURCE_RESOLVERS[GOOGLE].owns_source_instance_id(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
    )


def test_default_source_bindings_keep_yazio_and_google_instances_distinct(db, user) -> None:
    yazio = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="yazio-source",
    )
    google = _connection(db, user)
    db.add(yazio)
    db.flush()

    bindings = resolve_default_provider_sources(
        db,
        user_id=user.id,
        provider_keys=("yazio", GOOGLE),
    )

    assert bindings.for_provider("yazio").source_instance_id == yazio.id
    assert bindings.for_provider(GOOGLE).source_instance_id == google.id
    assert yazio.id != google.id


def test_google_health_resolver_sums_current_canonical_events_with_decimal_exactness(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _first, first_source = _event(
        db,
        user,
        connection,
        run,
        key="event-1",
        value=Decimal("1.20"),
    )
    _second, second_source = _event(
        db,
        user,
        connection,
        run,
        key="event-2",
        value=Decimal("2.30"),
    )

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("3.50")
    assert candidate.unit == "g"
    assert candidate.selected_granularity is ProjectionGranularity.EVENT
    assert candidate.presence_state is PresenceState.SUPPLIED
    assert candidate.coverage_state is CoverageState.COMPLETE
    assert candidate.lineage_state is LineageState.CONFIRMED
    assert {item.source_observation_id for item in candidate.source_lineage} == {
        first_source.id,
        second_source.id,
    }
    field_ids = set(
        db.scalars(
            select(NutritionFieldObservation.id).where(
                NutritionFieldObservation.source_observation_id.in_(
                    {first_source.id, second_source.id}
                )
            )
        ).all()
    )
    assert {item.evidence_id for item in candidate.source_lineage} == field_ids


@pytest.mark.parametrize(
    ("metric_key", "expected_unit"),
    (
        ("dietary_energy_kcal", "kcal"),
        ("protein_g", "g"),
        ("carbohydrates_g", "g"),
        ("fat_g", "g"),
        ("fiber_g", "g"),
        ("sugar_g", "g"),
        ("saturated_fat_g", "g"),
    ),
)
def test_google_health_resolves_every_canonical_metric(
    db,
    user,
    metric_key: str,
    expected_unit: str,
) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key=metric_key, value=Decimal("2.5"), metric_key=metric_key)

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key=metric_key,
    )

    assert candidate.value == Decimal("2.5")
    assert candidate.unit == expected_unit


def test_google_health_explicit_zero_is_not_missing(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="zero", value=Decimal("0"))

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("0")
    assert candidate.presence_state is PresenceState.EXPLICIT_ZERO


def test_google_health_unknown_event_kind_is_missing(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(
        db,
        user,
        connection,
        run,
        key="unknown-kind",
        value=Decimal("4"),
        event_kind="unknown",
    )

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value is None
    assert candidate.presence_state is PresenceState.MISSING


def test_google_health_uncertain_lineage_is_value_bearing_but_not_confirmed(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="uncertain", value=Decimal("4"), lineage="uncertain")

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("4")
    assert candidate.lineage_state is LineageState.UNCERTAIN


def test_google_health_missing_metric_is_not_zero(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="missing", value=None)

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value is None
    assert candidate.presence_state is PresenceState.MISSING


def test_google_health_partial_metric_coverage_keeps_value_and_marks_partial(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection, coverage="partial")
    _event(db, user, connection, run, key="present", value=Decimal("5"), coverage="partial")
    _event(db, user, connection, run, key="missing", value=None, coverage="partial")

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("5")
    assert candidate.coverage_state is CoverageState.PARTIAL
    assert candidate.selected_granularity is ProjectionGranularity.PARTIAL_EVENT
    assert candidate.lineage_state is LineageState.CONFIRMED


def test_google_health_excludes_null_and_other_day_events(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="same-day", value=Decimal("4"), local_date=DAY)
    _event(db, user, connection, run, key="other-day", value=Decimal("100"), local_date=OTHER_DAY)
    _event(db, user, connection, run, key="null-day", value=Decimal("200"), local_date=None)

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("4")


def test_google_health_uses_only_current_event_revision(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    old, _ = _event(db, user, connection, run, key="revisioned", value=Decimal("3"))
    _event(
        db,
        user,
        connection,
        run,
        key="revisioned",
        value=Decimal("7"),
        revision=2,
        source_revision=2,
        supersedes=old,
    )

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("7")


def test_google_health_revision_moved_to_other_date_replaces_old_date(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    old, _ = _event(db, user, connection, run, key="moved", value=Decimal("3"), local_date=DAY)
    _event(
        db,
        user,
        connection,
        run,
        key="moved",
        value=Decimal("7"),
        local_date=OTHER_DAY,
        revision=2,
        source_revision=2,
        supersedes=old,
    )

    old_day = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )
    new_day = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=OTHER_DAY,
        metric_key="protein_g",
    )

    assert old_day.value is None
    assert new_day.value == Decimal("7")




def test_google_health_projection_handles_revision_moved_to_other_date(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    old, _ = _event(db, user, connection, run, key="projected-move", value=Decimal("3"), local_date=DAY)
    _event(
        db,
        user,
        connection,
        run,
        key="projected-move",
        value=Decimal("7"),
        local_date=OTHER_DAY,
        revision=2,
        source_revision=2,
        supersedes=old,
    )
    create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, GOOGLE, 1),),
    )
    db.commit()

    old_day = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    new_day = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=OTHER_DAY,
        policy_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )
    repeated_new_day = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=OTHER_DAY,
        policy_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert old_day.status is ProjectionPersistenceStatus.CREATED
    assert new_day.status is ProjectionPersistenceStatus.CREATED
    assert repeated_new_day.status is ProjectionPersistenceStatus.UNCHANGED
    assert repeated_new_day.input_watermark == new_day.input_watermark


def test_google_health_resolver_is_tenant_and_source_instance_scoped(db, user) -> None:
    other = User(username="google-resolution-other", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    connection = _connection(db, user)
    other_connection = _connection(db, other)
    run = _run(db, user, connection)
    other_run = _run(db, other, other_connection)
    _event(db, user, connection, run, key="owned", value=Decimal("2"))
    _event(db, other, other_connection, other_run, key="other", value=Decimal("100"))

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )

    assert candidate.value == Decimal("2")
    with pytest.raises(ValueError, match="source_instance_id"):
        resolve_provider_metric(
            db,
            provider_key=GOOGLE,
            user_id=user.id,
            source_instance_id=other_connection.id,
            local_date=DAY,
            metric_key="protein_g",
        )


def test_google_health_reauth_does_not_invalidate_historical_resolution(db, user) -> None:
    connection = _connection(db, user, state="reauth_required")
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="historical", value=Decimal("9"))

    candidate = resolve_provider_metric(
        db,
        provider_key=GOOGLE,
        user_id=user.id,
        source_instance_id=connection.id,
        local_date=DAY,
        metric_key="protein_g",
    )
    assert candidate.value == Decimal("9")
    assert db.scalar(select(GoogleHealthConnection.state).where(GoogleHealthConnection.id == connection.id)) == "reauth_required"


def test_google_health_default_binding_produces_google_only_projection(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="projection", value=Decimal("9"))
    create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, GOOGLE, 1),),
    )
    db.commit()

    result = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result.status is ProjectionPersistenceStatus.CREATED
    fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == result.projection_id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert fact is not None
    assert fact.value == Decimal("9")


def test_google_health_projection_watermark_is_stable_on_rebuild(db, user) -> None:
    connection = _connection(db, user)
    run = _run(db, user, connection)
    _event(db, user, connection, run, key="stable", value=Decimal("9"))
    create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, GOOGLE, 1),),
    )
    db.commit()

    first = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    second = rebuild_nutrition_day(
        db,
        user_id=user.id,
        local_date=DAY,
        policy_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert first.status is ProjectionPersistenceStatus.CREATED
    assert second.status is ProjectionPersistenceStatus.UNCHANGED
    assert second.input_watermark == first.input_watermark



def test_google_health_rebuild_without_any_source_fails_closed(db, user) -> None:
    create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, GOOGLE, 1),),
    )
    db.commit()

    with pytest.raises(ValueError, match="source instance is not configured"):
        rebuild_nutrition_day(
            db,
            user_id=user.id,
            local_date=DAY,
            policy_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        )
