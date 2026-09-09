from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.source_priority.contracts import UNSET
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule
from app.source_priority.repositories import (
    add_rule,
    create_policy,
    get_effective_policy,
    get_policy,
    list_rules,
)

_FIRST_EFFECTIVE = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _other_user(db, user: User, username: str = "source-priority-other") -> User:
    other = User(username=username, password_hash=user.password_hash, timezone="UTC")
    db.add(other)
    db.flush()
    return other


def _policy(
    db,
    user: User,
    *,
    version: int = 1,
    effective_from: datetime = _FIRST_EFFECTIVE,
    flush: bool = True,
) -> SourcePriorityPolicy:
    policy = SourcePriorityPolicy(
        user_id=user.id,
        version=version,
        effective_from=effective_from,
    )
    db.add(policy)
    if flush:
        db.flush()
    return policy


def _rule(
    db,
    user: User,
    policy: SourcePriorityPolicy,
    *,
    data_area: str = "nutrition",
    metric_key: str | None = None,
    provider_key: str = "yazio",
    priority_rank: int = 1,
    flush: bool = True,
) -> SourcePriorityRule:
    rule = SourcePriorityRule(
        user_id=user.id,
        policy_id=policy.id,
        data_area=data_area,
        metric_key=metric_key,
        provider_key=provider_key,
        priority_rank=priority_rank,
    )
    db.add(rule)
    if flush:
        db.flush()
    return rule




def test_policy_version_must_be_at_least_one(db, user) -> None:
    _policy(db, user, version=0, flush=False)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_policy_version_is_unique_per_user(db, user) -> None:
    _policy(db, user, version=1)
    db.commit()
    _policy(db, user, version=1, effective_from=_FIRST_EFFECTIVE.replace(day=2), flush=False)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_policy_effective_timestamp_is_unique_per_user(db, user) -> None:
    _policy(db, user, version=1)
    db.commit()
    _policy(db, user, version=2, flush=False)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_same_policy_version_is_allowed_for_different_users(db, user) -> None:
    other = _other_user(db, user)
    _policy(db, user, version=1)
    _policy(db, other, version=1)
    db.commit()


def test_policy_effective_lookup_is_timezone_aware_and_has_no_earlier_result(db, user) -> None:
    first = create_policy(db, user.id, 1, _FIRST_EFFECTIVE)
    second = create_policy(
        db,
        user.id,
        2,
        datetime(2026, 1, 2, 9, tzinfo=UTC),
    )
    db.commit()

    assert get_effective_policy(
        db,
        user.id,
        datetime(2026, 1, 2, 10, 30, tzinfo=UTC),
    ).id == second.id
    assert get_effective_policy(
        db,
        user.id,
        datetime(2026, 1, 1, 12, 30, tzinfo=UTC),
    ).id == first.id
    assert get_effective_policy(
        db,
        user.id,
        datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
    ) is None

    with pytest.raises(ValueError, match=r"timezone|aware|naive"):
        create_policy(db, user.id, 3, datetime(2026, 1, 3, 12))
    with pytest.raises(ValueError, match=r"timezone|aware|naive"):
        get_effective_policy(db, user.id, datetime(2026, 1, 3, 12))


def test_effective_policy_lookup_normalizes_equivalent_utc_offsets(db, user) -> None:
    policy = create_policy(db, user.id, 1, datetime(2026, 1, 3, 12, tzinfo=UTC))
    db.commit()

    equivalent_local_time = datetime(
        2026,
        1,
        3,
        7,
        tzinfo=timezone(timedelta(hours=-5)),
    )
    assert get_effective_policy(db, user.id, equivalent_local_time).id == policy.id


