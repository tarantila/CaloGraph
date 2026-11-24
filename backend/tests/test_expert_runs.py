from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.expert_runs as expert_runs_api
import app.expert.runs as runs
from app.ai.connections import ProviderConnectionService
from app.ai.connections_contracts import ProviderConnectionPut
from app.ai.consents import AIConsentService
from app.ai.consents_contracts import CONSENT_POLICY_VERSION, AIConsentPut, ConsentHistory
from app.ai.contracts import FinishReason, GenerationResult
from app.ai.preferences import AISettingsPut, AISettingsService
from app.database import SessionLocal
from app.expert.chat_contracts import ConversationPut, RunPut
from app.expert.preferences import ExpertPreferencesService
from app.expert.preferences_contracts import ExpertPreferencesPut
from app.models import (
    ExpertConversation,
    ExpertConversationIdentity,
    ExpertRun,
    ExpertRunIdentity,
    User,
)
from app.services.user_operation_lock import exclusive_user_lifecycle_operation
from tests.test_ai_connections import KEY, login


def _prepare(db: Session, owner: User, *, mode: str = "chat") -> tuple[object, RunPut, object]:
    connection_id = uuid4()
    connection, _ = ProviderConnectionService(db, owner=owner).put(
        connection_id,
        ProviderConnectionPut(
            label="Synthetic test provider", provider_kind="openai_api", model_id="synthetic-model",
            enabled=True, api_key=KEY,
        ),
    )
    AISettingsService(db, owner=owner).put(
        AISettingsPut(active_connection_id=connection_id, configuration_version=connection.configuration_version)
    )
    prefs = ExpertPreferencesService(db, owner=owner).get()
    consent, _ = AIConsentService(db, owner=owner).put(
        uuid4(),
        AIConsentPut(
            connection_id=connection_id, configuration_version=connection.configuration_version,
            purpose="nutrition_check_in" if mode == "analysis" else "nutrition_chat",
            data_domains=prefs.data_domains, include_meal_names=prefs.include_meal_names,
            history=ConsentHistory(mode="all_available", start=None, end=None),
            policy_version=CONSENT_POLICY_VERSION,
        ),
    )
    conversation_id = uuid4()
    service = runs.ExpertRunService(db, owner=owner)
    service.put_conversation(conversation_id, ConversationPut(mode=mode))
    payload = RunPut(
        kind=mode, message="Summarize my history", connection_id=connection_id,
        connection_version=connection.configuration_version,
        preferences_version=prefs.configuration_version, consent_id=consent.id,
    )
    return (conversation_id, payload, connection)


def _context(*, end: date = date(2026, 1, 2), evidence_ids: tuple[str, ...] = ("nutrition:2026-01",)):
    return SimpleNamespace(
        coverage_start=date(2020, 1, 1), coverage_end=end, evidence_ids=evidence_ids,
        model_dump=lambda **_kwargs: {
            "coverage_start": "2020-01-01", "coverage_end": end.isoformat(),
            "evidence_ids": list(evidence_ids), "synthetic": True,
        },
    )


def _success(content: str | None = None) -> GenerationResult:
    return GenerationResult(
        content=content or json.dumps({"text": "A grounded summary.", "findings": [{
            "kind": "observation", "text": "A recorded pattern.", "evidence_ids": ["nutrition:2026-01"],
        }]}),
        model_id="synthetic-model", finish_reason=FinishReason.SUCCESS,
    )


def _queued(db: Session, owner: User, *, mode: str = "chat") -> tuple[object, RunPut, object]:
    conversation_id, payload, _connection = _prepare(db, owner, mode=mode)
    identifier = uuid4()
    result, created = runs.ExpertRunService(db, owner=owner).put_run(conversation_id, identifier, payload)
    assert created and result.state == "queued"
    return (conversation_id, identifier, payload)


