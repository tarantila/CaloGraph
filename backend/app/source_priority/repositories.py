from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.source_priority.contracts import (
    UNSET,
    validate_aware_datetime,
    validate_non_empty,
    validate_positive,
)
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule


def create_policy(
    db: Session,
    user_id: UUID,
    version: int,
    effective_from: datetime,
) -> SourcePriorityPolicy:
    validate_positive(version, "version")
    effective_from = validate_aware_datetime(effective_from)
    policy = SourcePriorityPolicy(
        user_id=user_id,
        version=version,
        effective_from=effective_from,
    )
    db.add(policy)
    db.flush()
    return policy


def add_rule(
    db: Session,
    user_id: UUID,
    policy_id: UUID,
    data_area: str,
    metric_key: str | None,
    provider_key: str,
    priority_rank: int,
) -> SourcePriorityRule:
    validate_non_empty(data_area, "data_area")
    if metric_key is not None:
        validate_non_empty(metric_key, "metric_key")
    validate_non_empty(provider_key, "provider_key")
    validate_positive(priority_rank, "priority_rank")
    if get_policy(db, user_id, policy_id) is None:
        raise ValueError("policy must belong to the same user")
    rule = SourcePriorityRule(
        user_id=user_id,
        policy_id=policy_id,
        data_area=data_area,
        metric_key=metric_key,
        provider_key=provider_key,
        priority_rank=priority_rank,
    )
    db.add(rule)
    db.flush()
    return rule


def get_policy(db: Session, user_id: UUID, policy_id: UUID) -> SourcePriorityPolicy | None:
    return db.scalar(
        select(SourcePriorityPolicy).where(
            SourcePriorityPolicy.id == policy_id,
            SourcePriorityPolicy.user_id == user_id,
        )
    )


def get_effective_policy(
    db: Session,
    user_id: UUID,
    at: datetime,
) -> SourcePriorityPolicy | None:
    at = validate_aware_datetime(at)
    return db.scalar(
        select(SourcePriorityPolicy)
        .where(
            SourcePriorityPolicy.user_id == user_id,
            SourcePriorityPolicy.effective_from <= at,
        )
        .order_by(SourcePriorityPolicy.effective_from.desc())
        .limit(1)
    )


def list_rules(
    db: Session,
    user_id: UUID,
    policy_id: UUID,
    *,
    data_area: str | None = None,
    metric_key: object = UNSET,
) -> list[SourcePriorityRule]:
    statement = select(SourcePriorityRule).where(
        SourcePriorityRule.user_id == user_id,
        SourcePriorityRule.policy_id == policy_id,
    )
    if data_area is not None:
        validate_non_empty(data_area, "data_area")
        statement = statement.where(SourcePriorityRule.data_area == data_area)
    if metric_key is not UNSET:
        if metric_key is not None:
            if not isinstance(metric_key, str):
                raise TypeError("metric_key must be a string or None")
            validate_non_empty(metric_key, "metric_key")
            statement = statement.where(SourcePriorityRule.metric_key == metric_key)
        else:
            statement = statement.where(SourcePriorityRule.metric_key.is_(None))
    return list(db.scalars(statement.order_by(SourcePriorityRule.priority_rank, SourcePriorityRule.id)))
