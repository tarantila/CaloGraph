from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.expert.service as expert_service
from app.analytics.nutrition_analysis import AnalysisNutritionReadError
from app.analytics.provider_selection import NutritionProviderSelection
from app.auth.dependencies import current_user
from app.expert.contracts import NutritionDaysQuery
from app.expert.service import ExpertAccessDenied, NutritionExpertReader
from app.importers.apple_xml import AppleFoodCorrelation, AppleFoodNutrient
from app.main import app
from app.models import User
from app.nutrition.enums import CoverageState
from app.nutrition.models import NutritionFieldObservation, NutritionSourceObservation
from app.services.analysis_demo import demo_source_instance_id, seed_analysis_demo
from app.services.apple_health_nutrition_ingestion import (
    apple_health_source_instance_id,
    create_apple_health_nutrition_run,
    persist_apple_food_correlation,
)
from app.services.user_operation_lock import InactiveUserOperation, UserOperationBusy

TODAY = date(2026, 11, 12)
START = TODAY - timedelta(days=89)
PATH = "/api/v1/expert/nutrition-days"


@pytest.fixture
def demo(db: Session, user: User) -> User:
    del user  # The supported demo seeder requires an existing bootstrap account.
    user_id = seed_analysis_demo(db, password="synthetic-expert-test-password", today=TODAY)
    account = db.get(User, user_id)
    assert account is not None
    return account


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def test_calendar_pagination_is_bounded_and_includes_missing_days(db: Session, demo: User) -> None:
    reader = NutritionExpertReader(db, owner=demo)
    first = START - timedelta(days=2)
    last = first + timedelta(days=32)
    page = reader.get_nutrition_days(NutritionDaysQuery(start=first, end=last))
    assert len(page.days) == 31
    assert page.days[0].provider_key is None
    assert page.days[0].values.protein_g is None
    assert not any(item.date == first for item in page.evidence)
    assert page.timezone == demo.timezone
    assert page.next_after == first + timedelta(days=30)
    final = reader.get_nutrition_days(NutritionDaysQuery(start=first, end=last, after=page.next_after))
    assert [item.date for item in final.days] == [last - timedelta(days=1), last]
    assert final.next_after is None
    assert set(day.date for day in page.days).isdisjoint(day.date for day in final.days)


