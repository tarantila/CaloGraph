from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models import (
    GoogleHealthConnection,
    NutritionDailyProjection,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
    User,
    YazioConnection,
)
from app.nutrition.projection.refresh import (
    NutritionProjectionRefreshError,
    NutritionProjectionRefreshResult,
)
from app.services.credential_crypto import encrypt_credential
from app.source_priority.application import create_policy_with_rules
from app.source_priority.bootstrap import bootstrap_nutrition_priority
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

PASSWORD = "correct-horse-battery-staple"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"
PATH = "/api/v1/source-priority/nutrition"


def _login(client: TestClient, username: str = "admin") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _add_yazio(db, user: User) -> None:
    db.add(
        YazioConnection(
            user_id=user.id,
            encrypted_email=b"encrypted-email",
            encrypted_password=b"encrypted-password",
            source_identifier="yazio-account",
        )
    )
    db.commit()


def _add_google(db, user: User) -> None:
    db.add(
        GoogleHealthConnection(
            client_id="source-priority-api-client",
            encrypted_client_secret=encrypt_credential("source-priority-api-client-secret"),
            user_id=user.id,
            encrypted_refresh_token=b"encrypted-refresh-token",
            granted_scopes=[GOOGLE_SCOPE],
            state="active",
        )
    )
    db.commit()


def _assert_public_state(payload: dict[str, object]) -> None:
    assert set(payload) == {
        "status",
        "version",
        "sources",
        "configuration_mode",
        "projection_refresh_required",
    }
    assert "policy_id" not in str(payload).lower()
    assert "source_priority" not in str(payload).lower()
    assert "instance" not in str(payload).lower()
    assert "database" not in str(payload).lower()
    for source in payload["sources"]:
        assert set(source) == {"id", "label", "available", "rank"}


def test_source_priority_anonymous_get_and_put_are_rejected(client: TestClient) -> None:
    assert client.get(PATH).status_code == 401
    assert (
        client.put(PATH, json={"expected_version": None, "source_order": ["yazio"]}).status_code
        == 401
    )


def test_source_priority_get_returns_authenticated_authoritative_state(
    client: TestClient, user: User, db
) -> None:
    _add_yazio(db, user)
    _login(client)

    response = client.get(PATH)

    assert response.status_code == 200
    payload = response.json()
    _assert_public_state(payload)
    assert payload == {
        "status": "selection_required",
        "version": None,
        "sources": [{"id": "yazio", "label": "YAZIO", "available": True, "rank": None}],
        "configuration_mode": "none",
        "projection_refresh_required": False,
    }


def test_source_priority_get_marks_history_stale_without_policy(
    client: TestClient, user: User, db
) -> None:
    _add_yazio(db, user)
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        connector_variant="no-policy-test",
        status="completed",
        coverage_state="complete",
    )
    db.add(run)
    db.flush()
    db.add(
        NutritionSourceObservation(
            user_id=user.id,
            ingestion_run_id=run.id,
            provider_key="yazio",
            source_instance_id=run.source_instance_id,
            connector_variant="no-policy-test",
            observation_kind="consumption_event",
            source_namespace="no-policy-test",
            source_record_id="2026-09-13",
            source_revision=1,
            observation_fingerprint="0" * 64,
            local_date=date(2026, 9, 13),
            timezone_source="provider",
            time_confidence="exact",
            presence_state="supplied",
            coverage_state="complete",
            resolution_state="resolved",
            lineage_state="confirmed",
        )
    )
    db.commit()
    _login(client)

    response = client.get(PATH)

    assert response.status_code == 200
    assert response.json()["status"] == "selection_required"
    assert response.json()["projection_refresh_required"] is True


def test_source_priority_put_rejects_missing_and_invalid_csrf(
    client: TestClient, user: User, db
) -> None:
    _add_yazio(db, user)
    _login(client)

    missing = client.put(PATH, json={"expected_version": None, "source_order": ["yazio"]})
    invalid = client.put(
        PATH,
        headers={"X-CSRF-Token": "not-the-session-token"},
        json={"expected_version": None, "source_order": ["yazio"]},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert missing.json()["type"] == "urn:calograph:problem:csrf-validation-failed"


def test_source_priority_put_returns_complete_state_and_refresh_flag(
    client: TestClient, user: User, db
) -> None:
    _add_yazio(db, user)
    csrf = _login(client)

    changed = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )
    no_op = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1, "source_order": ["yazio"]},
    )

    assert changed.status_code == 200
    assert no_op.status_code == 200
    assert changed.json() == {
        "status": "configured",
        "version": 1,
        "sources": [{"id": "yazio", "label": "YAZIO", "available": True, "rank": 1}],
        "configuration_mode": "global",
        "projection_refresh_required": False,
    }
    assert no_op.json()["projection_refresh_required"] is False
    assert "changed" not in changed.json()
    _assert_public_state(changed.json())


