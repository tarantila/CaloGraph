from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC
from app.analytics.service import PRIMARY_NUTRITION_METRICS
from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    PasskeyCredential,
    User,
    UserAchievement,
    UserProfile,
    UserTotpCredential,
    UserUsageDay,
)

SUCCESSFUL_IMPORT_STATUSES: Final = ("completed", "completed_with_errors")
SUPPORTED_SOURCE_TYPES: Final = frozenset(
    {
        "apple_health_xml",
        "health_auto_export_v2",
        "calograph_sync_v1",
        "yazio_export_v1",
    }
)
MACRO_METRICS: Final = frozenset({"protein_g", "carbohydrates_g", "fat_g"})
PRIMARY_METRIC_LIST: Final = tuple(sorted(PRIMARY_NUTRITION_METRICS))

@dataclass(frozen=True, slots=True)
class AchievementDefinition:
    key: str
    category: str
    kind: str
    hidden: bool
    sort_order: int
    target: int | None = None
    icon: str = "trophy"

ACHIEVEMENT_DEFINITIONS: Final[tuple[AchievementDefinition, ...]] = (
    AchievementDefinition("first_day", "tracking", "milestone", False, 10, 1),
    AchievementDefinition("tracked_7_days", "tracking", "milestone", False, 20, 7),
    AchievementDefinition("tracked_30_days", "tracking", "milestone", False, 30, 30),
    AchievementDefinition("tracked_100_days", "tracking", "milestone", False, 40, 100),
    AchievementDefinition("tracked_180_days", "tracking", "milestone", False, 50, 180),
    AchievementDefinition("tracked_365_days", "tracking", "milestone", False, 60, 365),
    AchievementDefinition("streak_7_days", "streak", "streak", False, 110, 7),
    AchievementDefinition("streak_30_days", "streak", "streak", False, 120, 30),
    AchievementDefinition("apple_health_first", "sources", "source", False, 210),
    AchievementDefinition("yazio_first", "sources", "source", False, 220),
    AchievementDefinition("multi_source", "sources", "source", False, 230, 2),
    AchievementDefinition("complete_macros_7", "data_quality", "milestone", False, 310, 7),
    AchievementDefinition("complete_macros_30", "data_quality", "milestone", False, 320, 30),
    AchievementDefinition("complete_macros_100", "data_quality", "milestone", False, 330, 100),
    AchievementDefinition("history_365", "tracking", "history", False, 70, 365),
    AchievementDefinition("hidden_leap_day", "hidden", "discovery", True, 410),
    AchievementDefinition("hidden_time_machine", "hidden", "discovery", True, 420),
    AchievementDefinition("hidden_break_day", "hidden", "discovery", True, 430),
    AchievementDefinition("hidden_full_house", "hidden", "discovery", True, 440),
    AchievementDefinition("one_week_in", "usage", "streak", False, 80, 7, "calendar"),
    AchievementDefinition("going_steady", "usage", "streak", False, 90, 30, "flame"),
    AchievementDefinition("century_club", "usage", "milestone", False, 100, 100, "calendar"),
    AchievementDefinition("long_term_relationship", "usage", "milestone", False, 130, 365, "heart"),
    AchievementDefinition("more_headroom", "activity", "discovery", True, 240, icon="activity"),
    AchievementDefinition("room_to_move", "activity", "milestone", False, 250, 7, "activity"),
    AchievementDefinition("double_locked", "security", "security", False, 270, icon="shield"),
    AchievementDefinition("password_what_password", "security", "security", False, 280, icon="fingerprint"),
    AchievementDefinition("change_of_plans", "budget", "milestone", False, 340, 2, "arrows"),
    AchievementDefinition("ordered_takeout", "export", "export", False, 350, icon="download"),
    AchievementDefinition("spreadsheet_ready", "export", "export", False, 360, icon="table"),
    AchievementDefinition("welcome_back", "import", "import", False, 370, icon="upload"),
    AchievementDefinition("the_big_picture", "analytics", "discovery", True, 450, icon="chart"),
    AchievementDefinition("deja_vu", "import", "discovery", True, 460, icon="repeat"),
    AchievementDefinition("make_a_wish", "hidden", "discovery", True, 470, icon="calendar"),
)
ACHIEVEMENT_BY_KEY: Final = {item.key: item for item in ACHIEVEMENT_DEFINITIONS}


