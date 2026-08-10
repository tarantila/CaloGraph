from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal, Never
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    ApiToken,
    RateLimitBucket,
    User,
    UserInvitation,
    UserSession,
    WebAuthnChallenge,
    YazioConnection,
)
from app.security_events import log_security_event, security_reference
from app.services.rate_limit import hash_rate_limit_key, normalize_account_identifier
from app.services.user_operation_lock import (
    UserOperationBusy,
    exclusive_admin_invariant_operation,
    exclusive_user_lifecycle_operation,
)

LifecycleAction = Literal["deactivate", "reactivate", "delete"]
LifecycleReason = Literal[
    "not_admin",
    "self_action",
    "last_admin",
    "target_active",
    "target_missing",
    "operation_busy",
]


class UserLifecycleRejected(RuntimeError):
    def __init__(self, action: LifecycleAction, reason: LifecycleReason) -> None:
        self.action = action
        self.reason = reason
        super().__init__(reason)


def _reject(action: LifecycleAction, reason: LifecycleReason) -> Never:
    raise UserLifecycleRejected(action, reason)


def _log_rejection(
    action: LifecycleAction,
    reason: LifecycleReason,
    actor_id: UUID,
    target_id: UUID,
) -> None:
    log_security_event(
        "admin.user.lifecycle_rejected",
        actor_ref=security_reference("user", actor_id),
        target_ref=security_reference("user", target_id),
        details={"action": action, "reason": reason},
    )


def _log_failure(
    action: LifecycleAction,
    actor_id: UUID,
    target_id: UUID,
) -> None:
    log_security_event(
        "admin.user.lifecycle_failed",
        actor_ref=security_reference("user", actor_id),
        target_ref=security_reference("user", target_id),
        details={"action": action, "reason": "transaction_failed"},
    )


@contextmanager
def _lifecycle_operation(
    db: Session,
    action: LifecycleAction,
    actor_id: UUID,
    target_id: UUID,
) -> Iterator[None]:
    try:
        with (
            exclusive_user_lifecycle_operation(db, target_id),
            exclusive_admin_invariant_operation(db),
        ):
            yield
    except UserOperationBusy as exc:
        db.rollback()
        _log_rejection(action, "operation_busy", actor_id, target_id)
        raise UserLifecycleRejected(action, "operation_busy") from exc
    except UserLifecycleRejected as exc:
        db.rollback()
        _log_rejection(action, exc.reason, actor_id, target_id)
        raise
    except Exception:
        db.rollback()
        _log_failure(action, actor_id, target_id)
        raise