@pytest.mark.parametrize(
    ("source_order", "expected_version", "expected_status", "expected_code"),
    [
        ([], None, 422, "invalid_source_order"),
        (["yazio"], None, 409, "provider_set_changed"),
    ],
)
def test_source_priority_put_maps_order_conflicts(
    client: TestClient,
    user: User,
    db,
    source_order: list[str],
    expected_version: int | None,
    expected_status: int,
    expected_code: str,
) -> None:
    _add_yazio(db, user)
    _add_google(db, user)
    csrf = _login(client)

    response = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": expected_version, "source_order": source_order},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_code
    assert response.json()["type"] == (
        f"urn:calograph:problem:source-priority-{expected_code.replace('_', '-')}"
    )


def test_source_priority_put_maps_stale_conflict(client: TestClient, user: User, db) -> None:
    _add_yazio(db, user)
    csrf = _login(client)
    first = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )
    assert first.status_code == 200

    stale = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )

    assert stale.status_code == 409
    assert stale.json()["detail"] == "stale_policy"
    assert stale.json()["type"] == "urn:calograph:problem:source-priority-stale-policy"


def test_source_priority_put_maps_advanced_conflict(client: TestClient, user: User, db) -> None:
    _add_yazio(db, user)
    create_policy_with_rules(
        db,
        user.id,
        1,
        datetime.now(UTC) - timedelta(minutes=1),
        (PriorityRuleSpec("nutrition", "protein_g", "yazio", 1),),
    )
    db.commit()
    csrf = _login(client)

    advanced = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1, "source_order": ["yazio"]},
    )

    assert advanced.status_code == 409
    assert advanced.json()["detail"] == "advanced_configuration"
    assert advanced.json()["type"] == "urn:calograph:problem:source-priority-advanced-configuration"


def test_source_priority_put_rejects_extra_fields(client: TestClient, user: User, db) -> None:
    _add_yazio(db, user)
    csrf = _login(client)

    response = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"], "policy_id": "secret"},
    )

    assert response.status_code == 422
    assert response.json()["type"] == "urn:calograph:problem:validation-error"
    assert "secret" not in response.text


def test_source_priority_isolates_authenticated_users(client: TestClient, user: User, db) -> None:
    _add_yazio(db, user)
    other = User(
        username="source-priority-other",
        password_hash=hash_password(PASSWORD),
        timezone="UTC",
    )
    db.add(other)
    db.flush()
    _add_yazio(db, other)
    csrf = _login(client)
    own = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )
    assert own.status_code == 200

    other_client = TestClient(app)
    _login(other_client, "source-priority-other")
    response = other_client.get(PATH)

    assert response.status_code == 200
    assert response.json()["status"] == "selection_required"
    assert response.json()["version"] is None
    assert response.json()["projection_refresh_required"] is False
    _assert_public_state(response.json())


