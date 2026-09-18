from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    NutritionTargetActivitySource,
    User,
    UserProviderPreference,
    UserProviderPriority,
    YazioConnection,
)
from app.provider_preferences import (
    ACTIVITY_ENERGY_DATA_AREA,
    NUTRITION_DATA_AREA,
    SUPPORTED_PROVIDER_KEYS,
    WEIGHT_DATA_AREA,
    normalize_provider_key,
    validate_provider_preference,
)
from app.source_priority import compatibility as source_priority_compatibility
from app.source_priority.contracts import PriorityRuleSpec
from app.weight import WEIGHT_PROVIDER_SOURCE_TYPES

PATH = "/api/v1/settings/provider-preferences"
WEIGHT_PATH = "/api/v1/analytics/weight"
AVAILABILITY_PATH = "/api/v1/settings/provider-availability/nutrition"
PASSWORD = "correct-horse-battery-staple"


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _add_yazio(db, user) -> None:
    db.add(
        YazioConnection(
            user_id=user.id,
            encrypted_email=b"encrypted-email",
            encrypted_password=b"encrypted-password",
            source_identifier="yazio-account",
        )
    )
    db.commit()


def _add_priority_policy(db, user, *rules: PriorityRuleSpec):
    grouped = {}
    for rule in rules:
        grouped.setdefault(rule.data_area, []).append(rule)
    for data_area, area_rules in grouped.items():
        for rule in sorted(area_rules, key=lambda item: item.priority_rank):
            db.add(
                UserProviderPriority(
                    user_id=user.id,
                    data_area=data_area,
                    priority=rule.priority_rank,
                    provider_key=rule.provider_key,
                )
            )
    db.commit()
    return tuple(
        (rule.data_area, rule.provider_key, rule.priority_rank)
        for rule in rules
    )


def _add_sample(
    db,
    user,
    *,
    metric_type: str,
    source_type: str,
    value: Decimal,
    local_date: date,
    source_identifier: str = "test-source",
) -> None:
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    timestamp = datetime.combine(local_date, datetime.min.time(), tzinfo=UTC)
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=f"{source_type}-{metric_type}-{local_date}",
            fingerprint=f"{source_type}-{metric_type}-{local_date}-{user.id}".replace("-", "")[:64],
            source_type=source_type,
            source_identifier=source_identifier,
            metric_type=metric_type,
            value=value,
            unit="kg" if metric_type == "weight_kg" else "kcal",
            original_value=value,
            original_unit="kg" if metric_type == "weight_kg" else "kcal",
            start_at=timestamp,
            end_at=timestamp,
            local_date=local_date,
            timezone="UTC",
        )
    )


def _add_weight_sample(
    db,
    user,
    *,
    source_type: str,
    value: Decimal,
    local_date: date,
    start_at: datetime,
    source_identifier: str,
) -> None:
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    external_id = f"{source_type}-{start_at.isoformat()}"
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=external_id,
            fingerprint=f"{external_id}-{user.id}".replace("-", "")[:64],
            source_type=source_type,
            source_identifier=source_identifier,
            metric_type="weight_kg",
            value=value,
            unit="kg",
            original_value=value,
            original_unit="kg",
            start_at=start_at,
            end_at=start_at,
            local_date=local_date,
            timezone="UTC",
        )
    )


def test_provider_preference_domain_accepts_canonical_nutrition_provider() -> None:
    assert NUTRITION_DATA_AREA == "nutrition"
    assert "google_health" in SUPPORTED_PROVIDER_KEYS[NUTRITION_DATA_AREA]
    assert normalize_provider_key(" Google_Health ") == "google_health"
    validate_provider_preference(NUTRITION_DATA_AREA, "apple_health")


def test_provider_preference_domain_rejects_unknown_area_or_provider() -> None:
    import pytest

    with pytest.raises(ValueError, match="data area"):
        validate_provider_preference("body_weight", "yazio")
    with pytest.raises(ValueError, match="provider"):
        validate_provider_preference(NUTRITION_DATA_AREA, "withings")


def test_provider_preference_api_requires_authentication(client: TestClient) -> None:
    assert client.get(PATH).status_code == 401
    assert client.get(AVAILABILITY_PATH).status_code == 401


