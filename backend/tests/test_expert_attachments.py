"""Synthetic file ownership, consent, replay and bounded dispatch regressions."""

import base64
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.expert.runs as runs
from app.ai.consents import AIConsentService
from app.ai.consents_contracts import AIConsentPut
from app.ai.contracts import AIMedia, AIMessage, GenerationRequest, ProviderKind
from app.ai.generation import generate
from app.database import SessionLocal
from app.expert.attachments import MAX_ATTACHMENT_BYTES, AttachmentService, validate_file
from app.expert.chat_contracts import ConversationPut
from app.models import ExpertAttachment, ExpertAttachmentIdentity, User
from tests.test_ai_connections import login
from tests.test_expert_runs import _context, _prepare, _success

PDF = b"%PDF-1.4\n% synthetic test fixture only\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\nsynthetic-test"


def test_file_replay_owner_conversation_limits_and_cascade(db: Session, user: User) -> None:
    service = runs.ExpertRunService(db, owner=user)
    conversation_id = uuid4()
    service.put_conversation(conversation_id, ConversationPut(mode="chat"))
    attachments = AttachmentService(db, owner=user)
    identifier = uuid4()
    saved, created = attachments.put(conversation_id, identifier, name="../synthetic.pdf", media_type="application/pdf", data=PDF)
    assert created and saved.file_name == "synthetic.pdf"
    assert not attachments.put(conversation_id, identifier, name="../synthetic.pdf", media_type="application/pdf", data=PDF)[1]
    with pytest.raises(ValueError):
        attachments.put(conversation_id, identifier, name="different.pdf", media_type="application/pdf", data=PDF)
    other_conversation = uuid4()
    service.put_conversation(other_conversation, ConversationPut(mode="chat"))
    with pytest.raises(ValueError):
        attachments.rows(other_conversation, (identifier,))
    other = User(username="synthetic-attachment-other", password_hash=user.password_hash, timezone="UTC")
    db.add(other)
    db.commit()
    with pytest.raises(ValueError):
        AttachmentService(db, owner=other).rows(conversation_id, (identifier,))
    assert len(attachments.drafts(conversation_id)) == 1
    attachments.put(conversation_id, uuid4(), name="second.png", media_type="image/png", data=PNG)
    attachments.put(conversation_id, uuid4(), name="third.pdf", media_type="application/pdf", data=PDF)
    with pytest.raises(ValueError):
        attachments.put(conversation_id, uuid4(), name="fourth.pdf", media_type="application/pdf", data=PDF)
    service.delete_conversation(conversation_id)
    assert db.scalar(select(func.count()).select_from(ExpertAttachment)) == 0


@pytest.mark.parametrize("data,media", [(b"<svg>untrusted</svg>", "image/svg+xml"), (PDF, "image/png"), (b"", "application/pdf"), (b"%PDF-" + b"x" * MAX_ATTACHMENT_BYTES, "application/pdf")])
def test_rejects_disallowed_or_oversized_media_without_parsing(data: bytes, media: str) -> None:
    with pytest.raises(ValueError):
        validate_file(data, media)


