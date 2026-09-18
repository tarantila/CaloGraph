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
    User,
    UserProviderPreference,
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

PATH = "/api/v1/settings/provider-preferences"
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

    stored = db.get(UserProviderPreference, (user.id, "nutrition"))
    assert stored is not None
    assert stored.provider_key == "yazio"

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
    assert db.get(UserProviderPreference, (user.id, "nutrition")) is None
    assert db.get(UserProviderPreference, (other.id, "nutrition")) is not None


def test_provider_preference_api_rejects_unavailable_provider(
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
    assert response.status_code == 409
    assert response.json()["type"] == "urn:calograph:problem:provider-not-available"


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