def test_provider_preference_api_reads_writes_and_deletes_user_scoped_value(
    client: TestClient,
    user,
    db,
) -> None:
    _add_yazio(db, user)
    csrf = _login(client)
    assert client.get(PATH).json() == {"preferences": []}

    other = User(username="other", password_hash="not-used", timezone="Europe/Berlin")
    db.add(other)
    db.flush()
    db.add(
        UserProviderPreference(user_id=other.id, data_area="nutrition", provider_key="apple_health")
    )
    db.commit()

    created = client.put(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
        json={"provider_key": "yazio"},
    )
    assert created.status_code == 200
    assert created.json() == {"data_area": "nutrition", "provider_key": "yazio"}

    stored = db.scalar(
        select(UserProviderPriority).where(
            UserProviderPriority.user_id == user.id,
            UserProviderPriority.data_area == "nutrition",
        )
    )
    assert stored is not None
    assert (stored.provider_key, stored.priority) == ("yazio", 1)

    listed = client.get(PATH)
    assert listed.status_code == 200
    assert listed.json() == {
        "preferences": [{"data_area": "nutrition", "provider_key": "yazio"}]
    }

    client.delete(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
    )
    db.expire_all()
    assert db.scalar(
        select(UserProviderPriority).where(
            UserProviderPriority.user_id == user.id,
            UserProviderPriority.data_area == "nutrition",
        )
    ) is None
    assert db.get(UserProviderPreference, (other.id, "nutrition")) is not None


def test_provider_preference_write_uses_generic_priority_storage_only(
    db,
    user,
    monkeypatch,
) -> None:
    _add_yazio(db, user)
    original_add = db.add

    def add_without_legacy_mirror(instance, *args, **kwargs):
        if isinstance(instance, UserProviderPreference):
            pytest.fail("provider preference writes must not add legacy rows")
        return original_add(instance, *args, **kwargs)

    monkeypatch.setattr(db, "add", add_without_legacy_mirror)

    snapshot = source_priority_compatibility.set_provider_preference(
        db,
        user_id=user.id,
        data_area="nutrition",
        provider_key="yazio",
    )
    db.commit()

    assert snapshot.provider_key == "yazio"
    assert db.scalar(
        select(UserProviderPriority).where(
            UserProviderPriority.user_id == user.id,
            UserProviderPriority.data_area == "nutrition",
        )
    ) is not None


def test_provider_preference_api_deletes_legacy_only_value(
    client: TestClient,
    user,
    db,
) -> None:
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    csrf = _login(client)

    assert client.get(PATH).json() == {
        "preferences": [{"data_area": "nutrition", "provider_key": "yazio"}]
    }

    deleted = client.delete(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 204
    db.expire_all()
    assert db.get(UserProviderPreference, (user.id, "nutrition")) is None
    assert client.get(PATH).json() == {"preferences": []}


def test_provider_preferences_get_is_grouped_and_keeps_unavailable_persisted_entries(
    client: TestClient,
    user,
    db,
) -> None:
    _add_priority_policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "yazio", 2),
        PriorityRuleSpec("weight", None, "yazio", 1),
        PriorityRuleSpec("nutrition", None, "google_health", 1),
    )
    _login(client)

    response = client.get(PATH)

    assert response.status_code == 200
    assert response.json() == {
        "preferences": [
            {"data_area": "nutrition", "provider_key": "google_health"},
            {"data_area": "nutrition", "provider_key": "yazio"},
            {"data_area": "weight", "provider_key": "yazio"},
        ]
    }


