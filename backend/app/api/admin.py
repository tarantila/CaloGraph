from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.auth.dependencies import require_admin
from app.config import settings
from app.database import get_db
from app.models import SecurityAuditEvent, User, UserInvitation, UserSession
from app.schemas import InvitationResponse, UserResponse
from app.services.app_logs import get_app_logs
from app.services.geoip import lookup_client_ip
from app.services.release_status import get_release_status
from app.services.security_audit import security_audit_metrics_24h
from app.services.yazio_sync import due_yazio_connection_ids

router = APIRouter(prefix="/admin", tags=["Administration"])


def _active_session_filter(now: datetime) -> tuple[ColumnElement[bool], ...]:
    idle_cutoff = now - timedelta(hours=settings.session_idle_timeout_hours)
    return (
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
        func.coalesce(UserSession.last_used_at, UserSession.created_at) > idle_cutoff,
    )


@router.get("/overview")
def overview(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    now = datetime.now(UTC)
    active_users = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    audit_metrics = security_audit_metrics_24h(db, now=now)
    active_sessions = db.scalar(
        select(func.count()).select_from(UserSession).where(*_active_session_filter(now))
    ) or 0
    open_invitations = db.scalar(
        select(func.count()).select_from(UserInvitation).where(
            UserInvitation.used_at.is_(None),
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > now,
        )
    ) or 0
    latest_events = db.execute(
        select(SecurityAuditEvent, User.username)
        .outerjoin(User, User.id == SecurityAuditEvent.actor_user_id)
        .order_by(SecurityAuditEvent.occurred_at.desc(), SecurityAuditEvent.id.desc())
        .limit(5)
    ).all()
    return {
        "active_users": int(active_users),
        "active_sessions": int(active_sessions),
        "open_invitations": int(open_invitations),
        "successful_logins_24h": audit_metrics.successful_logins,
        "failed_logins_24h": audit_metrics.failed_logins,
        "recent_events": [
            {
                "id": item.id,
                "occurred_at": item.occurred_at,
                "event": item.event,
                "outcome": item.outcome,
                "username": username or item.username_snapshot or "Unbekannt",
            }
            for item, username in latest_events
        ],
    }


@router.get("/system")
def system_status(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    db.execute(select(1))
    audit_metrics = security_audit_metrics_24h(db)
    try:
        application_version = version("calograph-backend")
    except PackageNotFoundError:
        application_version = "unknown"
    scheduler_available = False
    if settings.yazio_enabled:
        try:
            scheduler_available = bool(due_yazio_connection_ids())
        except Exception:
            scheduler_available = False
    return {
        "version": get_release_status(
            application_version,
            enabled=settings.release_status_enabled,
        ),
        "database": "healthy",
        "security_audit_retention_days": settings.security_audit_retention_days,
        "security_audit_enabled": True,
        "security_audit_events_24h": audit_metrics.audit_events,
        "failed_logins_24h": audit_metrics.failed_logins,
        "yazio_scheduler_enabled": settings.yazio_enabled,
        "yazio_scheduler_available": scheduler_available,
    }


@router.get("/users", response_model=list[UserResponse])
def admin_users(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.get("/invitations", response_model=list[InvitationResponse])
def admin_invitations(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[UserInvitation]:
    return list(
        db.scalars(select(UserInvitation).order_by(UserInvitation.created_at.desc()).limit(100))
    )


@router.get("/audit")
def audit_events(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    since: datetime | None = None,
    until: datetime | None = None,
    outcome: str | None = Query(default=None, max_length=16),
    event: str | None = Query(default=None, max_length=64),
    user_id: UUID | None = None,
) -> dict[str, object]:
    filters = []
    if since is not None:
        filters.append(SecurityAuditEvent.occurred_at >= since)
    if until is not None:
        filters.append(SecurityAuditEvent.occurred_at < until)
    if outcome is not None:
        filters.append(SecurityAuditEvent.outcome == outcome)
    if event is not None:
        filters.append(SecurityAuditEvent.event == event)
    if user_id is not None:
        filters.append(or_(SecurityAuditEvent.actor_user_id == user_id, SecurityAuditEvent.target_user_id == user_id))
    rows = db.execute(
        select(SecurityAuditEvent, User.username)
        .outerjoin(User, User.id == SecurityAuditEvent.actor_user_id)
        .where(*filters)
        .order_by(SecurityAuditEvent.occurred_at.desc(), SecurityAuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = []
    for item, username in rows:
        geo = lookup_client_ip(item.client_ip)
        items.append({
            "id": item.id,
            "occurred_at": item.occurred_at,
            "event": item.event,
            "outcome": item.outcome,
            "auth_method": item.auth_method,
            "username": username or item.username_snapshot or "Unbekannt",
            "client_ip": item.client_ip,
            "client_ref": item.client_ref,
            "location": geo.location if geo else None,
            "provider": geo.provider if geo else None,
            "reason": item.reason,
        })
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/logs")
def app_logs(
    _: User = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=100),
    request_id: str | None = Query(default=None, max_length=32),
    action: str | None = Query(default=None, max_length=128),
    level: str | None = Query(default=None, max_length=16),
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, object]:
    return {
        "items": get_app_logs(
            request_id=request_id,
            action=action,
            level=level,
            since=since,
            until=until,
            limit=limit,
        ),
        "buffer_limit": 500,
        "persistence": "process",
    }