def test_runs_are_owner_scoped_and_request_replay_does_not_requeue(db: Session, user: User) -> None:
    conversation_id, identifier, payload = _queued(db, user)
    service = runs.ExpertRunService(db, owner=user)
    again, created = service.put_run(conversation_id, identifier, payload)
    assert not created and again.id == identifier and again.state == "queued"
    stranger = User(username="other", password_hash="synthetic-hash", timezone="Europe/Berlin")
    db.add(stranger)
    db.commit()
    with pytest.raises(Exception) as error:
        runs.ExpertRunService(db, owner=stranger).get_run(identifier)
    assert getattr(error.value, "code", None) == "expert-run-not-found"
    with pytest.raises(Exception) as error:
        runs.ExpertRunService(db, owner=stranger).get_conversation(conversation_id)
    assert getattr(error.value, "code", None) == "expert-conversation-not-found"


def test_run_replay_with_changed_body_conflicts(db: Session, user: User) -> None:
    conversation_id, identifier, payload = _queued(db, user)
    different = payload.model_copy(update={"message": "a different question"})
    with pytest.raises(Exception) as error:
        runs.ExpertRunService(db, owner=user).put_run(conversation_id, identifier, different)
    assert getattr(error.value, "code", None) == "expert-run-conflict"


def test_deleted_conversation_and_run_ids_cannot_be_replayed(db: Session, user: User) -> None:
    conversation_id, run_id, payload = _queued(db, user)
    service = runs.ExpertRunService(db, owner=user)
    service.delete_conversation(conversation_id)
    with pytest.raises(Exception) as error:
        service.put_conversation(conversation_id, ConversationPut(mode="chat"))
    assert getattr(error.value, "code", None) == "expert-conversation-retired"

    other_conversation = uuid4()
    service.put_conversation(other_conversation, ConversationPut(mode="chat"))
    with pytest.raises(Exception) as error:
        service.put_run(other_conversation, run_id, payload)
    assert getattr(error.value, "code", None) == "expert-run-retired"
    assert db.get(ExpertConversationIdentity, (user.id, conversation_id)) is not None
    assert db.get(ExpertRunIdentity, (user.id, run_id)) is not None


def test_unknown_conversation_delete_reserves_id_and_ids_are_owner_scoped(db: Session, user: User) -> None:
    identifier = uuid4()
    runs.ExpertRunService(db, owner=user).delete_conversation(identifier)
    with pytest.raises(Exception) as error:
        runs.ExpertRunService(db, owner=user).put_conversation(
            identifier, ConversationPut(mode="analysis")
        )
    assert getattr(error.value, "code", None) == "expert-conversation-retired"

    stranger = User(username="other", password_hash="synthetic-hash", timezone="Europe/Berlin")
    db.add(stranger)
    db.commit()
    created, fresh = runs.ExpertRunService(db, owner=stranger).put_conversation(
        identifier, ConversationPut(mode="chat")
    )
    assert fresh and created.id == identifier


def test_run_identity_uuid_is_independent_per_account(db: Session, user: User) -> None:
    first_conversation, first_payload, _ = _prepare(db, user)
    identifier = uuid4()
    assert runs.ExpertRunService(db, owner=user).put_run(
        first_conversation, identifier, first_payload
    )[1]

    stranger = User(username="other", password_hash="synthetic-hash", timezone="Europe/Berlin")
    db.add(stranger)
    db.commit()
    second_conversation, second_payload, _ = _prepare(db, stranger)
    created, is_new = runs.ExpertRunService(db, owner=stranger).put_run(
        second_conversation, identifier, second_payload
    )
    assert is_new and created.id == identifier