def test_provider_preference_put_replaces_complete_list_atomically(
    client: TestClient,
    user,
    db,
    monkeypatch,
) -> None:
    _add_yazio(db, user)
    _add_priority_policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "google_health", 1),
        PriorityRuleSpec("nutrition", None, "yazio", 2),
    )
    other = User(username="other", password_hash="not-used", timezone="Europe/Berlin")
    db.add(other)
    db.flush()
    _add_priority_policy(
        db,
        other,
        PriorityRuleSpec("nutrition", None, "yazio", 1),
    )
    csrf = _login(client)

    missing_csrf = client.put(
        f"{PATH}/nutrition",
        json={
            "providers": [
                {"provider_key": "google_health", "priority_rank": 2},
                {"provider_key": "yazio", "priority_rank": 1},
            ]
        },
    )
    assert missing_csrf.status_code == 403

    replaced = client.put(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
        json={
            "providers": [
                {"provider_key": "google_health", "priority_rank": 2},
                {"provider_key": "yazio", "priority_rank": 1},
            ]
        },
    )
    assert replaced.status_code == 200
    assert client.get(PATH).json() == {
        "preferences": [
            {"data_area": "nutrition", "provider_key": "yazio"},
            {"data_area": "nutrition", "provider_key": "google_health"},
        ]
    }
    assert [
        (row.provider_key, row.priority)
        for row in db.scalars(
            select(UserProviderPriority)
            .where(
                UserProviderPriority.user_id == user.id,
                UserProviderPriority.data_area == "nutrition",
            )
            .order_by(UserProviderPriority.priority)
        )
    ] == [("yazio", 1), ("google_health", 2)]

    invalid = client.put(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
        json={
            "providers": [
                {"provider_key": "yazio", "priority_rank": 1},
                {"provider_key": "yazio", "priority_rank": 2},
            ]
        },
    )
    assert invalid.status_code == 422
    assert [
        (row.provider_key, row.priority)
        for row in db.scalars(
            select(UserProviderPriority)
            .where(
                UserProviderPriority.user_id == user.id,
                UserProviderPriority.data_area == "nutrition",
            )
            .order_by(UserProviderPriority.priority)
        )
    ] == [("yazio", 1), ("google_health", 2)]
    assert [
        (row.provider_key, row.priority)
        for row in db.scalars(
            select(UserProviderPriority)
            .where(
                UserProviderPriority.user_id == other.id,
                UserProviderPriority.data_area == "nutrition",
            )
        )
    ] == [("yazio", 1)]


def test_provider_priority_replacement_locks_user_row(db, user, monkeypatch) -> None:
    statements = []
    original_scalar = db.scalar

    def scalar(statement, *args, **kwargs):
        statements.append(statement)
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db, "scalar", scalar)
    source_priority_compatibility.replace_provider_preferences(
        db,
        user_id=user.id,
        data_area="nutrition",
        provider_keys=("yazio",),
    )
    db.commit()

    assert any(getattr(statement, "_for_update_arg", None) is not None for statement in statements)


def test_provider_preference_delete_removes_all_entries_for_area(
    client: TestClient,
    user,
    db,
) -> None:
    _add_priority_policy(
        db,
        user,
        PriorityRuleSpec("nutrition", None, "google_health", 1),
        PriorityRuleSpec("nutrition", None, "yazio", 2),
        PriorityRuleSpec("weight", None, "yazio", 1),
    )
    csrf = _login(client)

    deleted = client.delete(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
    )

    assert deleted.status_code == 204
    assert client.get(PATH).json() == {
        "preferences": [{"data_area": "weight", "provider_key": "yazio"}]
    }


def test_provider_preference_put_rejects_invalid_rank_without_mutation(
    client: TestClient,
    user,
    db,
    monkeypatch,
) -> None:
    _add_yazio(db, user)
    _add_priority_policy(db, user, PriorityRuleSpec("nutrition", None, "yazio", 1))
    csrf = _login(client)

    response = client.put(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
        json={
            "providers": [
                {"provider_key": "yazio", "priority_rank": 2},
                {"provider_key": "google_health", "priority_rank": 3},
            ]
        },
    )

    assert response.status_code == 422
    assert client.get(PATH).json() == {
        "preferences": [{"data_area": "nutrition", "provider_key": "yazio"}]
    }


def test_provider_availability_keeps_stable_status_values(client: TestClient, user, db) -> None:
    _add_yazio(db, user)
    _login(client)

    response = client.get(AVAILABILITY_PATH)

    assert response.status_code == 200
    statuses = {item["provider_key"]: item["status"] for item in response.json()["providers"]}
    assert statuses["yazio"] == "available"
    assert set(statuses.values()) <= {
        "available",
        "disabled",
        "not_configured",
        "reauth_required",
        "no_data",
    }


def test_provider_preference_api_persists_unavailable_provider(
    client: TestClient,
    user,
    db,
) -> None:
    csrf = _login(client)
    response = client.put(
        f"{PATH}/nutrition",
        headers={"X-CSRF-Token": csrf},
        json={"provider_key": "yazio"},
    )
    assert response.status_code == 200
    assert response.json() == {"data_area": "nutrition", "provider_key": "yazio"}
    assert client.get(PATH).json() == {
        "preferences": [{"data_area": "nutrition", "provider_key": "yazio"}]
    }