@dataclass(frozen=True, slots=True)
class AchievementFacts:
    tracked_dates: tuple[date, ...]
    usage_dates: tuple[date, ...]
    birth_date: date | None
    macro_complete_dates: frozenset[date]
    positive_activity_dates: frozenset[date]
    source_types: frozenset[str]
    successful_import_sources: frozenset[str]
    best_streak: int
    usage_best_streak: int
    historical_span_days: int
    budget_version_count: int
    has_mfa: bool
    has_passkey: bool
    has_portable_import: bool
    has_portable_import_repeat: bool
    has_internal_gap: bool
    has_leap_day: bool
    has_full_house: bool
    local_today: date


@dataclass(frozen=True, slots=True)
class AchievementStatus:
    key: str | None
    category: str
    kind: str | None
    icon: str | None
    hidden: bool
    placeholder: bool
    unlocked: bool
    unlocked_at: datetime | None
    progress: int | None
    target: int | None
    sort_order: int



def _best_streak(days: tuple[date, ...]) -> int:
    if not days:
        return 0
    best = current = 1
    for previous, current_day in pairwise(days):
        if current_day == previous + timedelta(days=1):
            current += 1
        else:
            current = 1
        best = max(best, current)
    return best


def _has_internal_gap(days: tuple[date, ...]) -> bool:
    return any((current - previous).days > 1 for previous, current in pairwise(days))


def load_facts(db: Session, user: User) -> AchievementFacts:
    tracked_dates = tuple(
        db.scalars(
            select(HealthSample.local_date)
            .where(
                HealthSample.user_id == user.id,
                HealthSample.metric_type == "dietary_energy_kcal",
                HealthSample.value > 0,
            )
            .distinct()
            .order_by(HealthSample.local_date)
        )
    )

    usage_dates = tuple(
        db.scalars(
            select(UserUsageDay.activity_date)
            .where(UserUsageDay.user_id == user.id)
            .distinct()
            .order_by(UserUsageDay.activity_date)
        )
    )
    birth_date = db.scalar(
        select(UserProfile.birth_date).where(UserProfile.user_id == user.id)
    )
    targets = list(
        db.scalars(
            select(NutritionTarget)
            .where(NutritionTarget.user_id == user.id)
            .order_by(NutritionTarget.valid_from)
        )
    )
    active_rows = db.execute(
        select(HealthSample.local_date, HealthSample.source_type)
        .where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type == ACTIVE_ENERGY_METRIC,
            HealthSample.value > 0,
        )
        .distinct()
    ).all()
    positive_activity_dates = frozenset(
        local_date
        for local_date, source_type in active_rows
        if any(
            target.valid_from <= local_date
            and (target.valid_to is None or local_date < target.valid_to)
            and target.activity_mode == "full"
            and target.activity_source_type == source_type
            for target in targets
        )
    )

    nutrition_presence = db.execute(
        select(HealthSample.local_date, HealthSample.metric_type, HealthSample.source_type)
        .where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type.in_(PRIMARY_METRIC_LIST),
        )
        .distinct()
    ).all()
    macro_metrics_by_day: dict[date, set[str]] = {}
    full_house_sources_by_day: dict[date, set[str]] = {}
    for local_date, metric_type, source_type in nutrition_presence:
        macro_metrics_by_day.setdefault(local_date, set()).add(metric_type)
        full_house_sources_by_day.setdefault(local_date, set()).add(source_type)
    macro_complete_dates = frozenset(
        local_date
        for local_date, metrics in macro_metrics_by_day.items()
        if MACRO_METRICS.issubset(metrics)
    )
    source_types = frozenset(
        db.scalars(
            select(HealthSample.source_type)
            .where(
                HealthSample.user_id == user.id,
                HealthSample.source_type.in_(SUPPORTED_SOURCE_TYPES),
            )
            .distinct()
        )
    )

    successful_import_sources = frozenset(
        db.scalars(
            select(ImportBatch.source_type)
            .where(
                ImportBatch.user_id == user.id,
                ImportBatch.status.in_(SUCCESSFUL_IMPORT_STATUSES),
                ImportBatch.source_type.in_(("apple_health_xml", "yazio_export_v1")),
            )
            .distinct()
        )
    )
    if "yazio_export_v1" in source_types:
        successful_import_sources = successful_import_sources | {"yazio_export_v1"}

    local_today = datetime.now(ZoneInfo(user.timezone)).date()
    historical_span_days = (
        (tracked_dates[-1] - tracked_dates[0]).days + 1 if tracked_dates else 0
    )
    budget_version_count = int(
        db.scalar(
            select(func.count(NutritionTarget.id)).where(NutritionTarget.user_id == user.id)
        )
        or 0
    )
    has_full_house = any(
        PRIMARY_NUTRITION_METRICS.issubset(macro_metrics_by_day.get(local_date, set()))
        and len(full_house_sources_by_day.get(local_date, set())) >= 2
        for local_date in macro_metrics_by_day
    )
    has_mfa = db.scalar(
        select(UserTotpCredential.user_id).where(
            UserTotpCredential.user_id == user.id,
            UserTotpCredential.enabled_at.is_not(None),
        )
    ) is not None
    has_passkey = db.scalar(
        select(PasskeyCredential.id).where(PasskeyCredential.user_id == user.id).limit(1)
    ) is not None
    has_portable_import = db.scalar(
        select(ImportBatch.id)
        .where(
            ImportBatch.user_id == user.id,
            ImportBatch.source_type == "calograph-data-export",
            ImportBatch.status.in_(SUCCESSFUL_IMPORT_STATUSES),
        )
        .limit(1)
    ) is not None
    has_portable_import_repeat = db.scalar(
        select(ImportBatch.payload_hash)
        .where(
            ImportBatch.user_id == user.id,
            ImportBatch.source_type == "calograph-data-export",
            ImportBatch.status.in_(SUCCESSFUL_IMPORT_STATUSES),
            ImportBatch.payload_hash.is_not(None),
        )
        .group_by(ImportBatch.payload_hash)
        .having(func.count(ImportBatch.id) >= 2)
        .limit(1)
    ) is not None
    return AchievementFacts(
        tracked_dates=tracked_dates,
        usage_dates=usage_dates,
        birth_date=birth_date,
        macro_complete_dates=macro_complete_dates,
        positive_activity_dates=positive_activity_dates,
        source_types=source_types,
        successful_import_sources=successful_import_sources,
        best_streak=_best_streak(tracked_dates),
        usage_best_streak=_best_streak(usage_dates),
        historical_span_days=historical_span_days,
        budget_version_count=budget_version_count,
        has_mfa=has_mfa,
        has_passkey=has_passkey,
        has_portable_import=has_portable_import,
        has_portable_import_repeat=has_portable_import_repeat,
        has_internal_gap=_has_internal_gap(tracked_dates),
        has_leap_day=any(day.month == 2 and day.day == 29 for day in tracked_dates),
        has_full_house=has_full_house,
        local_today=local_today,
    )