def test_conversation_identity_and_resource_commit_atomically(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = uuid4()
    service = runs.ExpertRunService(db, owner=user)
    original_commit = db.commit

    def fail_commit() -> None:
        db.flush()
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        service.put_conversation(identifier, ConversationPut(mode="chat"))
    db.rollback()
    monkeypatch.setattr(db, "commit", original_commit)

    assert db.get(ExpertConversationIdentity, (user.id, identifier)) is None
    assert db.get(ExpertConversation, (user.id, identifier)) is None
    assert runs.ExpertRunService(db, owner=user).put_conversation(
        identifier, ConversationPut(mode="chat")
    )[1]
    assert db.get(ExpertConversationIdentity, (user.id, identifier)) is not None

    conversation_id, payload, _ = _prepare(db, user)
    run_id = uuid4()
    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        service.put_run(conversation_id, run_id, payload)
    db.rollback()
    monkeypatch.setattr(db, "commit", original_commit)

    assert db.get(ExpertRunIdentity, (user.id, run_id)) is None
    assert db.get(ExpertRun, (user.id, run_id)) is None
    assert runs.ExpertRunService(db, owner=user).put_run(
        conversation_id, run_id, payload
    )[1]


def test_execute_run_dispatches_after_context_transaction_closes(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _conversation_id, identifier, _payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    seen: list[bool] = []

    def generate(*_args, **_kwargs):
        # A second account-level exclusive lifecycle operation proves no shared
        # dispatch lock or read transaction is retained across provider I/O.
        with SessionLocal() as session, exclusive_user_lifecycle_operation(session, user.id):
            assert session.scalar(select(ExpertRun.id).where(ExpertRun.id == identifier)) == identifier
        seen.append(True)
        return _success()

    monkeypatch.setattr(runs, "generate", generate)
    runs.execute_run(user.id, identifier)
    runs.execute_run(user.id, identifier)
    assert seen == [True]
    result = runs.ExpertRunService(db, owner=user).get_run(identifier)
    assert result.state == "succeeded" and result.answer is not None
    assert result.coverage_end == date(2026, 1, 2)


@pytest.mark.parametrize("change", ["preferences", "consent", "connection"])
def test_final_dispatch_rechecks_authorization_after_context_build(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    _conversation_id, identifier, payload = _queued(db, user)

    def mutate(_prefs):
        if change == "preferences":
            ExpertPreferencesService(db, owner=user).put(ExpertPreferencesPut(data_domains=("nutrition",), include_meal_names=True))
        elif change == "consent":
            AIConsentService(db, owner=user).revoke(payload.consent_id)
        else:
            ProviderConnectionService(db, owner=user).put(payload.connection_id, ProviderConnectionPut(
                label="Changed provider", provider_kind="openai_api", model_id="synthetic-model", enabled=True,
            ))
        return _context()

    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=mutate))
    calls = []
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: calls.append(True))
    runs.execute_run(user.id, identifier)
    result = runs.ExpertRunService(db, owner=user).get_run(identifier)
    assert result.state == "failed" and result.error_code == "authorization_changed"
    assert calls == []


def test_cancel_during_context_prevents_dispatch(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _conversation_id, identifier, _payload = _queued(db, user)

    def cancel(_prefs):
        with SessionLocal() as session:
            owner = session.get(User, user.id)
            assert owner is not None
            runs.ExpertRunService(session, owner=owner).cancel(identifier)
        return _context()

    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=cancel))
    calls = []
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: calls.append(True))
    runs.execute_run(user.id, identifier)
    assert runs.ExpertRunService(db, owner=user).get_run(identifier).state == "cancelled"
    assert calls == []


def test_cancel_during_provider_call_discards_answer(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _conversation_id, identifier, _payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))

    def cancel_then_reply(*_args, **_kwargs):
        with SessionLocal() as session:
            owner = session.get(User, user.id)
            assert owner is not None
            runs.ExpertRunService(session, owner=owner).cancel(identifier)
        return _success()

    monkeypatch.setattr(runs, "generate", cancel_then_reply)
    runs.execute_run(user.id, identifier)
    result = runs.ExpertRunService(db, owner=user).get_run(identifier)
    assert result.state == "cancelled" and result.answer is None