def test_provider_availability_reports_owned_yazio_without_exposing_source_instance(
    client: TestClient,
    user,
    db,
) -> None:
    _add_yazio(db, user)
    _login(client)

    response = client.get(AVAILABILITY_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "data_area": "nutrition",
        "providers": [
            {
                "provider_key": "apple_health",
                "available": False,
                "status": "no_data",
            },
            {
                "provider_key": "google_health",
                "available": False,
                "status": "disabled",
            },
            {
                "provider_key": "yazio",
                "available": True,
                "status": "available",
            },
        ],
    }
    assert "source_instance" not in response.text
    assert str(user.id) not in response.text


def test_nutrition_preference_resolution_uses_owned_provider_without_source_instance_publication(
    user,
    db,
) -> None:
    from app.analytics.provider_selection import resolve_nutrition_provider

    _add_yazio(db, user)
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    other = User(username="other", password_hash="not-used", timezone="Europe/Berlin")
    db.add(other)
    db.flush()
    _add_yazio(db, other)
    db.add(UserProviderPreference(user_id=other.id, data_area="nutrition", provider_key="yazio"))
    db.commit()

    selection = resolve_nutrition_provider(db, user_id=user.id)

    assert selection is not None
    assert selection.provider_key == "yazio"
    assert selection.source_instance_id is not None
    other_source_id = db.scalar(select(YazioConnection.id).where(YazioConnection.user_id == other.id))
    assert selection.source_instance_id != other_source_id


def test_nutrition_preference_resolution_returns_none_without_persisted_preference(
    user,
    db,
) -> None:
    from app.analytics.provider_selection import resolve_nutrition_provider

    assert resolve_nutrition_provider(db, user_id=user.id) is None


def test_micronutrients_without_source_uses_configured_provider(
    client: TestClient,
    user,
    db,
) -> None:
    _add_yazio(db, user)
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    _login(client)

    response = client.get(
        "/api/v1/analytics/micronutrients?start=2026-09-01&end=2026-09-01"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] is None
    assert payload["selected_provider"]["provider_key"] == "yazio"
    assert payload["last_updated_at"] is None
    assert payload["recorded_days"] == 0
    assert all(item["status"] == "no_data" for item in payload["nutrients"])


@pytest.mark.parametrize("days", [1, 30, 31, 32, 90, 180, 365, 366])
def test_micronutrients_preference_accepts_bounded_long_ranges(
    client: TestClient,
    user,
    db,
    days: int,
) -> None:
    _add_yazio(db, user)
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    _login(client)

    end = date(2026, 1, 1)
    start = end - timedelta(days=days - 1)
    response = client.get(
        f"/api/v1/analytics/micronutrients?start={start.isoformat()}&end={end.isoformat()}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["start_date"] == start.isoformat()
    assert payload["end_date"] == end.isoformat()
    assert payload["recorded_days"] == 0
    assert len(payload["nutrients"]) == 26


def test_micronutrients_preference_keeps_period_all_rejected(
    client: TestClient,
    user,
    db,
) -> None:
    _add_yazio(db, user)
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    _login(client)

    response = client.get(
        "/api/v1/analytics/micronutrients?start=2026-01-01&end=2026-01-01&period=all"
    )

    assert response.status_code == 422

def test_micronutrients_without_preference_keeps_legacy_yazio_fallback(
    client: TestClient,
    user,
    db,
    monkeypatch,
) -> None:
    from app.analytics.micronutrient_shadow import (
        MicronutrientEvaluation,
        MicronutrientShadowState,
    )

    _login(client)
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", True
    )
    canonical_calls = 0

    def observe_canonical(*args, **kwargs):
        nonlocal canonical_calls
        canonical_calls += 1
        return MicronutrientEvaluation(state=MicronutrientShadowState.NOT_COMPARABLE)

    monkeypatch.setattr("app.api.analytics.run_micronutrient_canonical_read", observe_canonical)

    response = client.get(
        "/api/v1/analytics/micronutrients?start=2026-09-01&end=2026-09-01"
    )

    assert response.status_code == 200
    payload = response.json()
    assert canonical_calls == 1
    assert payload["source"] == "yazio_export_v1"
    assert "selected_provider" not in payload
    assert payload["recorded_days"] == 0

