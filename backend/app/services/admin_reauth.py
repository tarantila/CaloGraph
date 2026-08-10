from typing import Literal, Never
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import verify_password
from app.config import settings
from app.models import User, UserTotpCredential
from app.security_events import log_security_event, security_reference
from app.services.mfa import consume_mfa_factor
from app.services.rate_limit import (
    check_rate_limit,
    clear_rate_limit,
    ensure_rate_limit_available,
)

AdminReauthenticationReason = Literal["not_admin", "invalid_credentials"]


class AdminReauthenticationRejected(RuntimeError):
    def __init__(self, reason: AdminReauthenticationReason) -> None:
        self.reason = reason
        super().__init__(reason)


def _rate_limit_key(actor_id: UUID) -> str:
    return f"user:{actor_id}"


def _log_rejection(actor_id: UUID, reason: AdminReauthenticationReason) -> None:
    log_security_event(
        "admin.reauthentication.failed",
        actor_ref=security_reference("user", actor_id),
        reason=reason,
    )


def _reject(
    db: Session,
    actor_id: UUID,
    reason: AdminReauthenticationReason,
    *,
    count_attempt: bool,
) -> Never:
    db.rollback()
    if count_attempt:
        check_rate_limit(
            db,
            "admin-reauthentication",
            _rate_limit_key(actor_id),
            settings.password_change_rate_limit,
            settings.password_change_rate_limit_window_seconds,
        )
    _log_rejection(actor_id, reason)
    raise AdminReauthenticationRejected(reason)


def verify_admin_reauthentication(
    db: Session,
    actor_id: UUID,
    current_password: str,
    code: str | None,
) -> None:
    key = _rate_limit_key(actor_id)
    ensure_rate_limit_available(
        db,
        "admin-reauthentication",
        key,
        settings.password_change_rate_limit,
        settings.password_change_rate_limit_window_seconds,
    )
    actor = db.scalar(
        select(User).where(User.id == actor_id).execution_options(populate_existing=True)
    )
    if actor is None or not actor.is_active or not actor.is_admin:
        _reject(db, actor_id, "not_admin", count_attempt=False)
    if not verify_password(actor.password_hash, current_password):
        _reject(db, actor_id, "invalid_credentials", count_attempt=True)

    credential = db.scalar(
        select(UserTotpCredential)
        .where(
            UserTotpCredential.user_id == actor.id,
            UserTotpCredential.enabled_at.is_not(None),
        )
        .with_for_update()
    )
    if credential is not None:
        if not code or not consume_mfa_factor(db, credential, code):
            _reject(db, actor_id, "invalid_credentials", count_attempt=True)
        db.commit()

    clear_rate_limit(db, "admin-reauthentication", key)
