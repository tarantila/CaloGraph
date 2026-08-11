import hashlib
import hmac
import ipaddress
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import case, delete, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine
from app.models import RateLimitBucket


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int, action: str, key_ref: str) -> None:
        self.retry_after = max(1, retry_after)
        self.action = action
        self.key_ref = key_ref
        super().__init__(
            status_code=429,
            detail="Zu viele Anfragen. Bitte später erneut versuchen.",
            headers={"Retry-After": str(self.retry_after)},
        )


def normalize_account_identifier(username: str) -> str:
    return unicodedata.normalize("NFKC", username).strip().casefold()


def normalize_client_ip(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    if isinstance(address, ipaddress.IPv6Address):
        network = ipaddress.ip_network(f"{address}/64", strict=False)
        return f"{network.network_address}/64"
    return str(address)


def hash_rate_limit_key(key: str) -> str:
    return hmac.new(
        settings.rate_limit_secret.encode(), key.encode(), hashlib.sha256
    ).hexdigest()


def rate_limit_key_id(key: str) -> str:
    return hash_rate_limit_key(key)[:16]


LOGIN_PASSWORD_MAX_PARALLEL = 4
_local_login_slots = threading.BoundedSemaphore(LOGIN_PASSWORD_MAX_PARALLEL)


def _login_slot_lock_id(slot: int) -> int:
    digest = hashlib.sha256(f"calograph-login-password-slot:{slot}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _login_capacity_exceeded() -> RateLimitExceeded:
    key_ref = hash_rate_limit_key("global:login-password-capacity")[:16]
    return RateLimitExceeded(1, "login-capacity", key_ref)


@contextmanager
def login_password_slot() -> Iterator[None]:
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            slot_lock_id: int | None = None
            for slot in range(LOGIN_PASSWORD_MAX_PARALLEL):
                candidate = _login_slot_lock_id(slot)
                if connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": candidate},
                ):
                    slot_lock_id = candidate
                    break
            if slot_lock_id is None:
                raise _login_capacity_exceeded()
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": slot_lock_id},
                )
        return

    if not _local_login_slots.acquire(blocking=False):
        raise _login_capacity_exceeded()
    try:
        yield
    finally:
        _local_login_slots.release()


def _window(now: datetime, window_seconds: int) -> tuple[datetime, int]:
    timestamp = int(now.timestamp())
    window_timestamp = timestamp - (timestamp % window_seconds)
    window_start = datetime.fromtimestamp(window_timestamp, tz=UTC)
    retry_after = window_seconds - (timestamp - window_timestamp)
    return window_start, retry_after


def _current_count(
    db: Session,
    action: str,
    key_hash: str,
    window_start: datetime,
) -> int:
    return (
        db.scalar(
            select(RateLimitBucket.count).where(
                RateLimitBucket.key_hash == key_hash,
                RateLimitBucket.action == action,
                RateLimitBucket.window_start == window_start,
            )
        )
        or 0
    )


def ensure_rate_limit_available(
    db: Session,
    action: str,
    key: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    now = datetime.now(UTC)
    window_start, retry_after = _window(now, window_seconds)
    if _current_count(db, action, hash_rate_limit_key(key), window_start) >= limit:
        raise RateLimitExceeded(retry_after, action, hash_rate_limit_key(key)[:16])


def check_rate_limit(
    db: Session,
    action: str,
    key: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    now = datetime.now(UTC)
    window_start, retry_after = _window(now, window_seconds)
    key_hash = hash_rate_limit_key(key)
    values = {
        "key_hash": key_hash,
        "action": action,
        "window_start": window_start,
        "count": 1,
    }
    capped_increment = case(
        (
            RateLimitBucket.count < limit + 1,
            RateLimitBucket.count + 1,
        ),
        else_=RateLimitBucket.count,
    )
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "postgresql":
        statement = (
            postgresql_insert(RateLimitBucket)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_rate_bucket",
                set_={"count": capped_increment},
            )
            .returning(RateLimitBucket.count)
        )
    elif dialect_name == "sqlite":
        statement = (
            sqlite_insert(RateLimitBucket)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["key_hash", "action", "window_start"],
                set_={"count": capped_increment},
            )
            .returning(RateLimitBucket.count)
        )
    else:
        raise RuntimeError(f"Rate limiting is not supported for database dialect {dialect_name!r}")

    count = db.scalar(statement)
    db.commit()
    if count is None:
        raise RuntimeError("Rate-limit counter update returned no value")
    if count > limit:
        raise RateLimitExceeded(retry_after, action, key_hash[:16])


def clear_rate_limit(db: Session, action: str, key: str) -> None:
    db.execute(
        delete(RateLimitBucket).where(
            RateLimitBucket.key_hash == hash_rate_limit_key(key),
            RateLimitBucket.action == action,
        )
    )
    db.commit()


def purge_expired_rate_limit_buckets(
    db: Session,
    retention_hours: int,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=retention_hours)
    result = db.execute(
        delete(RateLimitBucket).where(RateLimitBucket.window_start < cutoff)
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