def test_micronutrients_preference_path_never_reads_legacy_values(
    client: TestClient,
    user,
    db,
    monkeypatch,
) -> None:
    _add_yazio(db, user)
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    _login(client)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("configured provider path must not read legacy values")

    discovery_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.api.analytics.discover_nutrition_provider_metadata",
        lambda *args, **kwargs: discovery_calls.append(kwargs) or None,
    )
    monkeypatch.setattr("app.api.analytics.read_legacy_micronutrient_period", fail_legacy)
    response = client.get(
        "/api/v1/analytics/micronutrients?start=2026-09-01&end=2026-09-01"
    )

    assert response.status_code == 200
    assert discovery_calls[0]["provider_key"] == "yazio"
    payload = response.json()
    assert payload["source"] is None
    assert payload["selected_provider"]["provider_key"] == "yazio"
    assert payload["recorded_days"] == 0
    assert all(item["status"] == "no_data" for item in payload["nutrients"])

def test_micronutrients_explicit_legacy_source_bypasses_provider_preference(
    client: TestClient,
    user,
    db,
) -> None:
    _add_yazio(db, user)
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    _login(client)

    response = client.get(
        "/api/v1/analytics/micronutrients"
        "?start=2026-09-01&end=2026-09-01&source=yazio_export_v1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "yazio_export_v1"
    assert "selected_provider" not in payload


def test_micronutrients_without_source_rejects_unavailable_saved_provider(
    client: TestClient,
    user,
    db,
) -> None:
    db.add(UserProviderPreference(user_id=user.id, data_area="nutrition", provider_key="yazio"))
    db.commit()
    _login(client)

    response = client.get(
        "/api/v1/analytics/micronutrients?start=2026-09-01&end=2026-09-01"
    )

    assert response.status_code == 503
    assert response.json()["type"] == "urn:calograph:problem:provider-selection-unavailable"
    assert db.get(UserProviderPreference, (user.id, "nutrition")) is not None
def test_weight_priority_prefers_yazio_over_apple(
    client: TestClient,
    user,
    db,
) -> None:
    day = date(2026, 9, 15)
    _add_yazio(db, user)
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["yazio"],
        value=Decimal("72.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
        source_identifier="yazio-account",
    )
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["apple_health"],
        value=Decimal("71.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 9, tzinfo=UTC),
        source_identifier="apple-device",
    )
    _add_priority_policy(
        db,
        user,
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "yazio", 1),
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "apple_health", 2),
    )
    _login(client)

    response = client.get(WEIGHT_PATH, params={"start": day, "end": day})

    assert response.status_code == 200
    assert response.json()["selected_provider"] == {"provider_key": "yazio"}
    assert response.json()["points"] == [{"date": day.isoformat(), "weight_kg": 72.5}]


def test_weight_priority_prefers_apple_over_yazio(
    client: TestClient,
    user,
    db,
) -> None:
    day = date(2026, 9, 15)
    _add_yazio(db, user)
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["yazio"],
        value=Decimal("72.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
        source_identifier="yazio-account",
    )
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["apple_health"],
        value=Decimal("71.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 9, tzinfo=UTC),
        source_identifier="apple-device",
    )
    _add_priority_policy(
        db,
        user,
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "apple_health", 1),
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "yazio", 2),
    )
    _login(client)

    response = client.get(WEIGHT_PATH, params={"start": day, "end": day})

    assert response.status_code == 200
    assert response.json()["selected_provider"] == {"provider_key": "apple_health"}
    assert response.json()["points"] == [{"date": day.isoformat(), "weight_kg": 71.5}]


def test_weight_priority_falls_back_to_health_auto_export_without_forward_fill(
    client: TestClient,
    user,
    db,
) -> None:
    previous_day = date(2026, 9, 14)
    day = date(2026, 9, 15)
    _add_yazio(db, user)
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["yazio"],
        value=Decimal("72.5"),
        local_date=previous_day,
        start_at=datetime(2026, 9, 14, 8, tzinfo=UTC),
        source_identifier="yazio-account",
    )
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["health_auto_export"],
        value=Decimal("70.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 9, tzinfo=UTC),
        source_identifier="health-device",
    )
    _add_priority_policy(
        db,
        user,
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "yazio", 1),
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "apple_health", 2),
        PriorityRuleSpec(WEIGHT_DATA_AREA, None, "health_auto_export", 3),
    )
    _login(client)

    response = client.get(
        WEIGHT_PATH,
        params={"start": day, "end": day + timedelta(days=1)},
    )

    assert response.status_code == 200
    assert response.json()["selected_provider"] == {"provider_key": "yazio"}
    assert response.json()["points"] == [{"date": day.isoformat(), "weight_kg": 70.5}]


