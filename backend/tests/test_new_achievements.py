from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.analytics import _unlock_big_picture_if_requested
from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    User,
    UserAchievement,
    UserUsageDay,
)
from app.services.achievements import reconcile_achievements


def _usage_days(db: Session, user: User, days: list[date]) -> None:
    db.add_all([UserUsageDay(user_id=user.id, activity_date=day) for day in days])
    db.commit()


def _active_sample(db: Session, user: User, day: date, value: str) -> None:
    batch = ImportBatch(
        user_id=user.id,
        source_type="apple_health_xml",
        client_identifier="achievement-test",
        status="completed",
    )
    db.add(batch)
    db.flush()
    amount = Decimal(value)
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=f"activity-{day}-{value}",
            fingerprint=f"activity-{user.id}-{day}-{value}",
            source_type="apple_health_xml",
            source_name="Apple Health",
            source_identifier="activity-test",
            metric_type="active_energy_kcal",
            value=amount,
            unit="kcal",
            original_value=amount,
            original_unit="kcal",
            start_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            end_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=1),
            local_date=day,
            timezone="Europe/Berlin",
        )
    )
    db.commit()

def _nutrition_sample(db: Session, user: User, day: date) -> None:
    batch = ImportBatch(
        user_id=user.id,
        source_type="apple_health_xml",
        client_identifier="achievement-test",
        status="completed",
    )
    db.add(batch)
    db.flush()
    amount = Decimal("1800")
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=f"nutrition-{day}",
            fingerprint=f"nutrition-{user.id}-{day}",
            source_type="apple_health_xml",
            source_name="Apple Health",
            source_identifier="nutrition-test",
            metric_type="dietary_energy_kcal",
            value=amount,
            unit="kcal",
            original_value=amount,
            original_unit="kcal",
            start_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            end_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=1),
            local_date=day,
            timezone="Europe/Berlin",
        )
    )
    db.commit()
def test_big_picture_unlocks_only_from_explicit_all_request(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    _nutrition_sample(db, user, date(2025, 1, 1))
    _nutrition_sample(db, user, date(2025, 12, 31))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200

    response = client.get(
        "/api/v1/analytics/trends?start=2025-01-01&end=2025-12-31&period=all"
    )
    assert response.status_code == 200
    assert db.query(UserAchievement).filter_by(
        user_id=user.id, achievement_key="the_big_picture"
    ).count() == 1


def test_usage_day_persistence_is_timezone_aware_and_idempotent(db: Session, user: User) -> None:
    from app.auth.dependencies import _record_usage_day

    first = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    _record_usage_day(db, user, first)
    _record_usage_day(db, user, first + timedelta(minutes=10))
    db.commit()
    assert db.query(UserUsageDay).filter_by(user_id=user.id).count() == 1
    assert db.query(UserUsageDay).filter_by(user_id=user.id).one().activity_date == date(2026, 1, 2)

    _record_usage_day(db, user, first + timedelta(days=1))
    db.commit()
    assert db.query(UserUsageDay).filter_by(user_id=user.id).count() == 2

def _keys(items) -> set[str]:
    return {item.key for item in items if item.key is not None}


def test_usage_achievements_use_authenticated_days_not_nutrition_days(
    db: Session,
    user: User,
) -> None:
    start = date(2025, 8, 1)
    _usage_days(db, user, [start + timedelta(days=offset) for offset in range(365)])

    _, newly_unlocked = reconcile_achievements(db, user, now=datetime(2026, 8, 1, tzinfo=UTC))

    assert {"one_week_in", "going_steady", "century_club", "long_term_relationship"} <= _keys(newly_unlocked)
    assert db.query(UserAchievement).filter_by(user_id=user.id, achievement_key="long_term_relationship").count() == 1


def test_usage_streak_break_blocks_streak_achievements(db: Session, user: User) -> None:
    start = date(2026, 1, 1)
    _usage_days(db, user, [start + timedelta(days=offset) for offset in range(6)] + [start + timedelta(days=7)])

    _, newly_unlocked = reconcile_achievements(db, user, now=datetime(2026, 8, 1, tzinfo=UTC))

    assert "one_week_in" not in _keys(newly_unlocked)
    assert "going_steady" not in _keys(newly_unlocked)


def test_activity_achievements_require_positive_credited_activity(db: Session, user: User) -> None:
    target = db.query(NutritionTarget).filter_by(user_id=user.id).one()
    target.activity_mode = "full"
    target.activity_source_type = "apple_health_xml"
    db.commit()
    day = date(2026, 8, 1)
    _active_sample(db, user, day, "0")
    _, before = reconcile_achievements(db, user, now=datetime(2026, 8, 1, tzinfo=UTC))
    assert "more_headroom" not in _keys(before)

    for offset in range(7):
        _active_sample(db, user, day + timedelta(days=offset), "100")
    _, after = reconcile_achievements(db, user, now=datetime(2026, 8, 8, tzinfo=UTC))

    assert {"more_headroom", "room_to_move"} <= _keys(after)


def test_change_of_plans_requires_distinct_effective_dates(db: Session, user: User) -> None:
    db.add(
        NutritionTarget(
            user_id=user.id,
            valid_from=date(2025, 1, 1),
            calories_kcal=Decimal("2100"),
            protein_g=Decimal("130"),
        )
    )
    db.commit()

    _, newly_unlocked = reconcile_achievements(db, user, now=datetime(2026, 8, 1, tzinfo=UTC))

    assert "change_of_plans" in _keys(newly_unlocked)


def test_big_picture_requires_explicit_all_and_year_history(db: Session, user: User) -> None:
    _nutrition_sample(db, user, date(2025, 1, 1))
    _nutrition_sample(db, user, date(2025, 12, 31))

    _unlock_big_picture_if_requested(db, user, None)
    assert not db.query(UserAchievement).filter_by(user_id=user.id, achievement_key="the_big_picture").count()

    _unlock_big_picture_if_requested(db, user, "all")
    assert db.query(UserAchievement).filter_by(user_id=user.id, achievement_key="the_big_picture").count() == 1


def test_event_unlock_helper_remains_idempotent(db: Session, user: User) -> None:
    from app.services.achievements import unlock_achievement_keys

    assert unlock_achievement_keys(db, user.id, ("ordered_takeout",)) == {"ordered_takeout"}
    assert unlock_achievement_keys(db, user.id, ("ordered_takeout",)) == set()
    assert db.query(UserAchievement).filter_by(user_id=user.id, achievement_key="ordered_takeout").count() == 1
