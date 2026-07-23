import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, require_csrf
from app.auth.security import hash_invitation_token
from app.database import get_db
from app.models import User, UserInvitation
from app.schemas import (
    InvitationCreatedResponse,
    InvitationCreateRequest,
    InvitationResponse,
    UserResponse,
)

router = APIRouter(prefix="/users", tags=["Benutzer"])


def _admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administratorrechte erforderlich")


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
    return InvitationCreatedResponse(
        id=invitation.id,
        token=raw,
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
