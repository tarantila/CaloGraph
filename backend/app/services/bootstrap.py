from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.password_policy import PasswordPolicyError, validate_new_password
from app.auth.security import hash_password
from app.config import settings
from app.models import InstanceBootstrap, TrackingQualitySettings, User, UserOnboarding
from app.services.user_operation_lock import exclusive_initial_user_operation


class BootstrapAlreadyInitialized(RuntimeError):
    pass


class BootstrapResult(NamedTuple):
    user: User
    first: bool


def bootstrap_status(db: Session) -> bool:
    state = db.get(InstanceBootstrap, 1)
    if state is not None and state.initialized:
        return False
    return (db.scalar(select(func.count(User.id))) or 0) == 0


def create_initial_admin(db: Session, username: str, password: str) -> BootstrapResult:
    """Create the first admin atomically; API and CLI use the same lock boundary."""
    try:
        validate_new_password(password, username)
    except PasswordPolicyError:
        raise
    with exclusive_initial_user_operation(db):
        state = db.get(InstanceBootstrap, 1)
        user_count = db.scalar(select(func.count(User.id))) or 0
        if user_count or (state is not None and state.initialized):
            db.rollback()
            raise BootstrapAlreadyInitialized
        user = User(
            username=username,
            password_hash=hash_password(password),
            timezone=settings.calograph_timezone,
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise BootstrapAlreadyInitialized from exc
        db.add(UserOnboarding(user_id=user.id))
        db.add(TrackingQualitySettings(user_id=user.id))
        if state is None:
            state = InstanceBootstrap(id=1)
            db.add(state)
        state.initialized = True
        state.initialized_at = datetime.now(UTC)
        db.commit()
        db.refresh(user)
        return BootstrapResult(user=user, first=True)