def _progress_for(definition: AchievementDefinition, facts: AchievementFacts) -> int | None:
    if definition.hidden:
        return None
    if definition.key in {"one_week_in", "going_steady"}:
        return min(facts.usage_best_streak, definition.target or 0)
    if definition.key in {"century_club", "long_term_relationship"}:
        return min(len(facts.usage_dates), definition.target or 0)
    if definition.key == "room_to_move":
        return min(len(facts.positive_activity_dates), definition.target or 0)
    if definition.key == "change_of_plans":
        return min(facts.budget_version_count, definition.target or 0)
    if definition.key == "first_day" or definition.key.startswith("tracked_"):
        return min(len(facts.tracked_dates), definition.target or 0)
    if definition.key.startswith("streak_"):
        return min(facts.best_streak, definition.target or 0)
    if definition.key == "multi_source":
        return min(len(facts.source_types), definition.target or 0)
    if definition.key.startswith("complete_macros_"):
        return min(len(facts.macro_complete_dates), definition.target or 0)
    if definition.key == "history_365":
        return min(facts.historical_span_days, definition.target or 0)
    return None

def _qualifies(definition: AchievementDefinition, facts: AchievementFacts) -> bool:
    if definition.key == "double_locked":
        return facts.has_mfa
    if definition.key == "password_what_password":
        return facts.has_passkey
    if definition.key == "welcome_back":
        return facts.has_portable_import
    if definition.key == "deja_vu":
        return facts.has_portable_import_repeat
    if definition.key in {"one_week_in", "going_steady"}:
        return facts.usage_best_streak >= (definition.target or 0)
    if definition.key in {"century_club", "long_term_relationship"}:
        return len(facts.usage_dates) >= (definition.target or 0)
    if definition.key == "more_headroom":
        return bool(facts.positive_activity_dates)
    if definition.key == "room_to_move":
        return len(facts.positive_activity_dates) >= (definition.target or 0)
    if definition.key == "change_of_plans":
        return facts.budget_version_count >= (definition.target or 0)
    if definition.key == "first_day" or definition.key.startswith("tracked_"):
        return len(facts.tracked_dates) >= (definition.target or 0)
    if definition.key.startswith("streak_"):
        return facts.best_streak >= (definition.target or 0)
    if definition.key == "apple_health_first":
        return "apple_health_xml" in facts.successful_import_sources
    if definition.key == "yazio_first":
        return "yazio_export_v1" in facts.successful_import_sources
    if definition.key == "multi_source":
        return len(facts.source_types) >= (definition.target or 0)
    if definition.key.startswith("complete_macros_"):
        return len(facts.macro_complete_dates) >= (definition.target or 0)
    if definition.key == "history_365":
        return facts.historical_span_days >= (definition.target or 0)
    if definition.key == "make_a_wish":
        return (
            facts.birth_date is not None
            and any(
                day.month == facts.birth_date.month and day.day == facts.birth_date.day
                for day in facts.usage_dates
            )
        )
    if definition.key == "hidden_leap_day":
        return facts.has_leap_day
    if definition.key == "hidden_time_machine":
        return any((facts.local_today - day).days >= 730 for day in facts.tracked_dates)
    if definition.key == "hidden_break_day":
        return facts.has_internal_gap
    if definition.key == "hidden_full_house":
        return facts.has_full_house
    return False


