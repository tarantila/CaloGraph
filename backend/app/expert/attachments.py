"""Bounded account-owned files, never executed or fetched through external URLs."""

import base64
import hashlib
import unicodedata
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.ai.connections import ConnectionProblem, _aware
from app.ai.contracts import AIMedia
from app.expert.chat_contracts import AttachmentView
from app.models import (
    ExpertAttachment,
    ExpertAttachmentIdentity,
    ExpertConversation,
    ExpertRun,
    User,
)
from app.services.user_operation_lock import exclusive_user_lifecycle_operation

MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_CONVERSATION_BYTES = 8 * 1024 * 1024
MAX_ACCOUNT_BYTES = 64 * 1024 * 1024
MAX_ATTACHMENTS = 3
MEDIA_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg", "image/webp"})


def _now() -> datetime:
    return datetime.now(UTC)


def attachment_view(row: ExpertAttachment) -> AttachmentView:
    return AttachmentView(
        id=row.id,
        file_name=row.file_name,
        media_type=row.media_type,
        byte_size=row.byte_size,
        created_at=_aware(row.created_at),
    )


def file_name(value: str) -> str:
    value = value.replace("\\", "/").split("/")[-1]
    value = "".join(
        char
        for char in unicodedata.normalize("NFC", value)
        if not unicodedata.category(char).startswith("C")
    ).strip()[:120]
    return value or "document"


