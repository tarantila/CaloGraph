from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.analytics.provider_selection import NutritionProviderSelection
from app.expert.context import (
    ContextBuilder,
    ExpertContextProblem,
    _aggregate_history,
    _chunks,
    _Target,
)
from app.expert.context_contracts import ProviderCoverage
from app.expert.preferences import DEFAULT_DATA_DOMAINS, ExpertPreferencesService
from app.expert.preferences_contracts import ExpertPreferencesPut, ExpertPreferencesView
from app.micronutrients import MICRONUTRIENTS
from app.models import User
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import NutritionIngestionRun, NutritionSourceObservation
from app.services.analysis_demo import demo_source_instance_id
from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id
from app.services.user_operation_lock import InactiveUserOperation
from tests.test_ai_connections import login

PATH = "/api/v1/settings/expert"


def test_preference_contract_canonicalizes_and_rejects_invalid_scope() -> None:
    value = ExpertPreferencesPut.model_validate({
        "data_domains": ["weight", "nutrition"], "include_meal_names": True,
    })
    assert value.data_domains == ("nutrition", "weight")
    for invalid in (
        {"data_domains": [], "include_meal_names": False},
        {"data_domains": ["nutrition", "nutrition"], "include_meal_names": False},
        {"data_domains": ["unknown"], "include_meal_names": False},
        {"data_domains": ["profile"], "include_meal_names": True},
        {"data_domains": ["nutrition"], "include_meal_names": None},
        {"data_domains": ["nutrition"], "include_meal_names": False, "user_id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            ExpertPreferencesPut.model_validate(invalid)


def test_defaults_account_isolation_and_meaningful_versioning(db: Session, user: User) -> None:
    other = User(username="expert-preferences-admin", password_hash="synthetic-only", is_admin=True)
    db.add(other)
    db.commit()
    service = ExpertPreferencesService(db, owner=user)
    initial = service.get()
    assert initial.data_domains == DEFAULT_DATA_DOMAINS
    assert initial.include_meal_names is True and initial.configuration_version == 1
    same = service.put(ExpertPreferencesPut(
        data_domains=DEFAULT_DATA_DOMAINS, include_meal_names=True,
    ))
    assert same == initial
    changed = service.put(ExpertPreferencesPut(
        data_domains=("nutrition", "targets"), include_meal_names=False,
    ))
    assert changed.configuration_version == 2
    assert service.put(ExpertPreferencesPut(
        data_domains=("nutrition", "targets"), include_meal_names=False,
    )) == changed
    other_service = ExpertPreferencesService(db, owner=other)
    assert other_service.get().configuration_version == 1
    other_changed = other_service.put(ExpertPreferencesPut(
        data_domains=("weight",), include_meal_names=False,
    ))
    assert other_changed.data_domains == ("weight",)
    assert service.get() == changed


def test_inactive_account_cannot_read_or_update_preferences(db: Session, user: User) -> None:
    user.is_active = False
    user.deactivated_at = datetime.now(UTC)
    db.commit()
    service = ExpertPreferencesService(db, owner=user)
    with pytest.raises(InactiveUserOperation):
        service.get()
    with pytest.raises(ConnectionProblem):
        service.put(ExpertPreferencesPut(data_domains=("nutrition",), include_meal_names=False))


def test_preferences_api_auth_csrf_and_owner_scope(client: TestClient, user: User) -> None:
    assert client.get(PATH).status_code == 401
    csrf = login(client)
    initial = client.get(PATH)
    assert initial.status_code == 200
    assert initial.json() == {
        "data_domains": list(DEFAULT_DATA_DOMAINS),
        "include_meal_names": True,
        "configuration_version": 1,
    }
    body = {"data_domains": ["nutrition", "weight"], "include_meal_names": True}
    assert client.put(PATH, json=body).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    changed = client.put(PATH, headers=headers, json=body)
    assert changed.status_code == 200
    assert changed.json()["configuration_version"] == 2
    assert changed.json()["data_domains"] == ["nutrition", "weight"]
    assert client.put(PATH, headers=headers, json=body).json()["configuration_version"] == 2
    assert client.put(PATH, headers=headers, json={**body, "owner_id": str(user.id)}).status_code == 422


def test_full_history_chunks_do_not_truncate_and_zero_differs_from_missing() -> None:
    start = date(2023, 1, 1)
    end = start + timedelta(days=900)
    chunks = tuple(_chunks(start, end))
    assert chunks[0][0] == start and chunks[-1][1] == end
    assert all(1 <= (right - left).days + 1 <= 365 for left, right in chunks)

    rows = {
        start: ("first", {"dietary_energy_kcal": Decimal("0"), "protein_g": None}, ()),
        start + timedelta(days=1): (None, {"dietary_energy_kcal": None, "protein_g": None}, ()),
    }
    aggregate = _aggregate_history(rows, start, start + timedelta(days=1), [])
    assert len(aggregate) == 1
    calories, protein = aggregate[0].metrics[:2]
    assert (calories.count, calories.total, calories.mean) == (1, Decimal("0"), Decimal("0"))
    assert (protein.count, protein.total, protein.mean) == (0, None, None)
    with pytest.raises(ExpertContextProblem, match="history_exceeds_supported_span"):
        ContextBuilder._check_span(date(1900, 1, 1), date(2026, 1, 1))
    target = _Target(
        valid_from=start, valid_to=None, calories_kcal=Decimal("2000"),
        maintenance_kcal=None, protein_g=Decimal("100"), carbs_g=None,
        fat_g=None, fiber_g=None,
    )
    historical_comparison = _aggregate_history(
        rows, start, start + timedelta(days=1), [target],
    )[0]
    assert historical_comparison.target_days == 2
    assert historical_comparison.target_calories_kcal == Decimal("4000")
    assert historical_comparison.calories_vs_target_kcal == Decimal("-2000")


def test_context_builder_skips_disabled_categories(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.expert.context import ContextBuilder

    builder = ContextBuilder(db, user)
    monkeypatch.setattr(builder, "_nutrition_history", lambda *a, **k: pytest.fail("nutrition switch off"))
    monkeypatch.setattr(builder, "_target_history", lambda *a, **k: pytest.fail("target switch off"))
    monkeypatch.setattr(builder, "_weight_history", lambda *a, **k: pytest.fail("weight switch off"))
    monkeypatch.setattr(builder, "_activity_history", lambda *a, **k: pytest.fail("activity switch off"))
    seen: list[tuple[bool, bool]] = []

    def read_enabled_profile_fields(*, include_profile: bool, include_health_context: bool):
        seen.append((include_profile, include_health_context))
        return {"health_notes": "synthetic note", "intolerances": "synthetic intolerance"}

    monkeypatch.setattr(builder, "_profile", read_enabled_profile_fields)
    preferences = ExpertPreferencesView(
        data_domains=("health_context",), include_meal_names=False, configuration_version=1,
    )
    result = builder.build(preferences)
    assert result.nutrition_status == "disabled"
    assert result.targets_status == "disabled"
    assert result.weight_status == "disabled"
    assert result.activity_status == "disabled"
    assert result.profile is None
    assert result.health_context is not None
    assert result.health_context.notes == "synthetic note"
    assert seen == [(False, True)]


def test_targets_enabled_does_not_read_activity_or_expose_activity_budget(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.expert.context import ContextBuilder

    builder = ContextBuilder(db, user)
    monkeypatch.setattr(builder, "_nutrition_history", lambda *a, **k: pytest.fail("nutrition switch off"))
    monkeypatch.setattr(builder, "_weight_history", lambda *a, **k: pytest.fail("weight switch off"))
    monkeypatch.setattr(builder, "_activity_history", lambda *a, **k: pytest.fail("activity switch off"))
    monkeypatch.setattr(builder, "_profile", lambda **k: pytest.fail("profile switches off"))
    result = builder.build(ExpertPreferencesView(
        data_domains=("targets",), include_meal_names=False, configuration_version=1,
    ))
    assert result.targets_status == "available"
    assert result.activity_status == "disabled"
    assert result.activity_daily == () and result.activity_aggregates == ()
    assert result.target_periods[0].calories_kcal == Decimal("2000.000")
    assert result.target_comparison_basis == "base_target_without_activity_credit"
    assert any("not effective budget" in item for item in result.limitations)


def test_context_reads_full_selected_fallback_history_in_bounded_chunks(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.expert.context as context_module

    today = date.today()
    earliest = today - timedelta(days=800)
    apple_source = apple_health_source_instance_id(user.id)
    fallback_source = demo_source_instance_id(user.id)
    selected = NutritionProviderSelection(
        "apple_health", apple_source,
        (("apple_health", apple_source), ("analysis_demo", fallback_source)),
    )
    for provider, source, local_day in (
        ("analysis_demo", fallback_source, earliest),
        ("apple_health", apple_source, today - timedelta(days=2)),
        ("yazio", uuid4(), date(2010, 1, 1)),  # not in the selected source chain
    ):
        run = NutritionIngestionRun(
            user_id=user.id, provider_key=provider, source_instance_id=source,
            status="completed", coverage_state=CoverageState.COMPLETE.value,
        )
        db.add(run)
        db.flush()
        db.add(NutritionSourceObservation(
            user_id=user.id, ingestion_run_id=run.id, provider_key=provider,
            source_instance_id=source, observation_kind=ObservationKind.DAILY_SUMMARY.value,
            source_namespace="synthetic", source_revision=1, observation_fingerprint=uuid4().hex * 2,
            local_date=local_day, presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        ))
    db.commit()
    monkeypatch.setattr(context_module, "resolve_nutrition_provider", lambda *a, **k: selected)
    nutrition_calls: list[tuple[date, date, bool]] = []

    def fake_nutrition(_db, *, start: date, end: date, include_meals: bool, **kwargs):
        nutrition_calls.append((start, end, include_meals))
        values = {
            "dietary_energy_kcal": Decimal("0") if start <= earliest <= end else None,
            "protein_g": Decimal("0") if start <= earliest <= end else None,
            "carbohydrates_g": None, "fat_g": None, "fiber_g": None,
            "sugar_g": None, "saturated_fat_g": None,
        }
        return SimpleNamespace(days=[
            SimpleNamespace(
                date=start + timedelta(days=offset),
                provider_key=("analysis_demo" if start + timedelta(days=offset) == earliest else None),
                meals=[],
                **(values if start + timedelta(days=offset) == earliest else {key: None for key in values}),
            )
            for offset in range((end - start).days + 1)
        ])

    monkeypatch.setattr(context_module, "read_analysis_nutrition", fake_nutrition)
    monkeypatch.setattr(context_module, "read_analysis_micronutrients", lambda _db, **kwargs: SimpleNamespace(
        metrics=[SimpleNamespace(metric_key=item.metric_type, total=None, days_with_data=0)
                 for item in MICRONUTRIENTS],
    ))
    result = ContextBuilder(db, user).build(ExpertPreferencesView(
        data_domains=("nutrition",), include_meal_names=False, configuration_version=1,
    ))
    history_calls = [(start, end) for start, end, meals in nutrition_calls if not meals]
    assert result.coverage_start == earliest
    assert result.coverage_end == today
    assert result.coverage_complete is True
    assert len(history_calls) == 3
    assert all((end - start).days + 1 <= 365 for start, end in history_calls)
    assert result.provider_coverage == (ProviderCoverage(provider_key="analysis_demo", days_with_data=1),)
    old_period = next(item for item in result.aggregates if item.start <= earliest <= item.end)
    assert old_period.metrics[0].total == Decimal("0")
    assert old_period.metrics[0].count == 1
    assert old_period.metrics[1].total == Decimal("0")
