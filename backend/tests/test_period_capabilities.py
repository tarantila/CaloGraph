from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.provider_selection import NutritionProviderSelection
from app.api import analytics
from app.main import app
from app.models import HealthSample, ImportBatch, User, UserAchievement
from app.services.analysis_demo import seed_analysis_demo

END = date(2026, 9, 29)
NO_ALL = {"supports_all_data": False, "all_data_start_date": None}


def login(client: TestClient, username: str, password: str) -> None:
    assert client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    ).status_code == 200


@pytest.mark.parametrize(
    "path,max_days",
    [
        ("/analytics/weight", 3661),
        ("/analytics/verification/activity", 3661),
        ("/analytics/nutrition/analysis", 365),
        ("/analytics/nutrition/micronutrients", 365),
    ],
)
def test_window_results_do_not_claim_complete_all_data(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    max_days: int,
) -> None:
    owner_id = seed_analysis_demo(
        db, password="synthetic-analysis-test-password", today=END,
    )
    owner = db.get(User, owner_id)
    assert owner is not None
    monkeypatch.setitem(app.dependency_overrides, analytics.current_user, lambda: owner)
    monkeypatch.setitem(app.dependency_overrides, analytics.get_db, lambda: db)
    with TestClient(app) as http:
        accepted = http.get(
            f"/api/v1{path}",
            params={
                "start": (END - timedelta(days=max_days - 1)).isoformat(),
                "end": END.isoformat(),
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["period_capabilities"] == NO_ALL
        rejected = http.get(
            f"/api/v1{path}",
            params={
                "start": (END - timedelta(days=max_days)).isoformat(),
                "end": END.isoformat(),
            },
        )
        assert rejected.status_code == 422


@pytest.mark.parametrize(
    "path,extra",
    [
        ("/analytics/trends", {}),
        ("/analytics/trends", {"include_incomplete": "true"}),
        ("/analytics/micronutrients", {}),
        ("/analytics/micronutrients", {"source": "yazio_export_v1"}),
        ("/analytics/micronutrients", {"source": "apple_health_xml"}),
    ],
)
def test_legacy_acceptance_does_not_invent_an_earliest_reader_boundary(
    client: TestClient,
    user: User,
    path: str,
    extra: dict[str, str],
) -> None:
    login(client, user.username, "correct-horse-battery-staple")
    response = client.get(
        f"/api/v1{path}",
        params={"start": "2026-09-01", "end": "2026-09-29", **extra},
    )
    assert response.status_code == 200
    assert response.json()["period_capabilities"] == NO_ALL


def add_history(db: Session, owner: User, day: date) -> None:
    batch = ImportBatch(user_id=owner.id, source_type="apple_health_xml", status="completed")
    db.add(batch)
    db.flush()
    at = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    db.add(
        HealthSample(
            user_id=owner.id,
            import_batch_id=batch.id,
            external_sample_id=f"period-{day}",
            fingerprint=f"period-{owner.id}-{day}",
            source_type="apple_health_xml",
            source_identifier="synthetic-period-reader",
            metric_type="dietary_energy_kcal",
            value=Decimal("1800"),
            unit="kcal",
            original_value=Decimal("1800"),
            original_unit="kcal",
            start_at=at,
            end_at=at + timedelta(minutes=1),
            local_date=day,
            timezone="UTC",
        )
    )
    db.commit()


@pytest.mark.parametrize(
    "path,extra",
    [
        ("/analytics/trends", {}),
        ("/analytics/daily", {"source": "apple_health_xml"}),
        ("/analytics/weekdays", {}),
        ("/analytics/micronutrients", {"source": "apple_health_xml"}),
    ],
)
def test_legacy_all_keeps_explicit_achievement_semantics(
    client: TestClient,
    db: Session,
    user: User,
    path: str,
    extra: dict[str, str],
) -> None:
    add_history(db, user, date(2025, 1, 1))
    add_history(db, user, date(2025, 12, 31))
    login(client, user.username, "correct-horse-battery-staple")
    params = {"start": "2025-01-01", "end": "2025-12-31", **extra}
    ordinary = client.get(f"/api/v1{path}", params=params)
    assert ordinary.status_code == 200
    statement = select(UserAchievement).where(
        UserAchievement.user_id == user.id,
        UserAchievement.achievement_key == "the_big_picture",
    )
    assert db.scalars(statement).first() is None
    explicit = client.get(f"/api/v1{path}", params={**params, "period": "all"})
    assert explicit.status_code == 200
    assert db.scalars(statement).first() is not None
    again = client.get(f"/api/v1{path}", params={**params, "period": "all"})
    assert again.status_code == 200
    assert len(list(db.scalars(statement))) == 1


def test_preference_all_gate_stays_reader_specific(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = NutritionProviderSelection(
        provider_key="yazio",
        source_instance_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    monkeypatch.setattr(
        analytics, "_preferred_nutrition_provider", lambda *args, **_kwargs: chosen,
    )
    monkeypatch.setattr(
        analytics, "resolve_nutrition_provider", lambda *args, **kwargs: chosen,
    )
    monkeypatch.setattr(
        analytics, "_read_preferred_daily_points", lambda *args, **kwargs: [],
    )
    login(client, user.username, "correct-horse-battery-staple")
    params = {"start": "2026-09-01", "end": "2026-09-29", "period": "all"}
    for path in ("/analytics/trends", "/analytics/micronutrients"):
        assert client.get(f"/api/v1{path}", params=params).status_code == 422
    for path in ("/analytics/daily", "/analytics/weekdays"):
        assert client.get(f"/api/v1{path}", params=params).status_code == 200
    assert db.scalars(
        select(UserAchievement).where(
            UserAchievement.user_id == user.id,
            UserAchievement.achievement_key == "the_big_picture",
        )
    ).first() is None


def test_analysis_reader_bound_does_not_change_explicit_legacy_route(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login(client, user.username, "correct-horse-battery-staple")
    legacy_reader = analytics.read_legacy_micronutrient_period
    legacy_calls: list[UUID] = []

    def fail_analysis_reader(*_args: object, **_kwargs: object) -> None:
        pytest.fail("legacy route called the Analysis micronutrient reader")

    def track_legacy_reader(*args: object, **kwargs: object) -> object:
        legacy_calls.append(kwargs["user_id"])
        return legacy_reader(*args, **kwargs)

    monkeypatch.setattr(
        analytics, "read_analysis_micronutrients", fail_analysis_reader
    )
    monkeypatch.setattr(
        analytics, "read_legacy_micronutrient_period", track_legacy_reader
    )
    response = client.get(
        "/api/v1/analytics/micronutrients",
        params={
            "source": "yazio_export_v1",
            "start": "2026-09-01",
            "end": "2026-09-29",
        },
    )

    assert response.status_code == 200
    assert response.json()["period_capabilities"] == NO_ALL
    assert legacy_calls == [user.id]