def test_upload_api_auth_csrf_binary_replay_and_no_external_send(client: TestClient, db: Session, user: User, monkeypatch) -> None:
    conversation_id, identifier = uuid4(), uuid4()
    service = runs.ExpertRunService(db, owner=user)
    service.put_conversation(conversation_id, ConversationPut(mode="chat"))
    path = f"/api/v1/expert/conversations/{conversation_id}/attachments/{identifier}"
    assert client.put(path, content=PDF).status_code == 401
    csrf = login(client)
    assert client.put(path, content=PDF).status_code == 403
    monkeypatch.setattr(runs, "generate", lambda *a, **k: pytest.fail("local file storage must not contact a provider"))
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/pdf", "X-Attachment-Filename": "synthetic-report.pdf"}
    saved = client.put(path, content=PDF, headers=headers)
    assert saved.status_code == 201
    assert saved.json()["byte_size"] == len(PDF)
    assert "data" not in saved.json() and "sha256" not in saved.json() and "user_id" not in saved.json()
    assert client.put(path, content=PDF, headers=headers).status_code == 200
    listed = client.get(f"/api/v1/expert/conversations/{conversation_id}/attachments")
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert client.put(path, content=PDF, headers={**headers, "Content-Length": str(MAX_ATTACHMENT_BYTES + 1)}).status_code == 413
    assert client.delete(path, headers={"X-CSRF-Token": csrf}).status_code == 204

    unknown_id = uuid4()
    unknown_path = f"/api/v1/expert/conversations/{conversation_id}/attachments/{unknown_id}"
    assert client.delete(unknown_path, headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.put(unknown_path, content=PDF, headers=headers).status_code == 409


@pytest.mark.parametrize("deleted_resource", ["conversation", "attachment"])
def test_streamed_upload_cannot_resurrect_deleted_resource(
    client: TestClient, db: Session, user: User, deleted_resource: str,
) -> None:
    conversation_id, attachment_id = uuid4(), uuid4()
    runs.ExpertRunService(db, owner=user).put_conversation(
        conversation_id, ConversationPut(mode="chat"),
    )
    csrf = login(client)
    path = f"/api/v1/expert/conversations/{conversation_id}/attachments/{attachment_id}"
    headers = {
        "X-CSRF-Token": csrf,
        "Content-Type": "application/pdf",
        "X-Attachment-Filename": "synthetic.pdf",
    }

    def delete_during_body():
        # The upload route must not hold a user lock or DB transaction while it
        # waits for this streamed body. The final owner/conversation check wins.
        with SessionLocal() as concurrent_db:
            concurrent_owner = concurrent_db.get(User, user.id)
            assert concurrent_owner is not None
            if deleted_resource == "conversation":
                runs.ExpertRunService(concurrent_db, owner=concurrent_owner).delete_conversation(
                    conversation_id,
                )
            else:
                AttachmentService(concurrent_db, owner=concurrent_owner).delete(
                    conversation_id, attachment_id,
                )
        yield PDF

    response = client.put(path, content=delete_during_body(), headers=headers)
    assert response.status_code == (404 if deleted_resource == "conversation" else 409)
    assert db.get(ExpertAttachment, (user.id, attachment_id)) is None


def test_document_permission_checked_again_at_dispatch_and_kept_for_followups(db: Session, user: User, monkeypatch) -> None:
    conversation_id, payload, _connection = _prepare(db, user)
    identifier = uuid4()
    AttachmentService(db, owner=user).put(conversation_id, identifier, name="synthetic.pdf", media_type="application/pdf", data=PDF)
    service = runs.ExpertRunService(db, owner=user)
    with pytest.raises(ValueError):
        service.put_run(conversation_id, uuid4(), payload.model_copy(update={"attachment_ids": (identifier,)}))
    grant = AIConsentService(db, owner=user).get(payload.consent_id)
    put = AIConsentPut(connection_id=grant.connection_id, configuration_version=grant.configuration_version, purpose=grant.purpose, data_domains=grant.data_domains, include_meal_names=grant.include_meal_names, include_attachments=True, history=grant.history, policy_version=grant.policy_version)
    permission, _ = AIConsentService(db, owner=user).put(uuid4(), put)
    document_payload = payload.model_copy(update={"attachment_ids": (identifier,), "consent_id": permission.id})
    run_id = uuid4()
    service.put_run(conversation_id, run_id, document_payload)
    assert not AttachmentService(db, owner=user).drafts(conversation_id)
    with pytest.raises(ValueError):
        AttachmentService(db, owner=user).delete(conversation_id, identifier)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *a, **k: type("Builder", (), {"build": lambda self, _prefs: _context()})())
    calls = []
    def generated(*a, **kwargs):
        calls.append(kwargs["request"])
        return _success(json.dumps({"text": "A synthetic document was supplied.", "findings": [{"kind": "observation", "text": "Unverified uploaded report.", "evidence_ids": ["attachment:" + str(identifier)]}]}))
    monkeypatch.setattr(runs, "generate", generated)
    runs.execute_run(user.id, run_id)
    assert service.get_run(run_id).state == "succeeded"
    assert base64.b64decode(calls[0].messages[-1].attachments[0].data_base64) == PDF
    assert "even though they are not in CALOGRAPH_CONTEXT" in calls[0].system_instruction
    assert "CALOGRAPH_ATTACHMENT:<id>" in calls[0].system_instruction
    followup_id = uuid4()
    with pytest.raises(ValueError):
        service.put_run(conversation_id, followup_id, payload)
    service.put_run(conversation_id, followup_id, payload.model_copy(update={"consent_id": permission.id}))
    AIConsentService(db, owner=user).revoke(permission.id)
    runs.execute_run(user.id, followup_id)
    assert service.get_run(followup_id).state == "failed" and len(calls) == 1


