from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import User, UserProviderPreference, YazioConnection
from app.provider_preferences import (
    NUTRITION_DATA_AREA,
    SUPPORTED_PROVIDER_KEYS,
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

    monkeypatch.setattr("app.api.analytics.read_legacy_micronutrient_period", fail_legacy)

    response = client.get(
        "/api/v1/analytics/micronutrients?start=2026-09-01&end=2026-09-01"
    )

    assert response.status_code == 200
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
