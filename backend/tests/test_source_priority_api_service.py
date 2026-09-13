from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models import GoogleHealthConnection, User, YazioConnection
from app.nutrition.models import NutritionDailyProjection, NutritionProjectionHead
from app.source_priority.application import create_policy_with_rules
from app.source_priority.bootstrap import bootstrap_nutrition_priority
from app.source_priority.public import get_nutrition_priority_state
from app.source_priority.contracts import PriorityRuleSpec
from app.schemas_source_priority import (
    NutritionPrioritySource,
    NutritionPriorityState,
    NutritionPriorityUpdateRequest,
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
