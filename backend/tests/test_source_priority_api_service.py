from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import GoogleHealthConnection, User, YazioConnection
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
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.nutrition.projection.lifecycle import rebuild_affected_nutrition_days
from app.schemas_source_priority import (
    NutritionPrioritySource,
    NutritionPriorityState,
    NutritionPriorityUpdateRequest,
)
from app.source_priority.application import create_policy_with_rules
from app.source_priority.bootstrap import bootstrap_nutrition_priority
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.public import (
    AdvancedConfigurationConflict,
    InvalidSourceOrderConflict,
    ProviderSetChangedConflict,
    StalePolicyConflict,
    get_nutrition_priority_state,
    update_nutrition_priority,
)

AT = datetime(2026, 9, 13, 12, tzinfo=UTC)
GOOGLE_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"


def _add_yazio(db, user: User) -> YazioConnection:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="yazio-account",
    )
    db.add(connection)
    db.commit()
    return connection


def _add_google(db, user: User) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        encrypted_refresh_token=b"encrypted-refresh-token",
        granted_scopes=[GOOGLE_SCOPE],
        state="active",
    )
    db.add(connection)
    db.commit()
    return connection


def _policy(db, user: User, *rules: PriorityRuleSpec, effective_from: datetime = AT, version: int = 1):
    snapshot = create_policy_with_rules(db, user.id, version, effective_from, rules)
    db.commit()
    return snapshot


def _public_dump(state: NutritionPriorityState) -> dict[str, object]:
    return state.model_dump(mode="json")


def test_public_models_are_strict_and_update_request_has_only_public_fields() -> None:
    source = NutritionPrioritySource(id="yazio", label="YAZIO", available=True, rank=1)
    state = NutritionPriorityState(
        status="configured",
        version=1,
        sources=[source],
        configuration_mode="global",
        projection_refresh_required=False,
    )
    assert _public_dump(state) == {
        "status": "configured",
        "version": 1,
        "sources": [{"id": "yazio", "label": "YAZIO", "available": True, "rank": 1}],
        "configuration_mode": "global",
        "projection_refresh_required": False,
    }
    assert NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio"])
    with pytest.raises(ValidationError):
        NutritionPrioritySource(id="yazio", label="YAZIO", available=True, rank=1, policy_id="secret")
    with pytest.raises(ValidationError):
        NutritionPriorityState(
            status="configured",
            version=1,
            sources=[],
            configuration_mode="global",
            projection_refresh_required=False,
            policy_id="secret",
        )
    with pytest.raises(ValidationError):
        NutritionPriorityUpdateRequest(expected_version=None, source_order=[], policy_id="secret")


def test_get_state_reports_no_providers_without_policy(db, user) -> None:
    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert _public_dump(state) == {
        "status": "no_providers",
        "version": None,
        "sources": [],
        "configuration_mode": "none",
        "projection_refresh_required": False,
    }


def test_get_state_reports_d2a_single_provider_as_configured_global(db, user) -> None:
    _add_yazio(db, user)
    bootstrap_nutrition_priority(session_factory=lambda: db, user_id=user.id, effective_from=AT)

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert _public_dump(state) == {
        "status": "configured",
        "version": 1,
        "sources": [{"id": "yazio", "label": "YAZIO", "available": True, "rank": 1}],
        "configuration_mode": "global",
        "projection_refresh_required": False,
    }


def test_get_state_requires_selection_for_multiple_providers_without_policy(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.status == "selection_required"
    assert state.version is None
    assert state.configuration_mode == "none"
    assert [(item.id, item.label, item.available, item.rank) for item in state.sources] == [
        ("google_health", "Google Health", True, None),
        ("yazio", "YAZIO", True, None),
    ]


def test_get_state_reports_two_provider_global_policy_in_rank_order(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    _policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "google_health", 1),
        PriorityRuleSpec("nutrition", None, "yazio", 2),
    )

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.status == "configured"
    assert state.version == 1
    assert state.configuration_mode == "global"
    assert [(item.id, item.label, item.available, item.rank) for item in state.sources] == [
        ("google_health", "Google Health", True, 1),
        ("yazio", "YAZIO", True, 2),
    ]


def test_get_state_keeps_unavailable_configured_public_source_visible(db, user) -> None:
    _add_yazio(db, user)
    _policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "yazio", 1),
        PriorityRuleSpec("nutrition", None, "google_health", 2),
    )

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.status == "configured"
    assert [(item.id, item.available, item.rank) for item in state.sources] == [
        ("yazio", True, 1),
        ("google_health", False, 2),
    ]