def validate_file(data: bytes, media_type: str) -> None:
    valid = (
        (media_type == "application/pdf" and data.startswith(b"%PDF-"))
        or (media_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (media_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"))
        or (
            media_type == "image/webp"
            and len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        )
    )
    if media_type not in MEDIA_TYPES or not valid or not 1 <= len(data) <= MAX_ATTACHMENT_BYTES:
        raise ConnectionProblem(
            "expert-attachment-invalid",
            422,
            "Bitte eine PDF-, PNG-, JPEG- oder WebP-Datei bis 4 MB auswählen.",
        )


class AttachmentService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self.db, self.owner = db, owner

    def _active(self) -> None:
        if not self.owner.is_active:
            raise ConnectionProblem("account-inactive", 403, "Das Konto ist nicht aktiv.")

    def conversation(self, identifier: UUID) -> ExpertConversation:
        row = self.db.get(ExpertConversation, (self.owner.id, identifier), populate_existing=True)
        if row is None:
            raise ConnectionProblem("expert-conversation-not-found", 404, "Chat nicht gefunden.")
        return row

    def rows(self, conversation_id: UUID, identifiers: tuple[UUID, ...]) -> tuple[ExpertAttachment, ...]:
        if len(set(identifiers)) != len(identifiers) or len(identifiers) > MAX_ATTACHMENTS:
            raise ConnectionProblem("expert-attachment-limit", 422, "Maximal drei Anhänge pro Anfrage.")
        result = []
        for identifier in identifiers:
            row = self.db.get(ExpertAttachment, (self.owner.id, identifier), populate_existing=True)
            if row is None or row.conversation_id != conversation_id:
                raise ConnectionProblem("expert-attachment-not-found", 404, "Anhang nicht gefunden.")
            result.append(row)
        return tuple(result)

    def put(
        self,
        conversation_id: UUID,
        identifier: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
        _operation_locked: bool = False,
    ) -> tuple[AttachmentView, bool]:
        validate_file(data, media_type)
        name = file_name(name)
        digest = hashlib.sha256(data).hexdigest()
        context = (
            nullcontext()
            if _operation_locked
            else exclusive_user_lifecycle_operation(self.db, self.owner.id)
        )
        with context:
            self._active()
            self.conversation(conversation_id)
            existing = self.db.get(
                ExpertAttachment, (self.owner.id, identifier), populate_existing=True,
            )
            if existing is not None:
                if (
                    existing.conversation_id != conversation_id
                    or existing.sha256 != digest
                    or existing.file_name != name
                    or existing.media_type != media_type
                ):
                    raise ConnectionProblem(
                        "expert-attachment-conflict", 409,
                        "Die Anhang-ID wird bereits verwendet.",
                    )
                return attachment_view(existing), False
            identity = self.db.get(
                ExpertAttachmentIdentity, (self.owner.id, identifier), populate_existing=True,
            )
            if identity is not None:
                raise ConnectionProblem(
                    "expert-attachment-retired", 409,
                    "Die Anhang-ID wurde bereits verwendet. Bitte eine neue Datei-ID verwenden.",
                )

            count, conversation_size = self.db.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(ExpertAttachment.byte_size), 0),
                ).where(
                    ExpertAttachment.user_id == self.owner.id,
                    ExpertAttachment.conversation_id == conversation_id,
                )
            ).one()
            account_size = self.db.scalar(
                select(func.coalesce(func.sum(ExpertAttachment.byte_size), 0)).where(
                    ExpertAttachment.user_id == self.owner.id,
                )
            ) or 0
            if (
                count >= MAX_ATTACHMENTS
                or conversation_size + len(data) > MAX_CONVERSATION_BYTES
                or account_size + len(data) > MAX_ACCOUNT_BYTES
            ):
                raise ConnectionProblem(
                    "expert-attachment-limit", 409,
                    "Anhangslimit erreicht. Bitte nicht benötigte Gespräche oder Dateien entfernen.",
                )

            now = _now()
            self.db.add(ExpertAttachmentIdentity(
                user_id=self.owner.id, attachment_id=identifier, created_at=now,
            ))
            row = ExpertAttachment(
                user_id=self.owner.id,
                id=identifier,
                conversation_id=conversation_id,
                file_name=name,
                media_type=media_type,
                byte_size=len(data),
                sha256=digest,
                data=data,
                created_at=now,
            )
            self.db.add(row)
            self.db.commit()
            return attachment_view(row), True

    def delete(
        self, conversation_id: UUID, identifier: UUID, *, _operation_locked: bool = False,
    ) -> None:
        context = (
            nullcontext()
            if _operation_locked
            else exclusive_user_lifecycle_operation(self.db, self.owner.id)
        )
        with context:
            self._active()
            self.conversation(conversation_id)
            row = self.db.get(
                ExpertAttachment, (self.owner.id, identifier), populate_existing=True,
            )
            identity = self.db.get(
                ExpertAttachmentIdentity, (self.owner.id, identifier), populate_existing=True,
            )
            if row is None:
                if identity is None:
                    self.db.add(ExpertAttachmentIdentity(
                        user_id=self.owner.id, attachment_id=identifier, created_at=_now(),
                    ))
                    self.db.commit()
                return
            if row.conversation_id != conversation_id:
                raise ConnectionProblem("expert-attachment-not-found", 404, "Anhang nicht gefunden.")
            references = self.db.scalars(
                select(ExpertRun.attachment_ids).where(
                    ExpertRun.user_id == self.owner.id,
                    ExpertRun.conversation_id == conversation_id,
                )
            )
            if any(str(identifier) in (values or []) for values in references):
                raise ConnectionProblem(
                    "expert-attachment-in-use", 409,
                    "Verwendete Anhänge werden zusammen mit dem Gespräch gelöscht.",
                )
            # The identity receipt is retained from upload. Keep this defensive
            # branch for rows inserted by legacy tooling or a manual migration.
            if identity is None:
                self.db.add(ExpertAttachmentIdentity(
                    user_id=self.owner.id, attachment_id=identifier, created_at=_now(),
                ))
            self.db.delete(row)
            self.db.commit()

    def drafts(self, conversation_id: UUID) -> tuple[AttachmentView, ...]:
        self._active()
        self.conversation(conversation_id)
        selected_for_active_or_successful_run = {
            value
            for values in self.db.scalars(
                select(ExpertRun.attachment_ids).where(
                    ExpertRun.user_id == self.owner.id,
                    ExpertRun.conversation_id == conversation_id,
                    ExpertRun.state.in_(("queued", "running", "succeeded")),
                )
            )
            for value in (values or [])
        }
        rows = self.db.scalars(
            select(ExpertAttachment)
            .options(defer(ExpertAttachment.data))
            .where(
                ExpertAttachment.user_id == self.owner.id,
                ExpertAttachment.conversation_id == conversation_id,
            )
            .order_by(ExpertAttachment.created_at, ExpertAttachment.id)
        )
        return tuple(
            attachment_view(row)
            for row in rows
            if str(row.id) not in selected_for_active_or_successful_run
        )

    def generation_media(
        self, conversation_id: UUID, current: tuple[UUID, ...],
    ) -> tuple[AIMedia, ...]:
        # Only explicitly submitted files and files from successful document
        # turns are retained in model context. Failed/cancelled files stay drafts.
        identifiers = list(current)
        for values in self.db.scalars(
            select(ExpertRun.attachment_ids)
            .where(
                ExpertRun.user_id == self.owner.id,
                ExpertRun.conversation_id == conversation_id,
                ExpertRun.state == "succeeded",
            )
            .order_by(ExpertRun.created_at, ExpertRun.id)
        ):
            for value in values or []:
                attachment_id = UUID(value)
                if attachment_id not in identifiers:
                    identifiers.append(attachment_id)
        rows = self.rows(conversation_id, tuple(identifiers))
        if sum(row.byte_size for row in rows) > MAX_CONVERSATION_BYTES:
            raise ConnectionProblem(
                "expert-attachment-limit", 409,
                "Die Anhänge überschreiten das Anfragelimit.",
            )
        return tuple(
            AIMedia(
                id=str(row.id),
                file_name=row.file_name,
                media_type=row.media_type,
                data_base64=base64.b64encode(row.data).decode("ascii"),
            )
            for row in rows
        )