def test_unknown_answer_evidence_ids_are_rejected(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _conversation_id, identifier, _payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    forged = json.dumps({"text": "Unsupported", "findings": [{
        "kind": "observation", "text": "Unknown evidence.", "evidence_ids": ["private:unknown"],
    }]})
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(forged))
    runs.execute_run(user.id, identifier)
    result = runs.ExpertRunService(db, owner=user).get_run(identifier)
    assert result.state == "failed" and result.error_code == "invalid_evidence" and result.answer is None


def test_analysis_anchor_uses_previous_analysis_not_newer_chat(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    # A completed analysis in another conversation is the durable coverage anchor.
    old_conversation, old_payload, _ = _prepare(db, user, mode="analysis")
    previous = ExpertRun(
        user_id=user.id, id=uuid4(), conversation_id=old_conversation,
        request_hash="a" * 64, kind="analysis", message="old", connection_id=old_payload.connection_id,
        connection_version=old_payload.connection_version, preferences_version=old_payload.preferences_version,
        consent_id=old_payload.consent_id, provider_kind="openai_api", model_id="synthetic-model",
        state="succeeded", created_at=datetime(2026, 1, 1, tzinfo=UTC), claimed_at=datetime(2026, 1, 1, tzinfo=UTC),
        dispatched_at=datetime(2026, 1, 1, tzinfo=UTC), finished_at=datetime(2026, 1, 1, tzinfo=UTC),
        coverage_start=date(2020, 1, 1), coverage_end=date(2025, 12, 31), answer={"text": "old", "findings": []},
    )
    chat_conversation, chat_payload, _ = _prepare(db, user, mode="chat")
    newer_chat = ExpertRun(
        user_id=user.id, id=uuid4(), conversation_id=chat_conversation,
        request_hash="b" * 64, kind="chat", message="new chat", connection_id=chat_payload.connection_id,
        connection_version=chat_payload.connection_version, preferences_version=chat_payload.preferences_version,
        consent_id=chat_payload.consent_id, provider_kind="openai_api", model_id="synthetic-model",
        state="succeeded", created_at=datetime(2026, 1, 2, tzinfo=UTC), claimed_at=datetime(2026, 1, 2, tzinfo=UTC),
        dispatched_at=datetime(2026, 1, 2, tzinfo=UTC), finished_at=datetime(2026, 1, 2, tzinfo=UTC),
        coverage_start=date(2020, 1, 1), coverage_end=date(2026, 1, 1), answer={"text": "new", "findings": []},
    )
    db.add_all([previous, newer_chat])
    db.commit()
    analysis_conversation, analysis_payload, _ = _prepare(db, user, mode="analysis")
    identifier = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(analysis_conversation, identifier, analysis_payload)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context(end=date(2026, 1, 3))))
    captured = []
    monkeypatch.setattr(runs, "generate", lambda _kind, **kwargs: captured.append(kwargs["request"].system_instruction) or _success())
    runs.execute_run(user.id, identifier)
    assert len(captured) == 1
    assert '"previous_check_in_coverage_end": "2025-12-31"' in captured[0]
    assert "2026-01-01" not in captured[0]


def test_turn_endpoint_background_task_runs_after_exclusive_dependency_releases(
    client, db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = login(client)
    conversation_id, payload, _connection = _prepare(db, user)
    called = []

    def background(owner_id, _run_id):
        with SessionLocal() as session, exclusive_user_lifecycle_operation(session, owner_id):
            called.append(True)

    monkeypatch.setattr(expert_runs_api, "execute_run", background)
    response = client.put(
        f"/api/v1/expert/conversations/{conversation_id}/turns/{uuid4()}",
        headers={"X-CSRF-Token": csrf},
        json={
            "kind": "chat", "message": "hello", "connection_id": str(payload.connection_id),
            "connection_version": payload.connection_version, "preferences_version": payload.preferences_version,
            "consent_id": str(payload.consent_id),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "queued"
    assert called == [True]
