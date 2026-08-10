from datetime import UTC, datetime, timedelta
from typing import Never
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.auth.password_policy import PasswordPolicyError, validate_new_password
from app.auth.security import hash_account_recovery_token, hash_password
from app.models import AccountRecoveryToken, ApiToken, User, UserSession, YazioConnection
from app.security_events import log_security_event, security_reference
from app.services.user_operation_lock import (
    UserOperationBusy,
    exclusive_user_lifecycle_operation,
)

ACCOUNT_RECOVERY_RETENTION_HOURS = 24


class AccountRecoveryRejected(RuntimeError):
    pass


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

def _reject(db: Session) -> Never:
    db.rollback()
    raise AccountRecoveryRejected


def complete_account_recovery(
    db: Session,
    raw_token: str,
    new_password: str,
    *,
    now: datetime | None = None,
) -> UUID:
    changed_at = now or datetime.now(UTC)
    token_hash = hash_account_recovery_token(raw_token)
    target_id = db.scalar(
        select(AccountRecoveryToken.user_id).where(
            AccountRecoveryToken.token_hash == token_hash
        )
    )
    if target_id is None:
        _reject(db)

    try:
        with exclusive_user_lifecycle_operation(db, target_id):
            token = db.scalar(
                select(AccountRecoveryToken)
                .where(AccountRecoveryToken.token_hash == token_hash)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            target = db.scalar(
                select(User)
                .where(User.id == target_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                token is None
                or target is None
                or target.is_active
                or token.used_at is not None
                or token.revoked_at is not None
                or _as_utc(token.expires_at) <= _as_utc(changed_at)
            ):
                _reject(db)

            validate_new_password(new_password, target.username)
            target.password_hash = hash_password(new_password)
            token.used_at = changed_at
            db.execute(delete(UserSession).where(UserSession.user_id == target.id))
            db.execute(
                update(ApiToken)
                .where(ApiToken.user_id == target.id, ApiToken.revoked_at.is_(None))
                .values(revoked_at=changed_at)
            )
            db.execute(
                update(YazioConnection)
                .where(YazioConnection.user_id == target.id)
                .values(sync_enabled=False, next_sync_at=None)
            )
            db.commit()
    except UserOperationBusy as exc:
        db.rollback()
        raise AccountRecoveryRejected from exc
    except AccountRecoveryRejected:
        raise
    except PasswordPolicyError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        log_security_event(
            "auth.password.recovery_failed",
            target_ref=security_reference("user", target_id),
            reason="transaction_failed",
        )
        raise

    log_security_event(
        "auth.password.recovered",
        target_ref=security_reference("user", target_id),
    )
    return target_id


def purge_account_recovery_tokens(
    db: Session,
    retention_hours: int = ACCOUNT_RECOVERY_RETENTION_HOURS,
    *,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=retention_hours)
    result = db.execute(
        delete(AccountRecoveryToken).where(
            or_(
                AccountRecoveryToken.expires_at < cutoff,
                AccountRecoveryToken.used_at < cutoff,
                AccountRecoveryToken.revoked_at < cutoff,
            )
        )
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