def _locked_actor_and_target(
    db: Session,
    action: LifecycleAction,
    actor_id: UUID,
    target_id: UUID,
) -> tuple[User, User]:
    user_ids = sorted({actor_id, target_id}, key=str)
    users = list(
        db.scalars(
            select(User)
            .where(User.id.in_(user_ids))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    users_by_id = {user.id: user for user in users}
    actor = users_by_id.get(actor_id)
    if actor is None or not actor.is_active or not actor.is_admin:
        _reject(action, "not_admin")
    if actor_id == target_id:
        _reject(action, "self_action")
    target = users_by_id.get(target_id)
    if target is None:
        _reject(action, "target_missing")
    return actor, target


def _lock_active_admins(db: Session) -> list[UUID]:
    return list(
        db.scalars(
            select(User.id)
            .where(User.is_admin.is_(True), User.is_active.is_(True))
            .order_by(User.id)
            .with_for_update()
        )
    )


def _ensure_admin_survives(db: Session, action: LifecycleAction, target: User) -> None:
    active_admin_ids = _lock_active_admins(db)
    if target.is_admin and target.is_active and len(active_admin_ids) <= 1:
        _reject(action, "last_admin")


def _ensure_actor_may_manage(
    db: Session,
    action: LifecycleAction,
    actor_id: UUID,
    target_id: UUID,
) -> None:
    actor = db.scalar(
        select(User).where(User.id == actor_id).execution_options(populate_existing=True)
    )
    if actor is None or not actor.is_active or not actor.is_admin:
        _log_rejection(action, "not_admin", actor_id, target_id)
        _reject(action, "not_admin")


def _ensure_not_self_action(
    action: LifecycleAction,
    actor_id: UUID,
    target_id: UUID,
) -> None:
    if actor_id == target_id:
        _log_rejection(action, "self_action", actor_id, target_id)
        _reject(action, "self_action")


def _success_event(event: str, actor_id: UUID, target_id: UUID) -> None:
    log_security_event(
        event,
        actor_ref=security_reference("user", actor_id),
        target_ref=security_reference("user", target_id),
    )


def deactivate_user(
    db: Session,
    actor_id: UUID,
    target_id: UUID,
    *,
    now: datetime | None = None,
) -> User:
    action: LifecycleAction = "deactivate"
    _ensure_actor_may_manage(db, action, actor_id, target_id)
    _ensure_not_self_action(action, actor_id, target_id)
    changed_at = now or datetime.now(UTC)
    with _lifecycle_operation(db, action, actor_id, target_id):
        _, target = _locked_actor_and_target(db, action, actor_id, target_id)
        _ensure_admin_survives(db, action, target)
        if target.is_active:
            target.is_active = False
            target.deactivated_at = changed_at
        db.execute(delete(UserSession).where(UserSession.user_id == target.id))
        db.execute(
            update(ApiToken)
            .where(ApiToken.user_id == target.id, ApiToken.revoked_at.is_(None))
            .values(revoked_at=changed_at)
        )
        db.execute(
            update(UserInvitation)
            .where(
                UserInvitation.invited_by_user_id == target.id,
                UserInvitation.used_at.is_(None),
                UserInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=changed_at)
        )
        db.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.user_id == target.id))
        db.execute(
            update(YazioConnection)
            .where(YazioConnection.user_id == target.id)
            .values(sync_enabled=False, next_sync_at=None)
        )
        db.commit()
    _success_event("admin.user.deactivated", actor_id, target_id)
    return target


def reactivate_user(
    db: Session,
    actor_id: UUID,
    target_id: UUID,
) -> User:
    action: LifecycleAction = "reactivate"
    _ensure_actor_may_manage(db, action, actor_id, target_id)
    _ensure_not_self_action(action, actor_id, target_id)
    with _lifecycle_operation(db, action, actor_id, target_id):
        _, target = _locked_actor_and_target(db, action, actor_id, target_id)
        if not target.is_active:
            target.is_active = True
            target.deactivated_at = None
            db.execute(
                update(YazioConnection)
                .where(YazioConnection.user_id == target.id)
                .values(sync_enabled=False, next_sync_at=None)
            )
        db.commit()
    _success_event("admin.user.reactivated", actor_id, target_id)
    return target


def _target_rate_limit_hashes(db: Session, target: User) -> set[str]:
    token_ids = db.scalars(select(ApiToken.id).where(ApiToken.user_id == target.id))
    raw_keys = {
        f"user:{target.id}",
        f"account:{normalize_account_identifier(target.username)}",
        *(f"token:{token_id}" for token_id in token_ids),
    }
    return {hash_rate_limit_key(key) for key in raw_keys}


def delete_user(db: Session, actor_id: UUID, target_id: UUID) -> None:
    action: LifecycleAction = "delete"
    _ensure_actor_may_manage(db, action, actor_id, target_id)
    _ensure_not_self_action(action, actor_id, target_id)
    with _lifecycle_operation(db, action, actor_id, target_id):
        _, target = _locked_actor_and_target(db, action, actor_id, target_id)
        if target.is_active:
            _reject(action, "target_active")
        rate_limit_hashes = _target_rate_limit_hashes(db, target)
        if rate_limit_hashes:
            db.execute(
                delete(RateLimitBucket).where(RateLimitBucket.key_hash.in_(rate_limit_hashes))
            )
        db.execute(delete(User).where(User.id == target.id))
        db.commit()
    _success_event("admin.user.deleted", actor_id, target_id)