@pytest.mark.parametrize("kind", [ProviderKind.OPENAI_API, ProviderKind.ANTHROPIC, ProviderKind.GEMINI, ProviderKind.LOCAL, ProviderKind.GATEWAY])
def test_media_uses_inline_provider_contracts_without_remote_file_urls(kind: ProviderKind, monkeypatch) -> None:
    from pydantic import SecretStr
    calls = []
    def post(*a, **kwargs):
        calls.append(json.loads(kwargs["request_body"]))
        if kind == ProviderKind.ANTHROPIC:
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
        if kind == ProviderKind.GEMINI:
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    monkeypatch.setattr("app.ai.generation.fetch_provider_json", post)
    media = tuple(AIMedia(id=str(uuid4()), file_name=name, media_type=mime, data_base64=base64.b64encode(data).decode()) for name, mime, data in [("synthetic.pdf", "application/pdf", PDF), ("synthetic.png", "image/png", PNG)])
    generate(kind, base_url="http://synthetic.invalid/v1", key=SecretStr("synthetic-test-only"), request=GenerationRequest(system_instruction="test", messages=(AIMessage(role="user", content="test", attachments=media),), model_id="synthetic-model"), timeout_seconds=30)
    body = calls[0]
    content = body["contents"][0]["parts"] if kind == ProviderKind.GEMINI else body["messages"][-1]["content"]
    assert len(content) == 5
    assert "CALOGRAPH_ATTACHMENT:" in json.dumps(content)
    if kind == ProviderKind.GEMINI:
        assert content[2]["inlineData"]["mimeType"] == "application/pdf"
    elif kind == ProviderKind.ANTHROPIC:
        assert content[2]["type"] == "document" and content[4]["type"] == "image"
    else:
        assert content[2]["file"]["file_data"].startswith("data:application/pdf;base64,")
        assert content[4]["image_url"]["url"].startswith("data:image/png;base64,")


def test_upload_slots_are_bounded_and_release_on_errors() -> None:
    from app.ai.connections import ConnectionProblem
    from app.api.expert_attachments import upload_slot
    owner = uuid4()
    with upload_slot(owner):
        with pytest.raises(ConnectionProblem) as error, upload_slot(owner):
            pytest.fail("duplicate upload slot must not be acquired")
        assert error.value.status == 429
        with upload_slot(uuid4()), upload_slot(uuid4()), upload_slot(uuid4()), pytest.raises(ConnectionProblem), upload_slot(uuid4()):
            pytest.fail("global upload cap must be enforced")
    with upload_slot(owner):
        pass


def test_chunked_upload_is_bounded_and_account_deactivation_wins(client: TestClient, db: Session, user: User) -> None:
    from datetime import UTC, datetime
    conversation_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_conversation(conversation_id, ConversationPut(mode="chat"))
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/pdf", "X-Attachment-Filename": "synthetic.pdf"}
    path = f"/api/v1/expert/conversations/{conversation_id}/attachments/{uuid4()}"
    def oversized():
        yield PDF
        yield b"x" * MAX_ATTACHMENT_BYTES
    assert client.put(path, content=oversized(), headers=headers).status_code == 413
    def deactivate():
        user.is_active = False
        user.deactivated_at = datetime.now(UTC)
        db.commit()
        yield PDF
    assert client.put(path, content=deactivate(), headers=headers).status_code in (401, 409)
    assert db.scalar(select(func.count()).select_from(ExpertAttachment)) == 0


