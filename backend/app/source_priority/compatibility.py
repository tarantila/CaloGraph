from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import UserProviderPreference
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy
from app.source_priority.repositories import list_policies, list_rules


@dataclass(frozen=True, slots=True)
class ProviderPreferenceSnapshot:
    data_area: str
    provider_key: str


def _legacy_table_available(db: Session) -> bool:
    bind = db.get_bind()
    return bool(bind is not None and sa.inspect(bind).has_table("user_provider_preferences"))


def _latest_policy(db: Session, user_id: UUID) -> SourcePriorityPolicy | None:
    policies = list_policies(db, user_id)
    return max(
        policies,
        key=lambda policy: (policy.version, policy.effective_from, str(policy.id)),
        default=None,
    )


def _policy_preferences(
    db: Session,
    user_id: UUID,
    policy: SourcePriorityPolicy,
) -> list[ProviderPreferenceSnapshot]:
    return [
        ProviderPreferenceSnapshot(data_area=rule.data_area, provider_key=rule.provider_key)
        for rule in list_rules(db, user_id, policy.id)
        if rule.metric_key is None
    ]


def _legacy_preferences(db: Session, user_id: UUID) -> list[ProviderPreferenceSnapshot]:
    if not _legacy_table_available(db):
        return []
    try:
        rows = db.scalars(
            sa.select(UserProviderPreference)
            .where(UserProviderPreference.user_id == user_id)
            .order_by(UserProviderPreference.data_area)
        )
        return [
            ProviderPreferenceSnapshot(data_area=row.data_area, provider_key=row.provider_key)
            for row in rows
        ]
    except OperationalError:
        db.rollback()
        return []



def _legacy_delete(db: Session, user_id: UUID, data_area: str) -> None:
    if not _legacy_table_available(db):
        return
    row = db.get(UserProviderPreference, (user_id, data_area))
    if row is not None:
        db.delete(row)



def list_provider_preferences(db: Session, user_id: UUID) -> list[ProviderPreferenceSnapshot]:
    policy = _latest_policy(db, user_id)
    if policy is not None:
        return sorted(_policy_preferences(db, user_id, policy), key=lambda item: item.data_area)
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




def _next_effective_from(
    db: Session,
    user_id: UUID,
    candidate: datetime,
) -> datetime:
    existing = {
        policy.effective_from.astimezone(UTC)
        if policy.effective_from.tzinfo is not None
        else policy.effective_from.replace(tzinfo=UTC)
        for policy in list_policies(db, user_id)
    }
    while candidate in existing:
        candidate += timedelta(microseconds=1)
    return candidate


def set_provider_preference(
    db: Session,
    *,
    user_id: UUID,
    data_area: str,
    provider_key: str,
) -> ProviderPreferenceSnapshot:
    current_policy = _latest_policy(db, user_id)
    current = get_provider_preference(db, user_id, data_area)
    if current_policy is None:
        rules: list[PriorityRuleSpec] = []
        version = 1
    else:
        rules = [
            PriorityRuleSpec(
                data_area=rule.data_area,
                metric_key=rule.metric_key,
                provider_key=rule.provider_key,
                priority_rank=rule.priority_rank,
            )
            for rule in list_rules(db, user_id, current_policy.id)
            if not (rule.data_area == data_area and rule.metric_key is None)
        ]
        version = current_policy.version + 1
    if current is None or current.provider_key != provider_key:
        rules.append(PriorityRuleSpec(data_area, None, provider_key, 1))
        effective_from = _next_effective_from(db, user_id, datetime.now(UTC))
        create_policy_with_rules(
            db,
            user_id,
            version,
            effective_from,
            tuple(rules),
        )
    return ProviderPreferenceSnapshot(data_area=data_area, provider_key=provider_key)


def delete_provider_preference(db: Session, *, user_id: UUID, data_area: str) -> None:
    current_policy = _latest_policy(db, user_id)
    if current_policy is None:
        _legacy_delete(db, user_id, data_area)
        return
    rules = [
        PriorityRuleSpec(
            data_area=rule.data_area,
            metric_key=rule.metric_key,
            provider_key=rule.provider_key,
            priority_rank=rule.priority_rank,
        )
        for rule in list_rules(db, user_id, current_policy.id)
        if not (rule.data_area == data_area and rule.metric_key is None)
    ]
    if len(rules) != len(list_rules(db, user_id, current_policy.id)):
        create_policy_with_rules(
            db,
            user_id,
            current_policy.version + 1,
            _next_effective_from(db, user_id, datetime.now(UTC)),
            tuple(rules),
        )

__all__ = [
    "ProviderPreferenceSnapshot",
    "delete_provider_preference",
    "get_provider_preference",
    "list_provider_preferences",
    "set_provider_preference",
]
