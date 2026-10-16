from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, UserAnalysisPreference

PREFERENCES_PATH = "/api/v1/settings/analysis-preferences"
PASSWORD = "correct-horse-battery-staple"
DEFAULT_CARDS = ["weight", "nutrition", "micronutrients", "activity"]


def _login(client: TestClient, username: str = "admin") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _put(client: TestClient, csrf: str, payload: dict) -> TestClient:
    return client.put(PREFERENCES_PATH, headers={"X-CSRF-Token": csrf}, json=payload)


def test_get_uses_default_canonical_cards_and_meals_start_inactive(client: TestClient, user: User) -> None:
    _login(client)
    response = client.get(PREFERENCES_PATH)
    assert response.status_code == 200
    assert response.json() == {
        "active_card_ids": DEFAULT_CARDS,
        "analysis_keys": [],
        "summary_order": DEFAULT_CARDS,
    }


def test_canonical_only_mixed_sequence_round_trips_and_drives_legacy_projections(
    client: TestClient, user: User
) -> None:
    csrf = _login(client)
    cards = ["protein_g", "activity", "meals_today", "weight", "vitamin_a_ug", "nutrition"]
    response = _put(client, csrf, {"active_card_ids": cards})
    assert response.status_code == 200
    assert response.json() == {
        "active_card_ids": cards,
        "analysis_keys": ["protein_g", "vitamin_a_ug"],
        "summary_order": ["activity", "weight", "nutrition", "micronutrients"],
    }
    assert client.get(PREFERENCES_PATH).json() == response.json()


def test_canonical_empty_list_is_allowed_and_replaces_metric_rows(client: TestClient, user: User, db: Session) -> None:
    csrf = _login(client)
    assert _put(client, csrf, {"active_card_ids": ["protein_g"]}).status_code == 200
    response = _put(client, csrf, {"active_card_ids": []})
    assert response.status_code == 200
    assert response.json() == {
        "active_card_ids": [], "analysis_keys": [], "summary_order": DEFAULT_CARDS
    }
    assert db.scalars(select(UserAnalysisPreference).where(UserAnalysisPreference.user_id == user.id)).all() == []


def test_canonical_aliases_duplicates_and_invalid_metrics_reject_without_mutation(
    client: TestClient, user: User
) -> None:
    csrf = _login(client)
    baseline = _put(client, csrf, {"active_card_ids": ["weight", "protein_g"]})
    assert baseline.status_code == 200
    for cards in (
        ["weight", "weight_kg"],
        ["activity", "active_energy_kcal"],
        ["protein_g", "protein_g"],
        ["steps"],
        ["unknown_metric"],
    ):
        response = _put(client, csrf, {"active_card_ids": cards})
        assert response.status_code == 422
        assert client.get(PREFERENCES_PATH).json() == baseline.json()


def test_canonical_and_legacy_projections_must_agree(client: TestClient, user: User) -> None:
    csrf = _login(client)
    baseline = _put(client, csrf, {"active_card_ids": ["weight", "protein_g"]})
    assert baseline.status_code == 200
    response = _put(
        client,
        csrf,
        {"active_card_ids": ["weight", "protein_g"], "analysis_keys": ["calcium_mg"]},
    )
    assert response.status_code == 422
    assert client.get(PREFERENCES_PATH).json() == baseline.json()


def test_legacy_metric_replacement_preserves_builtin_and_meals_slots(client: TestClient, user: User) -> None:
    csrf = _login(client)
    initial = ["weight", "protein_g", "meals_today", "nutrition", "vitamin_a_ug", "activity"]
    assert _put(client, csrf, {"active_card_ids": initial}).status_code == 200
    response = _put(client, csrf, {"analysis_keys": ["calcium_mg", "fat_g"]})
    assert response.status_code == 200
    assert response.json()["active_card_ids"] == [
        "weight", "calcium_mg", "meals_today", "nutrition", "fat_g", "activity"
    ]


def test_legacy_aliases_do_not_toggle_builtin_cards(client: TestClient, user: User) -> None:
    csrf = _login(client)
    assert _put(client, csrf, {"active_card_ids": ["weight", "activity", "meals_today"]}).status_code == 200
    for alias in ("weight_kg", "active_energy_kcal"):
        response = _put(client, csrf, {"analysis_keys": [alias]})
        assert response.status_code == 200
        assert response.json()["active_card_ids"] == ["weight", "activity", "meals_today"]
        assert response.json()["analysis_keys"] == []


def test_omitted_legacy_summary_order_does_not_reorder_existing_cards(client: TestClient, user: User) -> None:
    csrf = _login(client)
    cards = ["activity", "protein_g", "weight", "meals_today", "micronutrients"]
    assert _put(client, csrf, {"active_card_ids": cards}).status_code == 200
    response = _put(client, csrf, {"analysis_keys": ["fat_g"]})
    assert response.status_code == 200
    assert response.json()["active_card_ids"] == ["activity", "fat_g", "weight", "meals_today", "micronutrients"]
    assert response.json()["summary_order"] == ["activity", "weight", "micronutrients", "nutrition"]


def test_legacy_summary_order_reorders_only_active_builtin_slots(client: TestClient, user: User) -> None:
    csrf = _login(client)
    cards = ["activity", "protein_g", "weight", "meals_today", "nutrition"]
    assert _put(client, csrf, {"active_card_ids": cards}).status_code == 200
    response = _put(client, csrf, {"analysis_keys": ["protein_g"], "summary_order": ["weight", "activity", "nutrition", "micronutrients"]})
    assert response.status_code == 200
    assert response.json()["active_card_ids"] == ["weight", "protein_g", "activity", "meals_today", "nutrition"]
    assert client.get(PREFERENCES_PATH).json()["active_card_ids"] == response.json()["active_card_ids"]


def test_preferences_are_isolated_between_users(client: TestClient, user: User, db: Session) -> None:
    first_csrf = _login(client)
    cards = ["meals_today", "protein_g", "weight"]
    assert _put(client, first_csrf, {"active_card_ids": cards}).status_code == 200
    other = User(username="other-active-card-user", password_hash=user.password_hash, timezone="Europe/Berlin")
    db.add(other)
    db.commit()
    second_client = TestClient(client.app)
    _login(second_client, "other-active-card-user")
    assert second_client.get(PREFERENCES_PATH).json()["active_card_ids"] == DEFAULT_CARDS
    assert client.get(PREFERENCES_PATH).json()["active_card_ids"] == cards


def test_migration_backfill_preserves_each_users_order_and_deduplicates_aliases() -> None:
    from pathlib import Path
    from runpy import run_path

    migration = Path(__file__).parents[1] / "alembic/versions/20261006_0039_active_card_ids.py"
    backfill = run_path(str(migration))["build_active_card_ids"]
    first = backfill(
        "first-user",
        ["activity", "weight", "nutrition", "micronutrients"],
        ["weight_kg", "protein_g", "active_energy_kcal", "protein_g"],
    )
    second = backfill(
        "second-user",
        ["nutrition", "micronutrients", "activity", "weight"],
        ["calcium_mg"],
    )
    assert first == ["activity", "weight", "nutrition", "micronutrients", "protein_g"]
    assert second == ["nutrition", "micronutrients", "activity", "weight", "calcium_mg"]
