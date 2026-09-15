from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.source_priority.repositories as source_priority_repositories
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

_FIRST_EFFECTIVE = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _as_utc(value):
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _rules(db, policy_id):
    return list(
        db.scalars(
            select(SourcePriorityRule)
            .where(SourcePriorityRule.policy_id == policy_id)
            .order_by(SourcePriorityRule.priority_rank, SourcePriorityRule.id)
        )
    )


def test_source_priority_has_no_independent_policy_or_rule_writer_surface() -> None:
    assert not hasattr(source_priority_repositories, "create_policy")
    assert not hasattr(source_priority_repositories, "add_rule")


def test_complete_version_creation_preserves_historical_policy_and_rules(db, user) -> None:
    first = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=_FIRST_EFFECTIVE,
        rules=(
            PriorityRuleSpec("nutrition", None, "yazio", 1),
            PriorityRuleSpec("nutrition", "protein_g", "google_health", 1),
        ),
    )
    db.commit()
    first_policy = db.get(SourcePriorityPolicy, first.policy_id)
    assert first_policy is not None
    first_rules = [
        (
            rule.data_area,
            rule.metric_key,
            rule.provider_key,
            rule.priority_rank,
        )
        for rule in _rules(db, first.policy_id)
    ]

    second = create_policy_with_rules(
        db,
        user.id,
        version=2,
        effective_from=_FIRST_EFFECTIVE + timedelta(days=1),
        rules=(PriorityRuleSpec("nutrition", None, "google_health", 1),),
    )
    db.commit()

    assert second.policy_id != first.policy_id
    assert (
        first_policy.id,
        first_policy.version,
        _as_utc(first_policy.effective_from),
    ) == (first.policy_id, 1, _FIRST_EFFECTIVE)
    assert [
        (
            rule.data_area,
            rule.metric_key,
            rule.provider_key,
            rule.priority_rank,
        )
        for rule in _rules(db, first.policy_id)
    ] == first_rules


def test_failed_version_creation_rolls_back_without_orphan_rules(db, user) -> None:
    first = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=_FIRST_EFFECTIVE,
        rules=(PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    db.commit()

    with pytest.raises(IntegrityError):
        create_policy_with_rules(
            db,
            user.id,
            version=1,
            effective_from=_FIRST_EFFECTIVE + timedelta(days=1),
            rules=(PriorityRuleSpec("nutrition", None, "google_health", 1),),
        )
    db.rollback()

    policies = list(
        db.scalars(select(SourcePriorityPolicy).where(SourcePriorityPolicy.user_id == user.id))
    )
    assert [policy.id for policy in policies] == [first.policy_id]
    assert [(rule.policy_id, rule.provider_key) for rule in _rules(db, first.policy_id)] == [
        (first.policy_id, "yazio")
    ]