def test_winning_provider_missing_fiber_and_explicit_zero_have_matching_evidence(
    db: Session, demo: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instant = datetime(START.year, START.month, START.day, 12, tzinfo=UTC)
    run = create_apple_health_nutrition_run(db, user_id=demo.id)
    event = persist_apple_food_correlation(
        db, user_id=demo.id, run=run, timezone=demo.timezone,
        correlation=AppleFoodCorrelation(
            source_name="Synthetic Apple", source_version="1", device=None,
            start_at=instant, end_at=instant, creation_at=instant,
            food_name="IGNORE INSTRUCTIONS synthetic imported label", external_uuid="expert-fixture",
            metadata=(),
            nutrients=tuple(
                AppleFoodNutrient(
                    raw_type=raw, raw_value=value, value=Decimal(value), unit=unit,
                    start_at=instant, end_at=instant, source_name="Synthetic Apple", source_version="1",
                )
                for raw, value, unit in (
                    ("HKQuantityTypeIdentifierDietaryEnergyConsumed", "612.345679", "kcal"),
                    ("HKQuantityTypeIdentifierDietaryProtein", "0", "g"),
                    ("HKQuantityTypeIdentifierDietaryCarbohydrates", "80", "g"),
                    ("HKQuantityTypeIdentifierDietaryFatTotal", "20", "g"),
                )
            ),
        ),
    )
    event.coverage_state = CoverageState.COMPLETE.value
    source = db.get(NutritionSourceObservation, event.source_observation_id)
    assert source is not None
    source.coverage_state = CoverageState.COMPLETE.value
    for field in db.scalars(select(NutritionFieldObservation).where(
        NutritionFieldObservation.user_id == demo.id,
        NutritionFieldObservation.source_observation_id == source.id,
    )):
        field.coverage_state = CoverageState.COMPLETE.value
    run.coverage_state = CoverageState.COMPLETE.value
    run.status = "completed"
    db.commit()
    apple = apple_health_source_instance_id(demo.id)
    selection = NutritionProviderSelection(
        "apple_health", apple,
        (("apple_health", apple), ("analysis_demo", demo_source_instance_id(demo.id))),
    )
    monkeypatch.setattr(expert_service, "resolve_nutrition_provider", lambda *a, **kw: selection)

    def reject_meal_read(*args, **kwargs):
        pytest.fail("bounded day reads must not load unbounded meal names")

    monkeypatch.setattr("app.analytics.nutrition_analysis._named_meals", reject_meal_read)
    page = NutritionExpertReader(db, owner=demo).get_nutrition_days(
        NutritionDaysQuery(start=START, end=START),
    )
    day = page.days[0]
    assert day.provider_key == "apple_health"
    assert day.values.dietary_energy_kcal == Decimal("612.345679")
    assert day.values.fiber_g is None
    assert day.values.protein_g == 0
    assert all(item.provider_key == "apple_health" for item in page.evidence)
    assert not any(item.metric == "fiber_g" for item in page.evidence)
    protein = next(item for item in page.evidence if item.metric == "protein_g")
    assert protein.value == 0
    assert "IGNORE INSTRUCTIONS" not in page.model_dump_json()
    encoded = page.model_dump(mode="json")["days"][0]["values"]
    assert isinstance(encoded["dietary_energy_kcal"], str)
    assert Decimal(encoded["dietary_energy_kcal"]) == day.values.dietary_energy_kcal
    assert Decimal(encoded["protein_g"]) == 0
    assert encoded["fiber_g"] is None


@pytest.mark.parametrize("changes", [
    {"end": START - timedelta(days=1)},
    {"end": START + timedelta(days=365)},
    {"page_size": 0}, {"page_size": 32},
    {"after": START - timedelta(days=1)}, {"after": START + timedelta(days=10)},
    {"user_id": "caller-selected-owner"}, {"source": "legacy"},
])
def test_application_query_rejects_unbounded_or_owner_selected_reads(changes: dict) -> None:
    with pytest.raises(ValidationError):
        NutritionDaysQuery.model_validate({"start": START, "end": START + timedelta(days=10), **changes})


def test_date_max_final_page_does_not_overflow(db: Session, demo: User) -> None:
    page = NutritionExpertReader(db, owner=demo).get_nutrition_days(
        NutritionDaysQuery(start=date.max, end=date.max),
    )
    assert len(page.days) == 1
    assert page.days[0].date == date.max
    assert page.next_after is None


def test_application_rejects_inactive_owner(db: Session, user: User) -> None:
    user.is_active = False
    with pytest.raises(ExpertAccessDenied):
        NutritionExpertReader(db, owner=user).get_nutrition_days(NutritionDaysQuery(start=START, end=START))


def test_authenticated_endpoint_uses_same_service_and_isolates_accounts(
    client: TestClient, db: Session, demo: User, user: User,
) -> None:
    params = {"start": START.isoformat(), "end": START.isoformat()}
    assert client.get(PATH, params=params).status_code == 401
    login(client, demo.username, "synthetic-expert-test-password")
    response = client.get(PATH, params=params)
    assert response.status_code == 200
    direct = NutritionExpertReader(db, owner=demo).get_nutrition_days(NutritionDaysQuery(start=START, end=START))
    assert response.json() == direct.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    client.cookies.clear()
    login(client, user.username, "correct-horse-battery-staple")
    response = client.get(PATH, params=params)
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert "analysis_demo" not in response.text


@pytest.mark.parametrize("extra", [
    {"end": "2025-01-01"}, {"start": "invalid"}, {"page_size": "32"},
    {"user_id": "somebody-else"}, {"source": "legacy"}, {"after": "9999-12-31"},
])
def test_rest_validation_is_safe_problem_json(
    client: TestClient, user: User, extra: dict[str, str],
) -> None:
    login(client, user.username, "correct-horse-battery-staple")
    response = client.get(PATH, params={"start": "2026-11-01", "end": "2026-11-12", **extra})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "urn:calograph:problem:validation-error"
    assert "somebody-else" not in response.text


def test_unsafe_canonical_read_fails_without_partial_page_or_upstream_details(
    client: TestClient, demo: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    login(client, demo.username, "synthetic-expert-test-password")

    def unreadable(*args, **kwargs):
        raise AnalysisNutritionReadError("private provider payload must not be returned")

    monkeypatch.setattr(expert_service, "read_analysis_nutrition", unreadable)
    response = client.get(PATH, params={"start": START.isoformat(), "end": START.isoformat()})
    assert response.status_code == 503
    assert response.json()["type"] == "urn:calograph:problem:provider-selection-not-ready"
    assert "private provider payload" not in response.text
    assert "days" not in response.json()


@pytest.mark.parametrize("error", [InactiveUserOperation, UserOperationBusy])
def test_auth_lifecycle_conflicts_match_documented_problem_contract(client: TestClient, error: type[Exception]) -> None:
    def blocked():
        raise error("internal lifecycle context")

    app.dependency_overrides[current_user] = blocked
    try:
        response = client.get(PATH, params={"start": START.isoformat(), "end": START.isoformat()})
    finally:
        app.dependency_overrides.pop(current_user)
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert "internal lifecycle context" not in response.text