def test_wildcard_provider_uniqueness_is_enforced(db, user) -> None:
    policy = _policy(db, user)
    _rule(db, user, policy, provider_key="yazio", priority_rank=1)
    db.commit()
    _rule(db, user, policy, provider_key="yazio", priority_rank=2, flush=False)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_wildcard_rank_uniqueness_is_enforced(db, user) -> None:
    policy = _policy(db, user)
    _rule(db, user, policy, provider_key="yazio", priority_rank=1)
    db.commit()
    _rule(db, user, policy, provider_key="apple_health", priority_rank=1, flush=False)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_metric_provider_uniqueness_is_enforced(db, user) -> None:
    policy = _policy(db, user)
    _rule(db, user, policy, metric_key="energy", provider_key="yazio", priority_rank=1)
    db.commit()
    _rule(
        db,
        user,
        policy,
        metric_key="energy",
        provider_key="yazio",
        priority_rank=2,
        flush=False,
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_metric_rank_uniqueness_is_enforced(db, user) -> None:
    policy = _policy(db, user)
    _rule(db, user, policy, metric_key="energy", provider_key="yazio", priority_rank=1)
    db.commit()
    _rule(
        db,
        user,
        policy,
        metric_key="energy",
        provider_key="apple_health",
        priority_rank=1,
        flush=False,
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_metric_provider_and_rank_scopes_are_independent(db, user) -> None:
    policy = _policy(db, user)
    _rule(db, user, policy, provider_key="yazio", priority_rank=1)
    _rule(db, user, policy, metric_key="energy", provider_key="yazio", priority_rank=1)
    _rule(db, user, policy, metric_key="protein", provider_key="yazio", priority_rank=1)
    _rule(db, user, policy, data_area="activity", provider_key="yazio", priority_rank=1)
    db.commit()


def test_rules_require_positive_rank_and_non_empty_scope_values(db, user) -> None:
    policy = _policy(db, user)
    db.commit()
    for invalid in (
        {"priority_rank": 0},
        {"data_area": ""},
        {"provider_key": ""},
        {"metric_key": ""},
    ):
        kwargs = {
            "data_area": "nutrition",
            "metric_key": None,
            "provider_key": "yazio",
            "priority_rank": 1,
        }
        kwargs.update(invalid)
        db.add(
            SourcePriorityRule(
                user_id=user.id,
                policy_id=policy.id,
                **kwargs,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_rule_policy_composite_fk_rejects_cross_user_reference(db, user) -> None:
    other = _other_user(db, user)
    policy = _policy(db, user)
    db.commit()
    db.add(
        SourcePriorityRule(
            user_id=other.id,
            policy_id=policy.id,
            data_area="nutrition",
            metric_key=None,
            provider_key="yazio",
            priority_rank=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_repository_policy_and_rule_operations_are_append_only_reads(db, user) -> None:
    first = create_policy(db, user.id, 1, _FIRST_EFFECTIVE)
    second = create_policy(
        db,
        user.id,
        2,
        datetime(2026, 1, 2, 12, tzinfo=UTC),
    )
    wildcard = add_rule(db, user.id, first.id, "nutrition", None, "yazio", 1)
    metric = add_rule(db, user.id, first.id, "nutrition", "energy", "apple_health", 2)
    db.commit()

    assert get_policy(db, user.id, first.id).id == first.id
    assert get_policy(db, user.id, second.id).id == second.id
    assert get_policy(db, user.id, uuid4()) is None
    assert get_policy(db, user.id, first.id).version == 1
    assert [item.id for item in list_rules(db, user.id, first.id)] == [wildcard.id, metric.id]
    assert list_rules(db, user.id, first.id, data_area="nutrition", metric_key=None)[0].id == wildcard.id
    assert list_rules(db, user.id, first.id, data_area="nutrition", metric_key="energy")[0].id == metric.id
    assert list_rules(db, user.id, first.id, metric_key=UNSET) == [wildcard, metric]

    other = _other_user(db, user, "source-priority-reader")
    db.commit()
    assert get_policy(db, other.id, first.id) is None
    assert list_rules(db, other.id, first.id) == []
