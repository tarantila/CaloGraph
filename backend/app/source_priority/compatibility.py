from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import User, UserProviderPreference, UserProviderPriority
from app.provider_preferences import canonicalize_provider_key


@dataclass(frozen=True, slots=True)
class ProviderPreferenceSnapshot:
    data_area: str
    provider_key: str
def _canonicalize_preferences(
    rows: Sequence[ProviderPreferenceSnapshot],
) -> list[ProviderPreferenceSnapshot]:
    result: list[ProviderPreferenceSnapshot] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        provider_key = canonicalize_provider_key(row.data_area, row.provider_key)
        identity = (row.data_area, provider_key)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            ProviderPreferenceSnapshot(
                data_area=row.data_area,
                provider_key=provider_key,
            )
        )
    return result




def _table_available(db: Session, table_name: str) -> bool:
    return sa.inspect(db.connection()).has_table(table_name)


def _lock_user_row(db: Session, user_id: UUID) -> None:
    db.scalar(sa.select(User.id).where(User.id == user_id).with_for_update())


def _generic_preferences(db: Session, user_id: UUID) -> list[ProviderPreferenceSnapshot]:
    rows = db.scalars(
        sa.select(UserProviderPriority)
        .where(UserProviderPriority.user_id == user_id)
        .order_by(
            UserProviderPriority.data_area,
            UserProviderPriority.priority,
            UserProviderPriority.provider_key,
        )
    )
    return _canonicalize_preferences(
        [
            ProviderPreferenceSnapshot(data_area=row.data_area, provider_key=row.provider_key)
            for row in rows
        ]
    )


def _legacy_preferences(db: Session, user_id: UUID) -> list[ProviderPreferenceSnapshot]:
    if not _table_available(db, "user_provider_preferences"):
        return []
    rows = db.scalars(
        sa.select(UserProviderPreference)
        .where(UserProviderPreference.user_id == user_id)
        .order_by(UserProviderPreference.data_area)
    )
    return _canonicalize_preferences(
        [
            ProviderPreferenceSnapshot(data_area=row.data_area, provider_key=row.provider_key)
            for row in rows
        ]
    )


def list_provider_preferences(db: Session, user_id: UUID) -> list[ProviderPreferenceSnapshot]:
    if _table_available(db, "user_provider_priorities"):
        preferences = _generic_preferences(db, user_id)
        if preferences:
            return preferences
    return _legacy_preferences(db, user_id)


def get_provider_preference(
    db: Session,
    user_id: UUID,
    data_area: str,
) -> ProviderPreferenceSnapshot | None:
    return next(
        (item for item in list_provider_preferences(db, user_id) if item.data_area == data_area),
        None,
    )


def replace_provider_preferences(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    provider_keys: Sequence[str],
) -> list[ProviderPreferenceSnapshot]:
    _lock_user_row(db, user_id)
    normalized_keys = tuple(provider_keys)
    if not normalized_keys:
        raise ValueError("provider list must not be empty")
    if len(normalized_keys) != len(set(normalized_keys)):
        raise ValueError("provider list must not contain duplicates")
    if not _table_available(db, "user_provider_priorities"):
        raise RuntimeError("user provider priority table is not migrated")

    current = [
        item.provider_key
        for item in _generic_preferences(db, user_id)
        if item.data_area == data_area
    ]
    if current == list(normalized_keys):
        return [
            ProviderPreferenceSnapshot(data_area=data_area, provider_key=provider_key)
            for provider_key in normalized_keys
        ]

    db.execute(
        sa.delete(UserProviderPriority).where(
            UserProviderPriority.user_id == user_id,
            UserProviderPriority.data_area == data_area,
        )
    )
    now = datetime.now(UTC)
    db.add_all(
        UserProviderPriority(
            user_id=user_id,
            data_area=data_area,
            provider_key=provider_key,
            priority=priority,
            created_at=now,
            updated_at=now,
        )
        for priority, provider_key in enumerate(normalized_keys, start=1)
    )
    return [
        ProviderPreferenceSnapshot(data_area=data_area, provider_key=provider_key)
        for provider_key in normalized_keys
    ]


def set_provider_preference(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    provider_key: str,
) -> ProviderPreferenceSnapshot:
    return replace_provider_preferences(
        db,
        user_id=user_id,
        data_area=data_area,
        provider_keys=(provider_key,),
    )[0]


def delete_provider_preference(db: Session, *, user_id: UUID, data_area: str) -> None:
    _lock_user_row(db, user_id)
    if _table_available(db, "user_provider_priorities"):
        db.execute(
            sa.delete(UserProviderPriority).where(
                UserProviderPriority.user_id == user_id,
                UserProviderPriority.data_area == data_area,
            )
        )
    if _table_available(db, "user_provider_preferences"):
        db.execute(
            sa.delete(UserProviderPreference).where(
                UserProviderPreference.user_id == user_id,
                UserProviderPreference.data_area == data_area,
            )
        )


__all__ = [
    "ProviderPreferenceSnapshot",
    "delete_provider_preference",
    "get_provider_preference",
    "list_provider_preferences",
    "replace_provider_preferences",
    "set_provider_preference",
]