def _status_items(
    db: Session,
    user: User,
    facts: AchievementFacts,
) -> list[AchievementStatus]:
    unlocked_at_by_key = {
        row.achievement_key: row.unlocked_at
        for row in db.scalars(
            select(UserAchievement).where(UserAchievement.user_id == user.id)
        )
    }
    statuses: list[AchievementStatus] = []
    for definition in ACHIEVEMENT_DEFINITIONS:
        unlocked_at = unlocked_at_by_key.get(definition.key)
        unlocked = unlocked_at is not None
        if definition.hidden and not unlocked:
            statuses.append(
                AchievementStatus(
                    key=None,
                    category="hidden",
                    kind=None,
                    icon=None,
                    hidden=True,
                    placeholder=True,
                    unlocked=False,
                    unlocked_at=None,
                    progress=None,
                    target=None,
                    sort_order=definition.sort_order,
                )
            )
            continue
        statuses.append(
            AchievementStatus(
                key=definition.key,
                category=definition.category,
                kind=definition.kind,
                icon=definition.icon,
                hidden=definition.hidden,
                placeholder=False,
                unlocked=unlocked,
                unlocked_at=unlocked_at,
                progress=_progress_for(definition, facts) if not unlocked else None,
                target=definition.target if not definition.hidden else None,
                sort_order=definition.sort_order,
            )
        )
    return statuses


def list_achievements(db: Session, user: User) -> list[AchievementStatus]:
    return _status_items(db, user, load_facts(db, user))


def _insert_unlocked(
    db: Session,
    user_id: UUID,
    keys: list[str],
    unlocked_at: datetime,
) -> set[str]:
    if not keys:
        return set()
    values = [
        {"user_id": user_id, "achievement_key": key, "unlocked_at": unlocked_at}
        for key in keys
    ]
    table: Any = UserAchievement.__table__
    dialect = db.get_bind().dialect.name
    statement: Any
    if dialect == "postgresql":
        statement = postgres_insert(table).values(values)
    elif dialect == "sqlite":
        statement = sqlite_insert(table).values(values)
    else:
        raise RuntimeError(f"Unsupported database dialect for achievements: {dialect}")
    statement = statement.on_conflict_do_nothing(
        index_elements=[table.c.user_id, table.c.achievement_key]
    ).returning(table.c.achievement_key)
    return set(db.scalars(statement))

def unlock_achievement_keys(
    db: Session,
    user_id: UUID,
    keys: list[str] | tuple[str, ...],
    *,
    unlocked_at: datetime | None = None,
) -> set[str]:
    eligible = [key for key in keys if key in ACHIEVEMENT_BY_KEY]
    inserted = _insert_unlocked(db, user_id, eligible, unlocked_at or datetime.now(UTC))
    if inserted:
        db.commit()
    return inserted


def reconcile_achievements(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
) -> tuple[list[AchievementStatus], list[AchievementStatus]]:
    facts = load_facts(db, user)
    eligible_keys = [
        definition.key
        for definition in ACHIEVEMENT_DEFINITIONS
        if _qualifies(definition, facts)
    ]
    existing_keys = set(
        db.scalars(
            select(UserAchievement.achievement_key).where(UserAchievement.user_id == user.id)
        )
    )
    inserted_keys = _insert_unlocked(
        db,
        user.id,
        [key for key in eligible_keys if key not in existing_keys],
        now or datetime.now(UTC),
    )
    db.commit()
    statuses = _status_items(db, user, facts)
    newly_unlocked = [status for status in statuses if status.key in inserted_keys]
    return statuses, newly_unlocked