def test_get_state_classifies_metric_specific_nutrition_policy_as_advanced(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    _policy(db, user, PriorityRuleSpec("nutrition", "protein_g", "yazio", 1))

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.status == "advanced_configuration"
    assert state.version == 1
    assert state.configuration_mode == "advanced"
    assert [(item.id, item.available, item.rank) for item in state.sources] == [
        ("google_health", True, None),
        ("yazio", True, None),
    ]


@pytest.mark.parametrize(
    "effective_from,data_area",
    [
        (AT + timedelta(days=1), "nutrition"),
        (AT - timedelta(days=1), "activity"),
    ],
)
def test_get_state_classifies_future_or_other_area_policy_as_configuration_required(
    db, user, effective_from: datetime, data_area: str
) -> None:
    _add_yazio(db, user)
    _policy(db, user, PriorityRuleSpec(data_area, None, "yazio", 1), effective_from=effective_from)

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.status == "configuration_required"
    assert state.version == 1
    assert state.configuration_mode == "none"
    assert [(item.id, item.available, item.rank) for item in state.sources] == [
        ("yazio", True, None),
    ]


def test_get_state_policy_and_sources_are_user_scoped(db, user) -> None:
    other = User(username="source-priority-other", password_hash=user.password_hash, timezone="UTC")
    db.add(other)
    db.flush()
    _add_yazio(db, other)
    _policy(db, other, PriorityRuleSpec("nutrition", None, "yazio", 1))

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.status == "no_providers"
    assert state.version is None
    assert state.sources == []
    assert "source_priority" not in str(_public_dump(state)).lower()


def test_get_state_refresh_flag_only_when_current_projection_uses_other_policy(db, user) -> None:
    _add_yazio(db, user)
    old_policy = _policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "yazio", 1),
        effective_from=AT - timedelta(days=2),
    )
    _policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "yazio", 1),
        effective_from=AT - timedelta(days=1),
        version=2,
    )
    projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=AT.date(),
        projection_version=1,
        projection_algorithm_version="test",
        priority_policy_id=old_policy.policy_id,
        input_watermark="test-watermark",
        projection_status="ready",
    )
    db.add(projection)
    db.flush()
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=AT.date(),
            current_projection_id=projection.id,
        )
    )
    db.commit()

    state = get_nutrition_priority_state(db, user.id, at=AT)

    assert state.projection_refresh_required is True
    assert "policy_id" not in str(_public_dump(state))
    assert "projection_id" not in str(_public_dump(state))


def _policy_rows(db, user: User) -> list[SourcePriorityPolicy]:
    return list(
        db.scalars(
            select(SourcePriorityPolicy)
            .where(SourcePriorityPolicy.user_id == user.id)
            .order_by(SourcePriorityPolicy.version)
        )
    )


def _rule_rows(db, user: User, policy_id: UUID) -> list[SourcePriorityRule]:
    return list(
        db.scalars(
            select(SourcePriorityRule)
            .where(
                SourcePriorityRule.user_id == user.id,
                SourcePriorityRule.policy_id == policy_id,
            )
            .order_by(SourcePriorityRule.priority_rank)
        )
    )


def test_put_without_policy_creates_version_one_for_exact_provider_order(db, user) -> None:
    _add_yazio(db, user)

    state, changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio"]),
    )

    assert changed is True
    assert state.status == "configured"
    assert state.version == 1
    assert state.projection_refresh_required is True
    policies = _policy_rows(db, user)
    assert len(policies) == 1
    assert [(rule.provider_key, rule.priority_rank) for rule in _rule_rows(db, user, policies[0].id)] == [
        ("yazio", 1)
    ]


def test_put_reorder_creates_immutable_next_version_and_requires_refresh(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    first_state, first_changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio", "google_health"]),
    )
    first_policy = _policy_rows(db, user)[0]
    first_rules = [(rule.provider_key, rule.priority_rank) for rule in _rule_rows(db, user, first_policy.id)]

    second_state, changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=first_state.version, source_order=["google_health", "yazio"]),
    )

    assert first_changed is True
    assert changed is True
    assert second_state.version == 2
    assert second_state.projection_refresh_required is True
    policies = _policy_rows(db, user)
    assert [policy.version for policy in policies] == [1, 2]
    assert [(rule.provider_key, rule.priority_rank) for rule in _rule_rows(db, user, first_policy.id)] == first_rules
    assert [(rule.provider_key, rule.priority_rank) for rule in _rule_rows(db, user, policies[1].id)] == [
        ("google_health", 1),
        ("yazio", 2),
    ]


