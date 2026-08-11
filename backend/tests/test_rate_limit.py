from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RateLimitBucket
from app.services.rate_limit import (
    LOGIN_PASSWORD_MAX_PARALLEL,
    RateLimitExceeded,
    check_rate_limit,
    hash_rate_limit_key,
    login_password_slot,
    normalize_account_identifier,
    normalize_client_ip,
    purge_expired_rate_limit_buckets,
)


def test_client_ip_normalization_groups_ipv6_by_64() -> None:
    assert normalize_client_ip("192.0.2.42") == "192.0.2.42"
    assert normalize_client_ip("::ffff:192.0.2.42") == "192.0.2.42"
    assert normalize_client_ip("2001:db8:1234:5678::1") == "2001:db8:1234:5678::/64"
    assert normalize_client_ip("2001:db8:1234:5678::ffff") == "2001:db8:1234:5678::/64"
    assert normalize_client_ip("not-an-address") == "unknown"


def test_account_identifier_is_trimmed_and_casefolded() -> None:
    assert normalize_account_identifier("  Admin  ") == "admin"
    assert normalize_account_identifier(
        "\uff21\uff24\uff2d\uff29\uff2e"
    ) == "admin"


def test_rate_limit_counter_uses_atomic_upsert(db: Session) -> None:
    check_rate_limit(db, "test", "key", limit=2, window_seconds=300)
    check_rate_limit(db, "test", "key", limit=2, window_seconds=300)

    with pytest.raises(RateLimitExceeded) as error:
        check_rate_limit(db, "test", "key", limit=2, window_seconds=300)

    assert error.value.headers is not None
    assert 1 <= int(error.value.headers["Retry-After"]) <= 300
    assert db.scalar(select(RateLimitBucket.count)) == 3


def test_login_password_slots_are_bounded_and_released() -> None:
    with ExitStack() as stack:
        for _ in range(LOGIN_PASSWORD_MAX_PARALLEL):
            stack.enter_context(login_password_slot())
        with pytest.raises(RateLimitExceeded), login_password_slot():
            pass

    with login_password_slot():
        pass


def test_expired_rate_limit_buckets_are_deleted(db: Session) -> None:
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    db.add_all(
        [
            RateLimitBucket(
                key_hash=hash_rate_limit_key("old"),
                action="test",
                window_start=now - timedelta(hours=25),
                count=1,
            ),
            RateLimitBucket(
                key_hash=hash_rate_limit_key("current"),
                action="test",
                window_start=now - timedelta(hours=23),
                count=1,
            ),
        ]
    )
    db.commit()

    deleted = purge_expired_rate_limit_buckets(db, retention_hours=24, now=now)

    assert deleted == 1
    assert db.scalar(select(func.count(RateLimitBucket.id))) == 1