def test_cancelled_documents_require_explicit_reuse_and_can_be_sent_again_without_reupload(db: Session, user: User, monkeypatch) -> None:
    conversation_id, payload, _ = _prepare(db, user)
    service = AttachmentService(db, owner=user)
    identifiers = tuple(uuid4() for _ in range(3))
    for index, identifier in enumerate(identifiers):
        service.put(conversation_id, identifier, name=f"synthetic-{index}.pdf", media_type="application/pdf", data=PDF)
    previous = AIConsentService(db, owner=user).get(payload.consent_id)
    permission, _ = AIConsentService(db, owner=user).put(uuid4(), AIConsentPut(
        connection_id=previous.connection_id, configuration_version=previous.configuration_version,
        purpose=previous.purpose, data_domains=previous.data_domains, include_meal_names=previous.include_meal_names,
        include_attachments=True, history=previous.history, policy_version=previous.policy_version,
    ))
    request = payload.model_copy(update={"attachment_ids": identifiers, "consent_id": permission.id})
    runs_service = runs.ExpertRunService(db, owner=user)
    original, retried = uuid4(), uuid4()
    runs_service.put_run(conversation_id, original, request)
    runs_service.cancel(original)
    assert service.generation_media(conversation_id, ()) == ()
    assert {item.id for item in service.drafts(conversation_id)} == set(identifiers)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *a, **k: type("Builder", (), {"build": lambda self, _prefs: _context()})())
    calls = []
    def generated(*a, **kwargs):
        calls.append(kwargs["request"])
        return _success()
    monkeypatch.setattr(runs, "generate", generated)
    runs.execute_run(user.id, original)
    assert calls == []
    runs_service.put_run(conversation_id, retried, request)
    runs.execute_run(user.id, retried)
    assert runs_service.get_run(retried).state == "succeeded"
    assert len(calls[0].messages[-1].attachments) == 3
    assert db.scalar(select(func.count()).select_from(ExpertAttachment)) == 3


def test_attachment_delete_reserves_unknown_and_deleted_ids_and_is_account_scoped(db: Session, user: User) -> None:
    service = runs.ExpertRunService(db, owner=user)
    first, second = uuid4(), uuid4()
    service.put_conversation(first, ConversationPut(mode="chat"))
    service.put_conversation(second, ConversationPut(mode="chat"))
    attachments = AttachmentService(db, owner=user)
    unknown = uuid4()
    attachments.delete(first, unknown)
    with pytest.raises(ValueError):
        attachments.put(first, unknown, name="synthetic.pdf", media_type="application/pdf", data=PDF)
    identifier = uuid4()
    attachments.put(first, identifier, name="synthetic.pdf", media_type="application/pdf", data=PDF)
    attachments.delete(first, identifier)
    with pytest.raises(ValueError):
        attachments.put(first, identifier, name="synthetic.pdf", media_type="application/pdf", data=PDF)
    with pytest.raises(ValueError):
        attachments.put(second, identifier, name="synthetic.pdf", media_type="application/pdf", data=PDF)

    stranger = User(username="attachment-receipt-other", password_hash="synthetic-hash", timezone="UTC")
    db.add(stranger)
    db.commit()
    runs.ExpertRunService(db, owner=stranger).put_conversation(first, ConversationPut(mode="chat"))
    created, fresh = AttachmentService(db, owner=stranger).put(
        first, identifier, name="synthetic.pdf", media_type="application/pdf", data=PDF
    )
    assert fresh and created.id == identifier
    assert db.get(ExpertAttachmentIdentity, (user.id, unknown)) is not None