def test_put_identical_current_order_is_noop_and_does_not_commit(db, user, monkeypatch) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    state, _ = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=None, source_order=["google_health", "yazio"]),
    )
    commit_calls = 0
    original_commit = db.commit

    def tracked_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(db, "commit", tracked_commit)
    result, changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=state.version, source_order=["google_health", "yazio"]),
    )

    assert changed is False
    assert result.version == state.version
    assert result.projection_refresh_required is False
    assert commit_calls == 0
    assert [policy.version for policy in _policy_rows(db, user)] == [1]


@pytest.mark.parametrize("source_order", [[], ["yazio", "yazio"], ["not-public"]])
def test_put_rejects_invalid_source_order(db, user, source_order: list[str]) -> None:
    _add_yazio(db, user)

    with pytest.raises(InvalidSourceOrderConflict) as raised:
        update_nutrition_priority(
            db,
            user.id,
            NutritionPriorityUpdateRequest(expected_version=None, source_order=source_order),
        )
    assert raised.value.code == "invalid_source_order"


@pytest.mark.parametrize("source_order", [["yazio"], ["google_health"]])
def test_put_rejects_provider_set_changes(db, user, source_order: list[str]) -> None:
    _add_yazio(db, user)
    _add_google(db, user)

    with pytest.raises(ProviderSetChangedConflict) as raised:
        update_nutrition_priority(
            db,
            user.id,
            NutritionPriorityUpdateRequest(expected_version=None, source_order=source_order),
        )
    assert raised.value.code == "provider_set_changed"



def test_put_rejects_added_provider_as_provider_set_change(db, user) -> None:
    _add_yazio(db, user)

    with pytest.raises(ProviderSetChangedConflict) as raised:
        update_nutrition_priority(
            db,
            user.id,
            NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio", "google_health"]),
        )
    assert raised.value.code == "provider_set_changed"

def test_put_rejects_stale_expected_version(db, user) -> None:
    _add_yazio(db, user)
    state, _ = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio"]),
    )

    with pytest.raises(StalePolicyConflict) as raised:
        update_nutrition_priority(
            db,
            user.id,
            NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio"]),
        )
    assert raised.value.code == "stale_policy"
    assert raised.value.current_version == state.version


def test_put_protects_advanced_active_policy(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    _policy(db, user, PriorityRuleSpec("nutrition", "protein_g", "yazio", 1))

    with pytest.raises(AdvancedConfigurationConflict) as raised:
        update_nutrition_priority(
            db,
            user.id,
            NutritionPriorityUpdateRequest(expected_version=1, source_order=["google_health", "yazio"]),
        )
    assert raised.value.code == "advanced_configuration"
    assert [policy.version for policy in _policy_rows(db, user)] == [1]


def test_put_repairs_incomplete_existing_policy(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    _policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "yazio", 2),
        PriorityRuleSpec("nutrition", None, "google_health", 3),
    )

    state, changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=1, source_order=["google_health", "yazio"]),
    )

    assert changed is True
    assert state.status == "configured"
    assert state.version == 2
    assert [policy.version for policy in _policy_rows(db, user)] == [1, 2]


def test_put_uses_latest_future_version_for_monotonic_next_version(db, user) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    _policy(db, user, PriorityRuleSpec("nutrition", None, "yazio", 1), version=1)
    _policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "google_health", 1),
        effective_from=datetime.now(UTC) + timedelta(days=1),
        version=7,
    )

    state, changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=7, source_order=["google_health", "yazio"]),
    )

    assert changed is True
    assert state.version == 8
    assert [policy.version for policy in _policy_rows(db, user)] == [1, 7, 8]


def test_put_propagates_unrelated_integrity_error(db, user, monkeypatch) -> None:
    _add_yazio(db, user)
    error = IntegrityError(
        "INSERT",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="some_other_constraint")),
    )

    def fail_create(*args, **kwargs):
        raise error

    monkeypatch.setattr("app.source_priority.public.create_policy_with_rules", fail_create)
    with pytest.raises(IntegrityError) as raised:
        update_nutrition_priority(
            db,
            user.id,
            NutritionPriorityUpdateRequest(expected_version=None, source_order=["yazio"]),
        )
    assert raised.value is error