def test_source_priority_d2a_to_d2b_transition_keeps_v1_and_does_not_backfill(
    client: TestClient, user: User, db, monkeypatch
) -> None:
    monkeypatch.setattr("app.config.settings.google_health_enabled", True)
    _add_yazio(db, user)
    bootstrap_nutrition_priority(
        session_factory=lambda: db,
        user_id=user.id,
        effective_from=datetime.now(UTC),
    )
    csrf = _login(client)

    initial = client.get(PATH)
    assert initial.status_code == 200
    assert initial.json() == {
        "status": "configured",
        "version": 1,
        "sources": [{"id": "yazio", "label": "YAZIO", "available": True, "rank": 1}],
        "configuration_mode": "global",
        "projection_refresh_required": False,
    }

    _add_google(db, user)
    second_provider = client.get(PATH)
    assert second_provider.status_code == 200
    assert second_provider.json() == {
        "status": "configuration_required",
        "version": 1,
        "sources": [
            {"id": "yazio", "label": "YAZIO", "available": True, "rank": 1},
            {"id": "google_health", "label": "Google Health", "available": False, "rank": None},
        ],
        "configuration_mode": "none",
        "projection_refresh_required": False,
    }

    policy_v1 = db.query(SourcePriorityPolicy).one()
    rules_v1 = [
        (rule.provider_key, rule.priority_rank)
        for rule in db.query(SourcePriorityRule)
        .filter_by(policy_id=policy_v1.id)
        .order_by(SourcePriorityRule.priority_rank)
        .all()
    ]
    historical_projection = NutritionDailyProjection(
        user_id=user.id,
        local_date=datetime(2026, 9, 12, tzinfo=UTC).date(),
        projection_version=1,
        projection_algorithm_version="test",
        priority_policy_id=policy_v1.id,
        input_watermark="historical-watermark",
        projection_status="ready",
    )
    db.add(historical_projection)
    db.flush()
    db.add(
        NutritionProjectionHead(
            user_id=user.id,
            local_date=historical_projection.local_date,
            current_projection_id=historical_projection.id,
        )
    )
    db.commit()

    updated = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1, "source_order": ["google_health", "yazio"]},
    )

    assert updated.status_code == 200
    assert updated.json() == {
        "status": "configured",
        "version": 2,
        "sources": [
            {"id": "google_health", "label": "Google Health", "available": False, "rank": 1},
            {"id": "yazio", "label": "YAZIO", "available": True, "rank": 2},
        ],
        "configuration_mode": "global",
        "projection_refresh_required": True,
    }
    policies = db.query(SourcePriorityPolicy).order_by(SourcePriorityPolicy.version).all()
    assert [policy.version for policy in policies] == [1, 2]
    assert [
        (rule.provider_key, rule.priority_rank)
        for rule in db.query(SourcePriorityRule)
        .filter_by(policy_id=policies[0].id)
        .order_by(SourcePriorityRule.priority_rank)
        .all()
    ] == rules_v1
    assert db.query(NutritionDailyProjection).count() == 1
    db.refresh(historical_projection)
    assert historical_projection.projection_version == 1
    assert historical_projection.priority_policy_id == policy_v1.id


REFRESH_PATH = f"{PATH}/refresh"


def _refresh_result() -> NutritionProjectionRefreshResult:
    return NutritionProjectionRefreshResult(
        policy_version=1,
        processed_count=2,
        created_count=1,
        unchanged_count=1,
        has_more=False,
        projection_refresh_required=False,
    )


def test_source_priority_anonymous_refresh_is_rejected(client: TestClient) -> None:
    response = client.post(REFRESH_PATH, json={"expected_version": 1})

    assert response.status_code == 401


def test_source_priority_refresh_rejects_missing_and_invalid_csrf(
    client: TestClient, user: User, db
) -> None:
    _add_yazio(db, user)
    _login(client)
    missing = client.post(REFRESH_PATH, json={"expected_version": 1})
    invalid = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": "not-the-session-token"},
        json={"expected_version": 1},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert missing.json()["type"] == "urn:calograph:problem:csrf-validation-failed"


