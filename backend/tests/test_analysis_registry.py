from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.micronutrients import MICRONUTRIENTS
from app.models import User

REGISTRY_PATH = "/api/v1/analytics/registry"
PREFERENCES_PATH = "/api/v1/settings/analysis-preferences"
PASSWORD = "correct-horse-battery-staple"


def _login(client: TestClient, username: str = "admin") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_registry_exposes_capabilities_without_nutrition_data_and_has_frontend_metadata(
    client: TestClient,
    user: User,
) -> None:
    _login(client)
    response = client.get(REGISTRY_PATH)

    assert response.status_code == 200
    entries = response.json()["analyses"]
    by_key = {item["analysis_key"]: item for item in entries}
    assert len(entries) == len(by_key)
    assert by_key["weight_kg"]["status"] == "available"
    assert by_key["active_energy_kcal"]["status"] == "available"
    nutrition_keys = [
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
    ]
    for key in nutrition_keys:
        assert by_key[key]["status"] == "available"
        assert by_key[key]["card_capable"] is True
        assert by_key[key]["detail_capable"] is True
        assert by_key[key]["reader_key"] == "analytics.nutrition.analysis"
        assert by_key[key]["period_capability"]["max_days"] == 365
    assert by_key["water_intake_ml"]["category_key"] == "fluid"
    assert by_key["steps"]["status"] == "coming_soon"
    assert by_key["vitamin_a_ug"]["detail_capable"] is True
    assert set(by_key["weight_kg"]) == {
        "analysis_key",
        "category_key",
        "domain_key",
        "status",
        "card_capable",
        "detail_capable",
        "reader_key",
        "period_capability",
    }
    assert {item["analysis_key"] for item in entries if item["category_key"] == "micronutrients"} == {
        item.metric_type for item in MICRONUTRIENTS
    }

    repository_metadata_path = Path(__file__).resolve().parents[2] / "frontend/src/analysis-presentation.json"
    container_metadata_path = Path(__file__).resolve().parents[1] / "frontend/src/analysis-presentation.json"
    metadata_path = repository_metadata_path if repository_metadata_path.is_file() else container_metadata_path
    presentation = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert set(by_key) <= set(presentation)


def test_preferences_are_user_scoped_empty_by_default_and_replace_atomically(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    csrf = _login(client)
    initial = client.get(PREFERENCES_PATH)
    assert initial.status_code == 200
    assert initial.json() == {"analysis_keys": []}

    response = client.put(
        PREFERENCES_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"analysis_keys": ["active_energy_kcal", "weight_kg"]},
    )
    assert response.status_code == 200
    assert response.json() == {"analysis_keys": ["active_energy_kcal", "weight_kg"]}

    other = User(
        username="other-analysis-user",
        password_hash=user.password_hash,
        timezone="Europe/Berlin",
    )
    db.add(other)
    db.commit()
    second_client = TestClient(client.app)
    second_csrf = _login(second_client, "other-analysis-user")
    assert second_client.get(PREFERENCES_PATH).json() == {"analysis_keys": []}
    second_response = second_client.put(
        PREFERENCES_PATH,
        headers={"X-CSRF-Token": second_csrf},
        json={"analysis_keys": ["vitamin_a_ug"]},
    )
    assert second_response.status_code == 200
    assert second_response.json() == {"analysis_keys": ["vitamin_a_ug"]}
    assert client.get(PREFERENCES_PATH).json() == response.json()
    assert second_client.get(PREFERENCES_PATH).json() == second_response.json()


def test_preferences_require_authentication_and_valid_csrf(
    client: TestClient,
    user: User,
) -> None:
    payload = {"analysis_keys": ["weight_kg"]}
    assert client.get(PREFERENCES_PATH).status_code == 401
    assert client.put(PREFERENCES_PATH, json=payload).status_code == 401

    _login(client)
    assert client.put(PREFERENCES_PATH, json=payload).status_code == 403
    assert client.put(
        PREFERENCES_PATH,
        headers={"X-CSRF-Token": "invalid-token"},
        json=payload,
    ).status_code == 403
    assert client.get(PREFERENCES_PATH).json() == {"analysis_keys": []}


def test_preferences_reject_duplicate_unknown_and_not_selectable_keys_without_partial_write(
    client: TestClient,
    user: User,
) -> None:
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    assert client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": ["weight_kg"]},
    ).status_code == 200

    for keys in (
        ["weight_kg", "weight_kg"],
        ["unknown_metric"],
        ["steps"],
    ):
        response = client.put(PREFERENCES_PATH, headers=headers, json={"analysis_keys": keys})
        assert response.status_code == 422
        assert client.get(PREFERENCES_PATH).json() == {"analysis_keys": ["weight_kg"]}


def test_preferences_accept_ordered_nutrition_analysis_keys(
    client: TestClient,
    user: User,
) -> None:
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    selected = [
        "saturated_fat_g",
        "dietary_energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
    ]
    response = client.put(PREFERENCES_PATH, headers=headers, json={"analysis_keys": selected})
    assert response.status_code == 200
    assert response.json() == {"analysis_keys": selected}

    rejected = client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": [*selected, "steps"]},
    )
    assert rejected.status_code == 422
    assert client.get(PREFERENCES_PATH).json() == {"analysis_keys": selected}


def test_preferences_accept_empty_selection_and_preserve_order(
    client: TestClient,
    user: User,
) -> None:
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    selected = ["vitamin_a_ug", "weight_kg", "active_energy_kcal"]
    assert client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": selected},
    ).json() == {"analysis_keys": selected}
    assert client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": []},
    ).json() == {"analysis_keys": []}
    assert client.get(PREFERENCES_PATH).json() == {"analysis_keys": []}
