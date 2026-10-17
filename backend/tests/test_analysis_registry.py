from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.micronutrients import MICRONUTRIENTS
from app.models import User

REGISTRY_PATH = "/api/v1/analytics/registry"
PREFERENCES_PATH = "/api/v1/settings/analysis-preferences"
PASSWORD = "correct-horse-battery-staple"
DEFAULT_ORDER = ["weight", "nutrition", "micronutrients", "activity"]


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

    micronutrient_entries = [
        item for item in entries if item["category_key"] == "micronutrients"
    ]
    assert len(micronutrient_entries) == len(MICRONUTRIENTS)
    for item in micronutrient_entries:
        assert item["reader_key"] == "analytics.nutrition.micronutrients"
        assert item["status"] == "available"
        assert item["card_capable"] is True
        assert item["detail_capable"] is True
        assert item["period_capability"] == {
            "min_days": 1,
            "max_days": 365,
            "custom_range": True,
        }
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
    assert initial.json() == {
        "active_card_ids": DEFAULT_ORDER,
        "analysis_keys": [],
        "summary_order": DEFAULT_ORDER,
    }

    summary_order = ["nutrition", "weight", "activity", "micronutrients"]
    response = client.put(
        PREFERENCES_PATH,
        headers={"X-CSRF-Token": csrf},
        json={"analysis_keys": ["active_energy_kcal", "weight_kg"], "summary_order": summary_order},
    )
    assert response.status_code == 200
    assert response.json() == {
        "active_card_ids": summary_order,
        "analysis_keys": [],
        "summary_order": summary_order,
    }

    other = User(
        username="other-analysis-user",
        password_hash=user.password_hash,
        timezone="Europe/Berlin",
    )
    db.add(other)
    db.commit()
    second_client = TestClient(client.app)
    second_csrf = _login(second_client, "other-analysis-user")
    assert second_client.get(PREFERENCES_PATH).json() == {
        "active_card_ids": DEFAULT_ORDER,
        "analysis_keys": [],
        "summary_order": DEFAULT_ORDER,
    }

    second_order = ["activity", "micronutrients", "nutrition", "weight"]
    second_response = second_client.put(
        PREFERENCES_PATH,
        headers={"X-CSRF-Token": second_csrf},
        json={"analysis_keys": ["vitamin_a_ug"], "summary_order": second_order},
    )
    assert second_response.status_code == 200
    assert second_response.json() == {
        "active_card_ids": [*second_order, "vitamin_a_ug"],
        "analysis_keys": ["vitamin_a_ug"],
        "summary_order": second_order,
    }
    assert client.get(PREFERENCES_PATH).json() == response.json()
    assert second_client.get(PREFERENCES_PATH).json() == second_response.json()


def test_summary_order_default_and_old_client_omission(client: TestClient, user: User) -> None:
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    assert client.get(PREFERENCES_PATH).json() == {
        "active_card_ids": DEFAULT_ORDER,
        "analysis_keys": [],
        "summary_order": DEFAULT_ORDER,
    }

    saved_order = ["micronutrients", "activity", "weight", "nutrition"]
    saved = client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": ["vitamin_a_ug", "weight_kg"], "summary_order": saved_order},
    )
    assert saved.status_code == 200
    old_client = client.put(
        PREFERENCES_PATH, headers=headers, json={"analysis_keys": ["protein_g", "calcium_mg"]}
    )
    assert old_client.status_code == 200
    assert old_client.json() == {
        "active_card_ids": ["micronutrients", "activity", "weight", "nutrition", "protein_g", "calcium_mg"],
        "analysis_keys": ["protein_g", "calcium_mg"],
        "summary_order": saved_order,
    }
    assert client.get(PREFERENCES_PATH).json() == old_client.json()


def test_summary_order_accepts_all_valid_permutations(
    client: TestClient,
    user: User,
) -> None:
    headers = {"X-CSRF-Token": _login(client)}
    analysis_keys = ["vitamin_a_ug", "weight_kg", "protein_g"]

    for order in permutations(DEFAULT_ORDER):
        summary_order = list(order)
        response = client.put(
            PREFERENCES_PATH,
            headers=headers,
            json={"analysis_keys": analysis_keys, "summary_order": summary_order},
        )
        assert response.status_code == 200
        assert response.json() == {
            "active_card_ids": [*summary_order, "vitamin_a_ug", "protein_g"],
            "analysis_keys": ["vitamin_a_ug", "protein_g"],
            "summary_order": summary_order,
        }


