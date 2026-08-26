from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import SecurityAuditEvent

AuditMethod = Literal["password", "password+mfa", "passkey"]

LOGIN_SUCCEEDED_EVENT = "auth.login.succeeded"
LOGIN_FAILED_EVENT = "auth.login.failed"


@dataclass(frozen=True, slots=True)
class SecurityAuditMetrics:
    audit_events: int
    successful_logins: int
    failed_logins: int

AUDITED_EVENTS = frozenset(
    {
        "auth.login.succeeded",
        "auth.login.failed",
        "auth.login.mfa_required",
        "auth.passkey.login_succeeded",
        "auth.passkey.login_failed",
        "auth.session.logged_out",
        "auth.password.changed",
        "auth.password.recovered",
        "auth.invitation.created",
        "auth.invitation.revoked",
        "admin.user.deactivated",
        "admin.user.reactivated",
        "admin.user.deleted",
        "admin.user.recovery_issued",
        "admin.authenticators.reset",
    }
)

logger = logging.getLogger("calograph.audit")


def security_audit_metrics_24h(
    db: Session,
    *,
    now: datetime | None = None,
) -> SecurityAuditMetrics:
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=24)
    audit_events, successful_logins, failed_logins = db.execute(
        select(
            func.count(SecurityAuditEvent.id),
            func.count(SecurityAuditEvent.id).filter(
                SecurityAuditEvent.event == LOGIN_SUCCEEDED_EVENT
            ),
            func.count(SecurityAuditEvent.id).filter(
                SecurityAuditEvent.event == LOGIN_FAILED_EVENT
            ),
        ).where(SecurityAuditEvent.occurred_at >= cutoff)
    ).one()
    return SecurityAuditMetrics(
        audit_events=int(audit_events),
        successful_logins=int(successful_logins),
        failed_logins=int(failed_logins),
    )


def purge_expired_security_audit_events(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=settings.security_audit_retention_days)
    result = db.execute(delete(SecurityAuditEvent).where(SecurityAuditEvent.occurred_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


def record_security_audit(
    *,
    event: str,
    outcome: str,
    auth_method: AuditMethod | None = None,
    actor_user_id: UUID | None = None,
    target_user_id: UUID | None = None,
    actor_ref: str | None = None,
    target_ref: str | None = None,
    username_snapshot: str | None = None,
    request_id: str | None = None,
    client_ip: str | None = None,
    client_ref: str | None = None,
    reason: str | None = None,
) -> None:
    if event not in AUDITED_EVENTS:
        return
    try:
        with SessionLocal() as db:
            purge_expired_security_audit_events(db)
            db.add(
                SecurityAuditEvent(
                    event=event,
                    outcome=outcome,
                    auth_method=auth_method,
                    actor_user_id=actor_user_id,
                    target_user_id=target_user_id,
                    actor_ref=actor_ref,
                    target_ref=target_ref,
                    username_snapshot=username_snapshot,
                    request_id=request_id,
                    client_ip=client_ip,
                    client_ref=client_ref,
                    reason=reason,
                )
            )
            db.commit()
    except Exception:
        logger.exception("Persisting security audit event failed: %s", event)