def test_put_v2_is_used_by_d1b_lifecycle_for_new_ingestion_date(db, user) -> None:
    yazio = _add_yazio(db, user)
    bootstrap_nutrition_priority(
        session_factory=lambda: db,
        user_id=user.id,
        effective_from=datetime.now(UTC),
    )
    google = _add_google(db, user)
    state, changed = update_nutrition_priority(
        db,
        user.id,
        NutritionPriorityUpdateRequest(expected_version=1, source_order=["google_health", "yazio"]),
    )
    assert changed is True
    assert state.version == 2
    policy_v2 = _policy_rows(db, user)[-1]

    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="google_health",
        source_instance_id=google.id,
        connector_variant="google-health-api-v4",
        requested_start_date=AT.date(),
        requested_end_date=AT.date(),
        covered_start_date=AT.date(),
        covered_end_date=AT.date(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()
    source = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="google_health",
        source_instance_id=google.id,
        connector_variant="google-health-api-v4",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="google_health.nutrition_log",
        source_record_id="d1b-transition",
        source_revision=1,
        observation_fingerprint=sha256(b"d1b-transition").hexdigest(),
        local_date=AT.date(),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(source)
    db.flush()
    db.add(
        NutritionConsumptionEvent(
            user_id=user.id,
            source_observation_id=source.id,
            provider_key="google_health",
            source_instance_id=google.id,
            event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value,
            logical_event_key="d1b-transition",
            revision=1,
            local_date=AT.date(),
            amount=1,
            amount_unit="serving",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.add(
        NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=source.id,
            provider_field_path="nutritionLog.protein",
            provider_raw_value_decimal=30,
            provider_raw_unit="g",
            metric_key="protein_g",
            canonical_value=30,
            canonical_unit="g",
            observation_role=ObservationRole.CANONICAL.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    yazio_run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=yazio.id,
        connector_variant="yazio-sdk-v22",
        requested_start_date=AT.date(),
        requested_end_date=AT.date(),
        covered_start_date=AT.date(),
        covered_end_date=AT.date(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(yazio_run)
    db.flush()
    yazio_source = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=yazio_run.id,
        provider_key="yazio",
        source_instance_id=yazio.id,
        connector_variant="yazio-sdk-v22",
        observation_kind=ObservationKind.CONSUMPTION_EVENT.value,
        source_namespace="yazio.daily_summary",
        source_record_id="yazio-transition",
        source_revision=1,
        observation_fingerprint=sha256(b"yazio-transition").hexdigest(),
        local_date=AT.date(),
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(yazio_source)
    db.flush()
    db.add(
        NutritionConsumptionEvent(
            user_id=user.id,
            source_observation_id=yazio_source.id,
            provider_key="yazio",
            source_instance_id=yazio.id,
            event_kind=ConsumptionEventKind.SIMPLE_PRODUCT.value,
            logical_event_key="yazio-transition",
            revision=1,
            local_date=AT.date(),
            amount=1,
            amount_unit="serving",
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )
    db.add(
        NutritionFieldObservation(
            user_id=user.id,
            source_observation_id=yazio_source.id,
            provider_field_path="dailySummary.nutrient.protein",
            provider_raw_value_decimal=20,
            provider_raw_unit="g",
            metric_key="protein_g",
            canonical_value=20,
            canonical_unit="g",
            observation_role=ObservationRole.CANONICAL.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
    )

    db.commit()

    lifecycle = rebuild_affected_nutrition_days(
        session_factory=SessionLocal,
        user_id=user.id,
        ingestion_run_id=run.id,
        policy_at=policy_v2.effective_from.replace(tzinfo=UTC)
        if policy_v2.effective_from.tzinfo is None
        else policy_v2.effective_from,
    )

    assert lifecycle.affected_dates == (AT.date(),)
    assert lifecycle.created_dates == (AT.date(),)
    db.expire_all()
    projection = db.scalar(
        select(NutritionDailyProjection).where(
            NutritionDailyProjection.user_id == user.id,
            NutritionDailyProjection.local_date == AT.date(),
        )
    )
    assert projection is not None
    assert projection.priority_policy_id == policy_v2.id
    fact = db.scalar(
        select(NutritionDailyProjectionFact).where(
            NutritionDailyProjectionFact.projection_id == projection.id,
            NutritionDailyProjectionFact.metric_key == "protein_g",
        )
    )
    assert fact is not None
    assert fact.selected_provider_key == "google_health"
    assert fact.value == 30
