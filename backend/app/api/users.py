import secrets
from datetime import UTC, datetime, timedelta
from typing import Never
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.auth.security import hash_invitation_token
from app.config import settings
from app.database import get_db
from app.models import User, UserInvitation
from app.schemas import (
    InvitationCreatedResponse,
    InvitationCreateRequest,
    InvitationResponse,
    UserResponse,
)
from app.security_events import log_security_event, security_reference
from app.services.user_lifecycle import (
    UserLifecycleRejected,
    deactivate_user,
    delete_user,
    reactivate_user,
)

router = APIRouter(prefix="/users", tags=["Benutzer"])


def _admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administratorrechte erforderlich")


def _raise_lifecycle_rejection(exc: UserLifecycleRejected) -> Never:
    if exc.reason == "not_admin":
        raise HTTPException(
            status_code=403,
            detail="Aktive Administratorrechte erforderlich",
        ) from exc
    if exc.reason == "target_missing":
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden") from exc
    details = {
        "self_action": "Die Aktion auf dem eigenen Konto ist nicht erlaubt.",
        "last_admin": "Der letzte aktive Administrator muss erhalten bleiben.",
        "target_active": "Der Benutzer muss vor dem Löschen deaktiviert werden.",
        "operation_busy": "Für dieses Konto läuft bereits eine Benutzeroperation.",
    }
    raise HTTPException(status_code=409, detail=details[exc.reason]) from exc


@router.get("", response_model=list[UserResponse])
def list_users(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[User]:
    _admin(user)
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.get("/invitations", response_model=list[InvitationResponse])
def list_invitations(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[UserInvitation]:
    _admin(user)
    return list(
        db.scalars(
            select(UserInvitation)
            .where(UserInvitation.invited_by_user_id == user.id)
            .order_by(UserInvitation.created_at.desc())
            .limit(50)
        )
    )


@router.post("/invitations", response_model=InvitationCreatedResponse, status_code=201)
def create_invitation(
    payload: InvitationCreateRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InvitationCreatedResponse:
    _admin(user)
    raw = f"invite_{secrets.token_urlsafe(40)}"
    invitation = UserInvitation(
        token_hash=hash_invitation_token(raw),
        invited_by_user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    log_security_event(
        "auth.invitation.created",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("invitation", invitation.id),
    )
    return InvitationCreatedResponse(
        id=invitation.id,
        invitation_url=f"{settings.calograph_public_url}/einladung#token={raw}",
        expires_at=invitation.expires_at,
    )


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    invitation_id: UUID,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    _admin(user)
    invitation = db.scalar(
        select(UserInvitation).where(
            UserInvitation.id == invitation_id,
            UserInvitation.invited_by_user_id == user.id,
        )
    )
    if invitation is None:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")
    invitation.revoked_at = datetime.now(UTC)
    db.commit()
    log_security_event(
        "auth.invitation.revoked",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("invitation", invitation.id),
    )


@router.post("/{user_id}/deactivate", response_model=UserResponse)
def deactivate_account(
    user_id: UUID,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> User:
    try:
        return deactivate_user(db, user.id, user_id)
    except UserLifecycleRejected as exc:
        _raise_lifecycle_rejection(exc)


@router.post("/{user_id}/reactivate", response_model=UserResponse)
def reactivate_account(
    user_id: UUID,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> User:
    try:
        return reactivate_user(db, user.id, user_id)
    except UserLifecycleRejected as exc:
        _raise_lifecycle_rejection(exc)


@router.delete("/{user_id}", status_code=204)
def delete_account(
    user_id: UUID,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    try:
        delete_user(db, user.id, user_id)
    except UserLifecycleRejected as exc:
        _raise_lifecycle_rejection(exc)