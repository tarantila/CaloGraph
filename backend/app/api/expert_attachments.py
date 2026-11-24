"""Authenticated bounded binary uploads; no multipart parsing or remote I/O."""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.api.ai_connections import _problem
from app.api.ai_connections import router as ai_router
from app.auth.dependencies import (
    _revalidate_locked_session,
    _validate_csrf,
    current_user,
    require_csrf_exclusive,
)
from app.auth.openapi import session_scheme
from app.database import get_db
from app.expert.attachments import MAX_ATTACHMENT_BYTES, MEDIA_TYPES, AttachmentService
from app.expert.chat_contracts import AttachmentView
from app.models import User
from app.services.user_operation_lock import (
    exclusive_user_lifecycle_operation,
    shared_user_operation,
)

router = APIRouter(prefix="/expert/conversations", tags=["Nutrition Expert"], dependencies=[Security(session_scheme)], responses=ai_router.responses)


_upload_guard = threading.Lock()
_upload_owners: set[UUID] = set()


@contextmanager
def upload_slot(owner_id: UUID) -> Iterator[None]:
    """Bound in-flight uploads in this backend process."""
    with _upload_guard:
        if owner_id in _upload_owners or len(_upload_owners) >= 4:
            raise ConnectionProblem("expert-attachment-busy", 429, "Es werden bereits Anhänge übertragen. Bitte kurz warten.")
        _upload_owners.add(owner_id)
    try:
        yield
    finally:
        with _upload_guard:
            _upload_owners.discard(owner_id)


@router.put(
    "/{conversation_id}/attachments/{identifier}", response_model=AttachmentView,
    operation_id="expert_put_attachment", responses={201: {"model": AttachmentView}, **{code: {**ai_router.responses[422], "description": description} for code, description in ((408, "Upload deadline exceeded."), (413, "File exceeds the upload limit."), (429, "Concurrent upload limit reached."))}},
    openapi_extra={"requestBody": {"required": True, "content": {kind: {"schema": {"type": "string", "format": "binary"}} for kind in ("application/pdf", "image/png", "image/jpeg", "image/webp")}}},
    description="Store an owned, immutable binary attachment locally. X-Attachment-Filename is URI encoded. Maximum 4 MiB/file, 3 files and 8 MiB/conversation, 64 MiB/account. No provider contact. Requires CSRF. Files are not parsed or executed on the host.",
)
async def put_attachment(
    conversation_id: UUID, identifier: UUID, request: Request, response: Response,
    owner: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)],
    x_csrf_token: Annotated[str, Header()] = "",
    x_attachment_filename: Annotated[str, Header(max_length=1600)] = "document",
) -> AttachmentView:
    try:
        _validate_csrf(request, x_csrf_token, owner, db)
        AttachmentService(db, owner=owner).conversation(conversation_id)
        owner_id = owner.id
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type not in MEDIA_TYPES:
            raise ConnectionProblem(
                "expert-attachment-invalid", 422,
                "Bitte eine PDF-, PNG-, JPEG- oder WebP-Datei bis 4 MB auswählen.",
            )
        length = request.headers.get("content-length")
        if length is not None and (not length.isdecimal() or not 1 <= int(length) <= MAX_ATTACHMENT_BYTES):
            raise ConnectionProblem("expert-attachment-size", 413, "Maximal 4 MB pro Anhang.")
        with upload_slot(owner_id):
            # Close the authentication read before waiting on an untrusted client.
            # No account lock or database transaction spans the body transfer.
            db.rollback()
            data = bytearray()
            try:
                async with asyncio.timeout(20):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_ATTACHMENT_BYTES:
                            raise ConnectionProblem("expert-attachment-size", 413, "Maximal 4 MB pro Anhang.")
                        data.extend(chunk)
            except TimeoutError:
                raise ConnectionProblem("expert-attachment-timeout", 408, "Die Übertragung hat zu lange gedauert.") from None
            with exclusive_user_lifecycle_operation(db, owner_id):
                fresh_owner = _revalidate_locked_session(request, owner_id, db)
                _validate_csrf(request, x_csrf_token, fresh_owner, db)
                view, created = AttachmentService(db, owner=fresh_owner).put(
                    conversation_id, identifier, name=unquote(x_attachment_filename, errors="replace"),
                    media_type=media_type, data=bytes(data), _operation_locked=True,
                )
                response.status_code = 201 if created else 200
                return view
    except ConnectionProblem as exc:
        _problem(exc)


@router.delete("/{conversation_id}/attachments/{identifier}", status_code=204, response_class=Response, operation_id="expert_delete_attachment")
def delete_attachment(
    conversation_id: UUID, identifier: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)], db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        AttachmentService(db, owner=owner).delete(conversation_id, identifier, _operation_locked=True)
        return Response(status_code=204)
    except ConnectionProblem as exc:
        _problem(exc)


@router.get("/{conversation_id}/attachments", response_model=tuple[AttachmentView, ...], operation_id="expert_list_draft_attachments")
def list_drafts(conversation_id: UUID, owner: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)]) -> tuple[AttachmentView, ...]:
    try:
        with shared_user_operation(db, owner.id):
            return AttachmentService(db, owner=owner).drafts(conversation_id)
    except ConnectionProblem as exc:
        _problem(exc)