def test_weight_priority_uses_latest_actual_sample_within_provider_day(
    client: TestClient,
    user,
    db,
) -> None:
    day = date(2026, 9, 15)
    _add_yazio(db, user)
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["yazio"],
        value=Decimal("72.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
        source_identifier="yazio-account",
    )
    _add_weight_sample(
        db,
        user,
        source_type=WEIGHT_PROVIDER_SOURCE_TYPES["yazio"],
        value=Decimal("71.5"),
        local_date=day,
        start_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
        source_identifier="yazio-account",
    )
    _add_priority_policy(db, user, PriorityRuleSpec(WEIGHT_DATA_AREA, None, "yazio", 1))
    _login(client)

    response = client.get(WEIGHT_PATH, params={"start": day, "end": day})

    assert response.status_code == 200
    assert response.json()["points"] == [{"date": day.isoformat(), "weight_kg": 71.5}]


def test_weight_api_is_opt_in_and_user_scoped(client: TestClient, user, db) -> None:
    sample_day = date(2026, 9, 15)
    _add_yazio(db, user)
    _add_sample(
        db,
        user,
        metric_type="weight_kg",
        source_type="yazio_export_v1",
        value=Decimal("72.5"),
        local_date=sample_day,
        source_identifier="yazio-account",
    )
    other = User(username="other-weight", password_hash="not-used", timezone="Europe/Berlin")
    db.add(other)
    db.flush()
    _add_sample(
        db,
        other,
        metric_type="weight_kg",
        source_type="yazio_export_v1",
        value=Decimal("99"),
        local_date=sample_day,
    )
    db.commit()
    _login(client)
    without_preference = client.get(
        "/api/v1/analytics/weight?start=2026-09-15&end=2026-09-15"
    )
    assert without_preference.status_code == 200
    assert without_preference.json()["selected_provider"] is None
    assert without_preference.json()["points"] == []

    db.add(
        UserProviderPreference(
            user_id=user.id,
            data_area=WEIGHT_DATA_AREA,
            provider_key="yazio",
        )
    )
    db.commit()
    response = client.get(
        "/api/v1/analytics/weight?start=2026-09-15&end=2026-09-15"
    )

    assert response.status_code == 200
    assert response.json() == {
        "start_date": "2026-09-15",
        "end_date": "2026-09-15",
        "selected_provider": {"provider_key": "yazio"},
        "points": [{"date": "2026-09-15", "weight_kg": 72.5}],
    }


def test_activity_availability_accepts_explicit_zero_samples(client: TestClient, user, db) -> None:
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="apple_health_xml",
        value=Decimal("0"),
        local_date=date(2026, 9, 15),
    )
    db.commit()
    _login(client)

    response = client.get(f"/api/v1/settings/provider-availability/{ACTIVITY_ENERGY_DATA_AREA}")

    assert response.status_code == 200
    statuses = {item["provider_key"]: item for item in response.json()["providers"]}
    assert statuses["apple_health"] == {
        "provider_key": "apple_health",
        "available": True,
        "status": "available",
    }

def test_activity_provider_change_creates_effective_target_version(
    client: TestClient,
    user,
    db,
) -> None:
    target = db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == user.id))
    assert target is not None
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="yazio_export_v1",
        value=Decimal("400"),
        local_date=date(2026, 9, 15),
    )
    db.commit()
    csrf = _login(client)

    response = client.put(
        f"{PATH}/{ACTIVITY_ENERGY_DATA_AREA}",
        headers={"X-CSRF-Token": csrf},
        json={"provider_key": "yazio"},
    )

    assert response.status_code == 200
    db.expire_all()
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    assert len(targets) == 2
    assert targets[0].activity_source_type == "apple_health_xml"
    assert targets[0].valid_to is not None
    assert targets[1].activity_source_type == "yazio_export_v1"
    assert targets[1].valid_from == targets[0].valid_to