def test_source_priority_refresh_returns_strict_public_result(
    client: TestClient, user: User, db, monkeypatch
) -> None:
    _add_yazio(db, user)
    csrf = _login(client)
    configured = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )
    assert configured.status_code == 200

    captured: dict[str, object] = {}

    def fake_refresh(*, session_factory, user_id, expected_version):
        captured.update(
            session_factory=session_factory,
            user_id=user_id,
            expected_version=expected_version,
        )
        return _refresh_result()

    from app.api import source_priority as source_priority_api

    monkeypatch.setattr(source_priority_api, "refresh_stale_nutrition_projections", fake_refresh)
    response = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1},
    )

    assert response.status_code == 200
    assert response.json() == {
        "policy_version": 1,
        "processed_count": 2,
        "created_count": 1,
        "unchanged_count": 1,
        "has_more": False,
        "projection_refresh_required": False,
    }
    assert captured["session_factory"] is SessionLocal
    assert captured["user_id"] == user.id
    assert captured["expected_version"] == 1
    assert "source_id" not in response.text
    assert "policy_id" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_version": 1, "batch_size": 1},
        {"expected_version": 1, "offset": 0},
        {"expected_version": 1, "cursor": "secret"},
        {"expected_version": 1, "provider_id": "secret"},
        {},
    ],
)
def test_source_priority_refresh_request_is_strict(
    client: TestClient, user: User, payload: dict[str, object]
) -> None:
    csrf = _login(client)

    response = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["type"] == "urn:calograph:problem:validation-error"
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("setup", "message", "expected_detail"),
    [
        ("configured", "expected policy version 1, current version is 2", "stale_policy"),
        ("selection_required", "", "selection_required"),
        ("configuration_required", "", "configuration_required"),
        (
            "configured",
            "effective nutrition policy disappeared while rebuilding 2026-09-14",
            "no_policy",
        ),
    ],
)
def test_source_priority_refresh_maps_safe_conflicts(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
    setup: str,
    message: str,
    expected_detail: str,
) -> None:
    if setup == "configured":
        _add_yazio(db, user)
        csrf = _login(client)
        configured = client.put(
            PATH,
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": None, "source_order": ["yazio"]},
        )
        assert configured.status_code == 200
    elif setup == "selection_required":
        _add_yazio(db, user)
        csrf = _login(client)
    elif setup == "configuration_required":
        _add_yazio(db, user)
        create_policy_with_rules(db, user.id, 1, datetime.now(UTC), ())
        db.commit()
        csrf = _login(client)
    else:
        csrf = _login(client)

    from app.api import source_priority as source_priority_api

    def fake_refresh(**_kwargs):
        raise NutritionProjectionRefreshError(message)

    monkeypatch.setattr(source_priority_api, "refresh_stale_nutrition_projections", fake_refresh)
    response = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == expected_detail
    assert (
        response.json()["type"]
        == f"urn:calograph:problem:source-priority-{expected_detail.replace('_', '-')}"
    )
    assert "policy_id" not in response.text
    assert "constraint" not in response.text.lower()


def test_source_priority_refresh_maps_unexpected_failure_safely(
    client: TestClient, user: User, db, monkeypatch
) -> None:
    _add_yazio(db, user)
    csrf = _login(client)
    configured = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )
    assert configured.status_code == 200

    from app.api import source_priority as source_priority_api

    def fake_refresh(**_kwargs):
        raise RuntimeError("SQL constraint secret payload")

    monkeypatch.setattr(source_priority_api, "refresh_stale_nutrition_projections", fake_refresh)
    response = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "projection_refresh_failed"
    assert (
        response.json()["type"] == "urn:calograph:problem:source-priority-projection-refresh-failed"
    )
    assert "SQL" not in response.text
    assert "secret" not in response.text


def test_source_priority_refresh_maps_preflight_failure_safely(
    client: TestClient, user: User, monkeypatch
) -> None:
    csrf = _login(client)

    from app.api import source_priority as source_priority_api

    def fail_preflight(*_args, **_kwargs):
        raise RuntimeError("SQL constraint secret provider payload")

    monkeypatch.setattr(source_priority_api, "get_nutrition_priority_state", fail_preflight)
    response = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "projection_refresh_failed"
    assert (
        response.json()["type"] == "urn:calograph:problem:source-priority-projection-refresh-failed"
    )
    assert "SQL" not in response.text
    assert "secret" not in response.text


def test_source_priority_refresh_isolates_authenticated_users(
    client: TestClient, user: User, db, monkeypatch
) -> None:
    _add_yazio(db, user)
    csrf = _login(client)
    configured = client.put(
        PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": None, "source_order": ["yazio"]},
    )
    assert configured.status_code == 200
    other = User(
        username="source-priority-refresh-other",
        password_hash=hash_password(PASSWORD),
        timezone="UTC",
    )
    db.add(other)
    db.commit()
    other_client = TestClient(app)
    other_csrf = _login(other_client, "source-priority-refresh-other")

    from app.api import source_priority as source_priority_api

    seen_users: list[object] = []

    def fake_refresh(*, user_id, **_kwargs):
        seen_users.append(user_id)
        return _refresh_result()

    monkeypatch.setattr(source_priority_api, "refresh_stale_nutrition_projections", fake_refresh)
    own = client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": 1},
    )
    other_response = other_client.post(
        REFRESH_PATH,
        headers={"X-CSRF-Token": other_csrf},
        json={"expected_version": 1},
    )

    assert own.status_code == 200
    assert other_response.status_code == 409
    assert other_response.json()["detail"] == "no_providers"
    assert seen_users == [user.id]
