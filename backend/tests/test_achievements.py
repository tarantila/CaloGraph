from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import HealthSample, ImportBatch, User, UserAchievement
from app.services.achievements import reconcile_achievements


METRICS = {
    "dietary_energy_kcal": "kcal",
    "protein_g": "g",
    "carbohydrates_g": "g",
    "fat_g": "g",
}


def add_day(
    db: Session,
    user: User,
    local_date: date,
    *,
    source_type: str = "apple_health_xml",
    metrics: set[str] | None = None,
) -> None:
    batch = ImportBatch(
        user_id=user.id,
        source_type=source_type,
        client_identifier="achievement-test",
        status="completed",
    )
    db.add(batch)
    db.flush()
    for index, metric_type in enumerate(metrics or set(METRICS)):
        value = Decimal("100")
        db.add(
            HealthSample(
                user_id=user.id,
                import_batch_id=batch.id,
                external_sample_id=f"{source_type}-{local_date}-{metric_type}",
                fingerprint=f"{user.id}-{source_type}-{local_date}-{metric_type}",
                source_type=source_type,
                source_name=source_type,
                source_identifier=source_type,
                metric_type=metric_type,
                value=value,
                unit=METRICS[metric_type],
                original_value=value,
                original_unit=METRICS[metric_type],
                start_at=datetime.combine(local_date, datetime.min.time(), tzinfo=UTC)
                + timedelta(minutes=index),
                end_at=datetime.combine(local_date, datetime.min.time(), tzinfo=UTC)
                + timedelta(minutes=index + 1),
                local_date=local_date,
                timezone="Europe/Berlin",
            )
        )
    db.flush()


def status_by_key(statuses):
    return {status.key: status for status in statuses}


def test_reconcile_unlocks_historical_progress_and_is_idempotent(db: Session, user: User) -> None:
    start = date(2026, 1, 1)
    for offset in range(7):
        add_day(db, user, start + timedelta(days=offset))
    db.commit()

    statuses, newly_unlocked = reconcile_achievements(
        db, user, now=datetime(2026, 8, 16, tzinfo=UTC)
    )
    unlocked = {item.key for item in newly_unlocked}
    assert {"first_day", "tracked_7_days", "streak_7_days", "complete_macros_7"} <= unlocked
    assert {item.key for item in statuses if item.unlocked} == unlocked
    assert db.query(UserAchievement).filter_by(user_id=user.id).count() == len(unlocked)

    _, second_unlocks = reconcile_achievements(
        db, user, now=datetime(2026, 8, 16, tzinfo=UTC)
    )
    assert second_unlocks == []
    assert db.query(UserAchievement).filter_by(user_id=user.id).count() == len(unlocked)


def test_reconcile_keeps_users_isolated(db: Session, user: User) -> None:
    other = User(
        username="other",
        password_hash="not-used",
        timezone="Europe/Berlin",
    )
    db.add(other)
    db.flush()
    add_day(db, user, date(2026, 1, 1))
    db.commit()

    statuses, newly_unlocked = reconcile_achievements(
        db, user, now=datetime(2026, 8, 16, tzinfo=UTC)
    )
    assert "first_day" in {item.key for item in newly_unlocked}
    assert not db.query(UserAchievement).filter_by(user_id=other.id).count()
    assert not status_by_key(reconcile_achievements(db, other)[0])["first_day"].unlocked
    assert status_by_key(statuses)["first_day"].unlocked


def test_hidden_achievements_use_reconstructible_facts(db: Session, user: User) -> None:
    today = date(2026, 8, 16)
    add_day(db, user, today - timedelta(days=800))
    add_day(db, user, date(2024, 2, 29), source_type="yazio_export_v1")
    add_day(db, user, today, source_type="yazio_export_v1")
    add_day(db, user, today, source_type="health_auto_export_v2")
    db.commit()

    statuses, newly_unlocked = reconcile_achievements(
        db, user, now=datetime(2026, 8, 16, tzinfo=UTC)
    )
    unlocked = status_by_key(statuses)
    assert {"hidden_leap_day", "hidden_time_machine", "hidden_break_day", "hidden_full_house"} <= {
        item.key for item in newly_unlocked
    }
    assert unlocked["hidden_leap_day"].progress is None
    assert unlocked["hidden_leap_day"].target is None
    assert unlocked["hidden_leap_day"].unlocked