@pytest.mark.parametrize(
    "invalid_order",
    [
        None,
        [],
        ["weight"],
        ["weight", "nutrition", "micronutrients"],
        ["weight", "nutrition", "micronutrients", "weight"],
        ["weight", "nutrition", "micronutrients", "unknown"],
        ["weight", "nutrition", "micronutrients", "activity", "activity"],
    ],
)
def test_invalid_summary_order_cannot_partially_replace_metrics(
    client: TestClient,
    user: User,
    invalid_order: list[str] | None,
) -> None:
    headers = {"X-CSRF-Token": _login(client)}
    baseline = {
        "active_card_ids": ["activity", "nutrition", "weight", "micronutrients", "vitamin_a_ug"],
        "analysis_keys": ["vitamin_a_ug"],
        "summary_order": ["activity", "nutrition", "weight", "micronutrients"],
    }
    assert client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": ["weight_kg", "vitamin_a_ug"], "summary_order": baseline["summary_order"]},
    ).status_code == 200

    rejected = client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": ["calcium_mg"], "summary_order": invalid_order},
    )
    assert rejected.status_code == 422
    assert client.get(PREFERENCES_PATH).json() == baseline
    summary_only = client.put(
        PREFERENCES_PATH, headers=headers, json={"summary_order": DEFAULT_ORDER}
    )
    assert summary_only.status_code == 200
    assert summary_only.json()["analysis_keys"] == ["vitamin_a_ug"]


def test_user_summary_order_defaults_are_independent(
    db: Session,
    user: User,
) -> None:
    other = User(
        username="other-summary-default-user",
        password_hash=user.password_hash,
        timezone="Europe/Berlin",
    )
    db.add(other)
    db.flush()
    db.refresh(user)
    db.refresh(other)
    assert user.summary_order == DEFAULT_ORDER
    assert other.summary_order == DEFAULT_ORDER

    user.summary_order = ["activity", "weight", "nutrition", "micronutrients"]
    db.flush()
    db.refresh(other)
    assert other.summary_order == DEFAULT_ORDER



def test_preferences_require_authentication_and_valid_csrf(
    client: TestClient,
    user: User,
) -> None:
    payload = {"analysis_keys": ["weight_kg"], "summary_order": DEFAULT_ORDER}
    assert client.get(PREFERENCES_PATH).status_code == 401
    assert client.put(PREFERENCES_PATH, json=payload).status_code == 401

    _login(client)
    assert client.put(PREFERENCES_PATH, json=payload).status_code == 403
    assert client.put(
        PREFERENCES_PATH,
        headers={"X-CSRF-Token": "invalid-token"},
        json=payload,
    ).status_code == 403
    assert client.get(PREFERENCES_PATH).json() == {
        "active_card_ids": DEFAULT_ORDER,
        "analysis_keys": [],
        "summary_order": DEFAULT_ORDER,
    }


def test_preferences_reject_duplicate_unknown_and_not_selectable_keys_without_partial_write(
    client: TestClient,
    user: User,
) -> None:
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    stored = {
        "active_card_ids": ["activity", "nutrition", "weight", "micronutrients"],
        "analysis_keys": [],
        "summary_order": ["activity", "nutrition", "weight", "micronutrients"],
    }
    initialized = client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"active_card_ids": stored["active_card_ids"]},
    )
    assert initialized.status_code == 200

    for keys in (
        ["weight_kg", "weight_kg"],
        ["unknown_metric"],
        ["steps"],
    ):
        response = client.put(
            PREFERENCES_PATH,
            headers=headers,
            json={"analysis_keys": keys, "summary_order": DEFAULT_ORDER},
        )
        assert response.status_code == 422
        assert client.get(PREFERENCES_PATH).json() == stored



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
    assert response.json() == {
        "active_card_ids": [*DEFAULT_ORDER, *selected],
        "analysis_keys": selected,
        "summary_order": DEFAULT_ORDER,
    }
    rejected = client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": [*selected, "steps"]},
    )
    assert rejected.status_code == 422
    assert client.get(PREFERENCES_PATH).json() == {
        "active_card_ids": [*DEFAULT_ORDER, *selected],
        "analysis_keys": selected,
        "summary_order": DEFAULT_ORDER,
    }

def test_preferences_accept_empty_selection_and_preserve_order(
    client: TestClient,
    user: User,
) -> None:
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}
    selected = ["vitamin_a_ug", "weight_kg", "active_energy_kcal"]
    expected = {
        "active_card_ids": [*DEFAULT_ORDER, "vitamin_a_ug"],
        "analysis_keys": ["vitamin_a_ug"],
        "summary_order": DEFAULT_ORDER,
    }
    assert client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": selected},
    ).json() == expected
    empty = {"active_card_ids": DEFAULT_ORDER, "analysis_keys": [], "summary_order": DEFAULT_ORDER}
    assert client.put(
        PREFERENCES_PATH,
        headers=headers,
        json={"analysis_keys": []},
    ).json() == empty
    assert client.get(PREFERENCES_PATH).json() == empty