def test_activity_provider_priority_snapshots_preserve_history_and_same_day_updates(
    client: TestClient,
    user,
    db,
) -> None:
    target = db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == user.id))
    assert target is not None
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="apple_health_xml",
        value=Decimal("300"),
        local_date=date(2026, 9, 15),
    )
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="yazio_export_v1",
        value=Decimal("400"),
        local_date=date(2026, 9, 15),
    )
    db.commit()
    csrf = _login(client)

    first = client.put(
        f"{PATH}/{ACTIVITY_ENERGY_DATA_AREA}",
        headers={"X-CSRF-Token": csrf},
        json={"provider_keys": ["yazio", "apple_health"]},
    )

    assert first.status_code == 200
    db.expire_all()
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    assert len(targets) == 2
    historical, current = targets
    first_snapshot_rows = list(
        db.scalars(
            select(NutritionTargetActivitySource)
            .where(NutritionTargetActivitySource.target_id == current.id)
            .order_by(NutritionTargetActivitySource.priority)
        )
    )
    assert [(row.priority, row.provider_key, row.source_type) for row in first_snapshot_rows] == [
        (1, "yazio", "yazio_export_v1"),
        (2, "apple_health", "apple_health_xml"),
    ]

    second = client.put(
        f"{PATH}/{ACTIVITY_ENERGY_DATA_AREA}",
        headers={"X-CSRF-Token": csrf},
        json={"provider_keys": ["apple_health", "yazio"]},
    )

    assert second.status_code == 200
    db.expire_all()
    targets_after_same_day_update = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    assert len(targets_after_same_day_update) == 2
    historical_after_update, current_after_update = targets_after_same_day_update
    assert historical_after_update.id == historical.id
    assert historical_after_update.activity_source_type == "apple_health_xml"
    assert current_after_update.id == current.id
    assert current_after_update.activity_source_type == "apple_health_xml"
    second_snapshot_rows = list(
        db.scalars(
            select(NutritionTargetActivitySource)
            .where(NutritionTargetActivitySource.target_id == current.id)
            .order_by(NutritionTargetActivitySource.priority)
        )
    )
    assert [(row.priority, row.provider_key, row.source_type) for row in second_snapshot_rows] == [
        (1, "apple_health", "apple_health_xml"),
        (2, "yazio", "yazio_export_v1"),
    ]


def test_current_day_target_put_preserves_complete_activity_policy_chain(
    client: TestClient,
    user,
    db,
) -> None:
    today = datetime.now(UTC).date()
    target = db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == user.id))
    assert target is not None
    target.valid_from = today
    target.activity_mode = "off"
    target.activity_source_type = None
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="apple_health_xml",
        value=Decimal("300"),
        local_date=today,
    )
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="yazio_export_v1",
        value=Decimal("400"),
        local_date=today,
    )
    db.commit()
    csrf = _login(client)

    preference = client.put(
        f"{PATH}/{ACTIVITY_ENERGY_DATA_AREA}",
        headers={"X-CSRF-Token": csrf},
        json={"provider_keys": ["yazio", "apple_health"]},
    )
    assert preference.status_code == 200
    db.expire_all()
    target = db.scalar(
        select(NutritionTarget).where(
            NutritionTarget.user_id == user.id,
            NutritionTarget.valid_from == today,
        )
    )
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    db.commit()

    response = client.put(
        f"/api/v1/settings/targets/{today.isoformat()}",
        headers={"X-CSRF-Token": csrf},
        json={
            "valid_from": today.isoformat(),
            "calories_kcal": "2000",
            "maintenance_kcal": None,
            "target_weight_min_kg": None,
            "target_weight_max_kg": None,
            "activity_mode": "full",
            "activity_source_type": "yazio_export_v1",
            "protein_g": "120",
            "carbs_g": None,
            "fat_g": None,
            "fiber_g": None,
        },
    )

    assert response.status_code == 200
    db.expire_all()
    target = db.scalar(
        select(NutritionTarget).where(
            NutritionTarget.user_id == user.id,
            NutritionTarget.valid_from == today,
        )
    )
    assert target is not None
    assert target.activity_source_type == "yazio_export_v1"
    snapshots = list(
        db.scalars(
            select(NutritionTargetActivitySource)
            .where(NutritionTargetActivitySource.target_id == target.id)
            .order_by(NutritionTargetActivitySource.priority)
        )
    )
    assert [(row.priority, row.provider_key, row.source_type) for row in snapshots] == [
        (1, "yazio", "yazio_export_v1"),
        (2, "apple_health", "apple_health_xml"),
    ]


