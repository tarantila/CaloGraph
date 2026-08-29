from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import settings as settings_api
from app.models import User, UserProfile

PASSWORD = "correct-horse-battery-staple"
PROFILE_FIELDS = {
    "display_name",
    "gender",
    "birth_date",
    "height_cm",
    "diet_type",
    "health_notes",
    "intolerances",
}


def _login(client: TestClient, username: str = "admin") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _put_profile(client: TestClient, csrf: str, payload: dict[str, object]):
    return client.put(
        "/api/v1/settings/personal-profile",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )


def test_personal_profile_requires_authentication_and_csrf(
    client: TestClient,
    user: User,
) -> None:
    del user
    assert client.get("/api/v1/settings/personal-profile").status_code == 401
    assert client.put("/api/v1/settings/personal-profile", json={}).status_code == 401

    _login(client)
    assert client.put("/api/v1/settings/personal-profile", json={}).status_code == 403


def test_personal_profile_get_without_row_does_not_create_one(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    _login(client)

    response = client.get("/api/v1/settings/personal-profile")

    assert response.status_code == 200
    assert response.json() == {field: None for field in PROFILE_FIELDS}
    assert db.get(UserProfile, user.id) is None


def test_personal_profile_put_trims_and_persists_every_field(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    csrf = _login(client)

    response = _put_profile(
        client,
        csrf,
        {
            "display_name": "  Ada Lovelace  ",
            "gender": "female",
            "birth_date": "1815-12-10",
            "height_cm": "165.50",
            "diet_type": "vegetarian",
            "health_notes": "  Freiwilliger Hinweis  ",
            "intolerances": "  Laktose  ",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Ada Lovelace"
    assert body["gender"] == "female"
    assert body["birth_date"] == "1815-12-10"
    assert Decimal(str(body["height_cm"])) == Decimal("165.50")
    assert body["diet_type"] == "vegetarian"
    assert body["health_notes"] == "Freiwilliger Hinweis"
    assert body["intolerances"] == "Laktose"

    db.expire_all()
    stored = db.get(UserProfile, user.id)
    assert stored is not None
    assert stored.display_name == "Ada Lovelace"
    assert stored.health_notes == "Freiwilliger Hinweis"
    assert stored.created_at is not None
    assert stored.updated_at is not None


def test_personal_profile_put_is_full_replacement_and_can_clear_every_field(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    csrf = _login(client)
    first = _put_profile(
        client,
        csrf,
        {
            "display_name": "First",
            "gender": "other",
            "birth_date": "2000-01-01",
            "height_cm": 180,
            "diet_type": "vegan",
            "health_notes": "note",
            "intolerances": "item",
        },
    )
    assert first.status_code == 200

    replaced = _put_profile(client, csrf, {"display_name": "  Second  "})
    assert replaced.status_code == 200
    assert replaced.json() == {
        **{field: None for field in PROFILE_FIELDS},
        "display_name": "Second",
    }

    cleared = _put_profile(
        client,
        csrf,
        {
            "display_name": "  ",
            "health_notes": "\n\t",
            "intolerances": " ",
        },
    )
    assert cleared.status_code == 200
    assert cleared.json() == {field: None for field in PROFILE_FIELDS}
    db.expire_all()
    stored = db.get(UserProfile, user.id)
    assert stored is not None
    assert all(getattr(stored, field) is None for field in PROFILE_FIELDS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gender", "female"),
        ("gender", "male"),
        ("gender", "non_binary"),
        ("gender", "other"),
        ("gender", "prefer_not_to_say"),
        ("diet_type", "no_special_diet"),
        ("diet_type", "vegetarian"),
        ("diet_type", "vegan"),
        ("diet_type", "pescetarian"),
        ("diet_type", "other"),
        ("diet_type", "prefer_not_to_say"),
    ],
)
def test_personal_profile_accepts_defined_enum_values(
    client: TestClient,
    user: User,
    field: str,
    value: str,
) -> None:
    del user
    csrf = _login(client)

    response = _put_profile(client, csrf, {field: value})

    assert response.status_code == 200
    assert response.json()[field] == value


@pytest.mark.parametrize("field", ["gender", "diet_type"])
def test_personal_profile_rejects_unknown_enum_without_echoing_it(
    client: TestClient,
    user: User,
    field: str,
) -> None:
    del user
    csrf = _login(client)
    private_value = "PRIVATE-invalid-profile-enum"

    response = _put_profile(client, csrf, {field: private_value})

    assert response.status_code == 422
    assert response.json()["type"] == "urn:calograph:problem:validation-error"
    assert private_value not in response.text


def test_personal_profile_rejects_unknown_fields(
    client: TestClient,
    user: User,
) -> None:
    del user
    csrf = _login(client)

    response = _put_profile(client, csrf, {"unknown_profile_field": "PRIVATE-value"})

    assert response.status_code == 422
    assert response.json()["type"] == "urn:calograph:problem:validation-error"
    assert "PRIVATE-value" not in response.text


def test_personal_profile_accepts_today_and_past_but_rejects_future_birth_date(
    client: TestClient,
    user: User,
) -> None:
    csrf = _login(client)
    local_today = datetime.now(ZoneInfo(user.timezone)).date()

    for accepted in (local_today, date(1900, 1, 1)):
        response = _put_profile(client, csrf, {"birth_date": accepted.isoformat()})
        assert response.status_code == 200
        assert response.json()["birth_date"] == accepted.isoformat()

    future = local_today + timedelta(days=1)
    rejected = _put_profile(client, csrf, {"birth_date": future.isoformat()})
    assert rejected.status_code == 422
    assert rejected.json()["type"] == "urn:calograph:problem:validation-error"
    assert future.isoformat() not in rejected.text


@pytest.mark.parametrize("height", ["0.01", "180.25", "300.00"])
def test_personal_profile_accepts_valid_height(
    client: TestClient,
    user: User,
    height: str,
) -> None:
    del user
    csrf = _login(client)

    response = _put_profile(client, csrf, {"height_cm": height})

    assert response.status_code == 200
    assert Decimal(str(response.json()["height_cm"])) == Decimal(height)


@pytest.mark.parametrize("height", [0, -1, "300.01", "180.123"])
def test_personal_profile_rejects_invalid_height(
    client: TestClient,
    user: User,
    height: object,
) -> None:
    del user
    csrf = _login(client)

    response = _put_profile(client, csrf, {"height_cm": height})

    assert response.status_code == 422
    assert response.json()["type"] == "urn:calograph:problem:validation-error"
    assert response.json()["detail"] == "Eingabedaten sind ungültig."


@pytest.mark.parametrize(
    ("field", "maximum"),
    [("display_name", 120), ("health_notes", 4000), ("intolerances", 2000)],
)
def test_personal_profile_enforces_text_limits_after_trimming(
    client: TestClient,
    user: User,
    field: str,
    maximum: int,
) -> None:
    del user
    csrf = _login(client)
    accepted = _put_profile(client, csrf, {field: f"  {'x' * maximum}  "})
    assert accepted.status_code == 200
    assert accepted.json()[field] == "x" * maximum

    rejected = _put_profile(client, csrf, {field: "y" * (maximum + 1)})
    assert rejected.status_code == 422
    assert "y" * 20 not in rejected.text


def test_personal_profile_is_current_user_only_and_absent_from_general_user_response(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    user.is_admin = True
    other = User(
        username="other-profile-owner",
        password_hash=user.password_hash,
        timezone="Europe/Berlin",
    )
    db.add(other)
    db.flush()
    db.add(
        UserProfile(
            user_id=other.id,
            display_name="PRIVATE-other-display-name",
            health_notes="PRIVATE-other-health-notes",
        )
    )
    db.commit()
    _login(client)

    own_profile = client.get("/api/v1/settings/personal-profile")
    current_user = client.get("/api/v1/auth/me")
    nonexistent_admin_route = client.get(f"/api/v1/users/{other.id}/personal-profile")

    assert own_profile.status_code == 200
    assert own_profile.json() == {field: None for field in PROFILE_FIELDS}
    assert current_user.status_code == 200
    assert PROFILE_FIELDS.isdisjoint(current_user.json())
    assert "PRIVATE-other" not in current_user.text
    assert nonexistent_admin_route.status_code == 404


def test_personal_profile_values_do_not_enter_controlled_errors_or_security_events(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del user
    csrf = _login(client)
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        settings_api,
        "log_security_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    private_value = "PRIVATE-sensitive-health-note"

    response = _put_profile(
        client,
        csrf,
        {"health_notes": private_value * 500},
    )

    assert response.status_code == 422
    assert private_value not in response.text
    assert events == []


def test_preferred_weight_unit_is_readable_updatable_and_validated(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    csrf = _login(client)
    initial = client.get("/api/v1/settings/profile")
    assert initial.status_code == 200
    assert initial.json()["preferred_weight_unit"] == "kg"

    updated = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRF-Token": csrf},
        json={"preferred_weight_unit": "lb"},
    )
    assert updated.status_code == 200
    assert updated.json()["preferred_weight_unit"] == "lb"
    db.refresh(user)
    assert user.preferred_weight_unit == "lb"

    rejected = client.put(
        "/api/v1/settings/profile",
        headers={"X-CSRF-Token": csrf},
        json={"preferred_weight_unit": "stone"},
    )
    assert rejected.status_code == 422
    assert "stone" not in rejected.text