def test_attachment_and_identity_commit_atomically_after_flush(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = uuid4()
    runs.ExpertRunService(db, owner=user).put_conversation(
        conversation, ConversationPut(mode="chat"),
    )
    identifier = uuid4()
    original_commit = db.commit

    def fail_after_flush() -> None:
        db.flush()
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(db, "commit", fail_after_flush)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        AttachmentService(db, owner=user).put(
            conversation, identifier, name="synthetic.pdf",
            media_type="application/pdf", data=PDF,
        )
    db.rollback()
    monkeypatch.setattr(db, "commit", original_commit)
    assert db.get(ExpertAttachment, (user.id, identifier)) is None
    assert db.get(ExpertAttachmentIdentity, (user.id, identifier)) is None
    assert AttachmentService(db, owner=user).put(
        conversation, identifier, name="synthetic.pdf",
        media_type="application/pdf", data=PDF,
    )[1]


def test_run_replay_keeps_pre_attachment_empty_payload_hash(db: Session, user: User) -> None:
    conversation_id, payload, _ = _prepare(db, user)
    run_id = uuid4()
    service = runs.ExpertRunService(db, owner=user)
    # Recreate the original wire shape independently of newer optional fields.
    legacy_payload = {
        "kind": payload.kind,
        "message": payload.message,
        "connection_id": str(payload.connection_id),
        "connection_version": payload.connection_version,
        "preferences_version": payload.preferences_version,
        "consent_id": str(payload.consent_id),
    }
    original_hash = __import__("hashlib").sha256(
        json.dumps(legacy_payload, ensure_ascii=False, separators=(",", ":")).encode(),
    ).hexdigest()
    service.put_run(conversation_id, run_id, payload)
    stored = db.get(runs.ExpertRun, (user.id, run_id))
    assert stored is not None and stored.request_hash == original_hash
    assert service.put_run(conversation_id, run_id, payload.model_copy(update={"attachment_ids": ()}))[1] is False


@pytest.mark.parametrize("quota_scope", ["conversation", "account"])
def test_aggregate_byte_quota_rejection_does_not_consume_attachment_id(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, quota_scope: str,
) -> None:
    import app.expert.attachments as attachment_module
    from app.ai.connections import ConnectionProblem

    limit = 2 * len(PDF) - 1
    monkeypatch.setattr(
        attachment_module, "MAX_CONVERSATION_BYTES",
        limit if quota_scope == "conversation" else 4 * len(PDF),
    )
    monkeypatch.setattr(
        attachment_module, "MAX_ACCOUNT_BYTES",
        limit if quota_scope == "account" else 4 * len(PDF),
    )
    conversation_id, target_conversation = uuid4(), uuid4()
    conversations = runs.ExpertRunService(db, owner=user)
    conversations.put_conversation(conversation_id, ConversationPut(mode="chat"))
    if quota_scope == "conversation":
        target_conversation = conversation_id
    else:
        conversations.put_conversation(target_conversation, ConversationPut(mode="chat"))

    other = User(username="synthetic-quota-other", password_hash="synthetic-hash", timezone="UTC")
    db.add(other)
    db.commit()
    other_conversation = uuid4()
    runs.ExpertRunService(db, owner=other).put_conversation(
        other_conversation, ConversationPut(mode="chat"),
    )
    AttachmentService(db, owner=other).put(
        other_conversation, uuid4(), name="other.pdf", media_type="application/pdf", data=PDF,
    )

    service = AttachmentService(db, owner=user)
    first_id, rejected_id = uuid4(), uuid4()
    service.put(conversation_id, first_id, name="first.pdf", media_type="application/pdf", data=PDF)
    with pytest.raises(ConnectionProblem) as error:
        service.put(
            target_conversation, rejected_id, name="next.pdf", media_type="application/pdf", data=PDF,
        )
    assert error.value.code == "expert-attachment-limit"
    db.rollback()
    assert db.get(ExpertAttachmentIdentity, (user.id, rejected_id)) is None
    assert db.get(ExpertAttachment, (user.id, rejected_id)) is None

    service.delete(conversation_id, first_id)
    assert db.get(ExpertAttachmentIdentity, (user.id, first_id)) is not None
    saved, created = service.put(
        target_conversation, rejected_id, name="next.pdf", media_type="application/pdf", data=PDF,
    )
    assert created and saved.id == rejected_id