def test_historical_activity_mode_change_preserves_target_and_snapshot_chain(
    client: TestClient,
    user,
    db,
) -> None:
    target = db.scalar(select(NutritionTarget).where(NutritionTarget.user_id == user.id))
    assert target is not None
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    db.flush()
    db.add_all(
        [
            NutritionTargetActivitySource(
                target_id=target.id,
                user_id=user.id,
                priority=1,
                provider_key="apple_health",
                source_type="apple_health_xml",
            ),
            NutritionTargetActivitySource(
                target_id=target.id,
                user_id=user.id,
                priority=2,
                provider_key="yazio",
                source_type="yazio_export_v1",
            ),
        ]
    )
    db.commit()
    csrf = _login(client)

    response = client.put(
        f"{PATH.replace('/provider-preferences', '/targets')}/{target.valid_from.isoformat()}",
        headers={"X-CSRF-Token": csrf},
        json={
            "valid_from": target.valid_from.isoformat(),
            "calories_kcal": "2000",
            "maintenance_kcal": None,
            "target_weight_min_kg": None,
            "target_weight_max_kg": None,
            "activity_mode": "off",
            "activity_source_type": None,
            "protein_g": "120",
            "carbs_g": None,
            "fat_g": None,
            "fiber_g": None,
        },
    )

    assert response.status_code == 200
    db.expire_all()
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    assert len(targets) == 2
    historical, current = targets
    assert historical.activity_mode == "full"
    assert historical.activity_source_type == "apple_health_xml"
    snapshots = list(
        db.scalars(
            select(NutritionTargetActivitySource)
            .where(NutritionTargetActivitySource.target_id == historical.id)
            .order_by(NutritionTargetActivitySource.priority)
        )
    )
    assert [(row.priority, row.source_type) for row in snapshots] == [
        (1, "apple_health_xml"),
        (2, "yazio_export_v1"),
    ]
    assert current.activity_mode == "off"
    assert current.activity_source_type is None


def test_new_target_captures_complete_activity_provider_priority_chain(
    client: TestClient,
    user,
    db,
) -> None:
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="apple_health_xml",
        value=Decimal("300"),
        local_date=date(2026, 9, 15),
    )
    _add_sample(
        db,
        user,
        metric_type="active_energy_kcal",
        source_type="yazio_export_v1",
        value=Decimal("400"),
        local_date=date(2026, 9, 15),
    )
    db.commit()
    csrf = _login(client)
    preference = client.put(
        f"{PATH}/{ACTIVITY_ENERGY_DATA_AREA}",
        headers={"X-CSRF-Token": csrf},
        json={"provider_keys": ["yazio", "apple_health"]},
    )
    assert preference.status_code == 200

    created = client.post(
        "/api/v1/settings/targets",
        headers={"X-CSRF-Token": csrf},
        json={
            "valid_from": "2026-09-19",
            "calories_kcal": "2000",
            "maintenance_kcal": None,
            "target_weight_min_kg": None,
            "target_weight_max_kg": None,
            "activity_mode": "full",
            "activity_source_type": "yazio_export_v1",
            "protein_g": "120",
            "carbs_g": None,
            "fat_g": None,
            "fiber_g": None,
        },
    )

    assert created.status_code == 201
    new_target = db.scalar(
        select(NutritionTarget).where(
            NutritionTarget.user_id == user.id,
            NutritionTarget.valid_from == date(2026, 9, 19),
        )
    )
    assert new_target is not None
    snapshots = list(
        db.scalars(
            select(NutritionTargetActivitySource)
            .where(NutritionTargetActivitySource.target_id == new_target.id)
            .order_by(NutritionTargetActivitySource.priority)
        )
    )
    assert [(row.priority, row.provider_key, row.source_type) for row in snapshots] == [
        (1, "yazio", "yazio_export_v1"),
        (2, "apple_health", "apple_health_xml"),
    ]
