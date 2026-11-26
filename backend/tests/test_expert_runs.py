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
from app.ai.routing import AIRoutingService
from app.ai.routing_contracts import AIRoutingEntryPut, AIRoutingPut
from app.database import SessionLocal
from app.expert.chat_contracts import ConversationPut, RunPut
from app.expert.preferences import ExpertPreferencesService
from app.expert.preferences_contracts import ExpertPreferencesPut
from app.models import (
    ExpertConversation,
    ExpertConversationIdentity,
    ExpertRun,
    ExpertRunIdentity,
    ExpertRunProviderAttempt,
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


def test_setup_reuses_document_grants_for_text_and_never_widens_text_grants(
    db: Session, user: User,
) -> None:
    _conversation_id, payload, primary = _prepare(db, user)
    preferences = ExpertPreferencesService(db, owner=user).get()
    primary_text = AIConsentService(db, owner=user).get(payload.consent_id)
    backup, _ = ProviderConnectionService(db, owner=user).put(
        uuid4(),
        ProviderConnectionPut(
            label="Synthetic fallback", provider_kind="anthropic", model_id="backup-model",
            enabled=True, api_key=KEY,
        ),
    )

    def grant(connection, *, documents: bool):
        consent, _ = AIConsentService(db, owner=user).put(
            uuid4(),
            AIConsentPut(
                connection_id=connection.id,
                configuration_version=connection.configuration_version,
                purpose="nutrition_chat",
                data_domains=preferences.data_domains,
                include_meal_names=preferences.include_meal_names,
                include_attachments=documents,
                history=ConsentHistory(mode="all_available", start=None, end=None),
                policy_version=CONSENT_POLICY_VERSION,
            ),
        )
        return consent

    backup_text = grant(backup, documents=False)
    routing = AIRoutingService(db, owner=user).get()
    AIRoutingService(db, owner=user).put(AIRoutingPut(
        expected_configuration_version=routing.configuration_version,
        entries=(
            AIRoutingEntryPut(connection_id=primary.id, enabled=True),
            AIRoutingEntryPut(connection_id=backup.id, enabled=True),
        ),
    ))
    service = runs.ExpertRunService(db, owner=user)

    document_setup = service.setup("chat", include_attachments=True)
    assert document_setup.consent is None
    assert document_setup.fallback_candidates[1].consent_id is None

    primary_documents = grant(primary, documents=True)
    backup_documents = grant(backup, documents=True)
    document_setup = service.setup("chat", include_attachments=True)
    assert document_setup.consent is not None and document_setup.consent.id == primary_documents.id
    assert document_setup.fallback_candidates[1].consent_id == backup_documents.id

    text_setup = service.setup("chat", include_attachments=False)
    assert text_setup.consent is not None and text_setup.consent.id == primary_documents.id
    assert text_setup.fallback_candidates[1].consent_id == backup_documents.id
    assert primary_text.include_attachments is False
    assert backup_text.include_attachments is False
    AIConsentService(db, owner=user).revoke(backup_documents.id)
    assert service.setup("chat", include_attachments=True).fallback_candidates[1].consent_id is None
    assert service.setup("chat", include_attachments=False).fallback_candidates[1].consent_id == backup_text.id


def test_setup_keeps_domain_purpose_revocation_and_configuration_scope_exact(
    db: Session, user: User,
) -> None:
    _conversation_id, payload, connection = _prepare(db, user)
    service = runs.ExpertRunService(db, owner=user)
    consent_service = AIConsentService(db, owner=user)
    assert service.setup("chat").consent is not None
    assert service.setup("analysis").consent is None

    preferences = ExpertPreferencesService(db, owner=user).get()
    ExpertPreferencesService(db, owner=user).put(ExpertPreferencesPut(
        data_domains=("nutrition",), include_meal_names=False,
    ))
    assert service.setup("chat").consent is None

    ExpertPreferencesService(db, owner=user).put(ExpertPreferencesPut(
        data_domains=preferences.data_domains, include_meal_names=False,
    ))
    assert service.setup("chat").consent is None

    consent_service.revoke(payload.consent_id)
    ExpertPreferencesService(db, owner=user).put(ExpertPreferencesPut(
        data_domains=preferences.data_domains,
        include_meal_names=preferences.include_meal_names,
    ))
    assert service.setup("chat").consent is None

    active, _ = consent_service.put(uuid4(), AIConsentPut(
        connection_id=connection.id,
        configuration_version=connection.configuration_version,
        purpose="nutrition_chat", data_domains=preferences.data_domains,
        include_meal_names=preferences.include_meal_names,
        history=ConsentHistory(mode="all_available", start=None, end=None),
        policy_version=CONSENT_POLICY_VERSION,
    ))
    assert service.setup("chat").consent.id == active.id
    replacement, _ = ProviderConnectionService(db, owner=user).put(
        connection.id,
        ProviderConnectionPut(
            label="Updated synthetic provider", provider_kind=connection.provider_kind,
            model_id=connection.model_id, enabled=True, api_key=KEY,
        ),
    )
    assert replacement.configuration_version > connection.configuration_version
    assert service.setup("chat").consent is None


def test_runs_are_owner_scoped_and_request_replay_does_not_requeue(db: Session, user: User) -> None:
    conversation_id, identifier, payload = _queued(db, user)
    service = runs.ExpertRunService(db, owner=user)
    again, created = service.put_run(conversation_id, identifier, payload)
    assert not created and again.id == identifier and again.state == "queued"
    own_conversation = db.get(ExpertConversation, (user.id, conversation_id))
    assert own_conversation is not None
    own_conversation.title, own_conversation.title_source = "Private generated title", "generated"
    stranger = User(
        username="other", password_hash="synthetic-hash", timezone="Europe/Berlin", is_admin=True,
    )
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


@pytest.mark.parametrize(("fallback_safe", "backup_consent_purpose", "expected_calls", "expected_state"), [
    (True, "nutrition_chat", 2, "succeeded"), (True, None, 1, "failed"),
    (True, "nutrition_check_in", 1, "failed"), (False, "nutrition_chat", 1, "failed"),
])
def test_opted_in_route_falls_back_only_after_safe_failure(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
    fallback_safe: bool, expected_calls: int, expected_state: str,
    backup_consent_purpose: str | None,
) -> None:
    from app.ai.connections import ProviderConnectionService
    from app.ai.connections_contracts import ProviderConnectionPut
    from app.ai.generation import GenerationFailure
    conversation_id, payload, primary = _prepare(db, user)
    backup_id = uuid4()
    backup, _ = ProviderConnectionService(db, owner=user).put(
        backup_id,
        ProviderConnectionPut(
            label="Synthetic backup", provider_kind="anthropic", model_id="backup-model",
            enabled=True, api_key=KEY,
        ),
    )
    preferences = ExpertPreferencesService(db, owner=user).get()
    backup_consent = None
    if backup_consent_purpose:
        backup_consent, _ = AIConsentService(db, owner=user).put(
            uuid4(),
            AIConsentPut(
                connection_id=backup_id, configuration_version=backup.configuration_version,
                purpose=backup_consent_purpose, data_domains=preferences.data_domains,
                include_meal_names=preferences.include_meal_names,
                history=ConsentHistory(mode="all_available", start=None, end=None),
                policy_version=CONSENT_POLICY_VERSION,
            ),
        )
        assert backup_consent.state == "active"
    routed = AIRoutingService(db, owner=user).put(AIRoutingPut(
        expected_configuration_version=0,
        entries=(
            AIRoutingEntryPut(connection_id=primary.id, enabled=True),
            AIRoutingEntryPut(connection_id=backup_id, enabled=True),
        ),
    ))
    assert routed.configuration_version == 1
    payload = payload.model_copy(update={"routing_configuration_version": 1})
    run_id = uuid4()
    created, is_new = runs.ExpertRunService(db, owner=user).put_run(conversation_id, run_id, payload)
    assert is_new and len(created.provider_attempts) == 2

    monkeypatch.setattr(
        runs, "ContextBuilder",
        lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()),
    )
    seen: list[tuple[str, str]] = []

    def generate(kind, **kwargs):
        seen.append((kind.value, kwargs["request"].model_id))
        if len(seen) == 1:
            raise GenerationFailure("unavailable", fallback_safe=fallback_safe)
        return _success()

    monkeypatch.setattr(runs, "generate", generate)
    runs.execute_run(user.id, run_id)
    result = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert result.state == expected_state
    assert len(seen) == expected_calls
    if fallback_safe and backup_consent_purpose == "nutrition_chat":
        assert seen == [("openai_api", "synthetic-model"), ("anthropic", "backup-model")]
        assert [(attempt.state, attempt.winner) for attempt in result.provider_attempts] == [
            ("failed", False), ("succeeded", True),
        ]
    else:
        assert [(attempt.state, attempt.winner) for attempt in result.provider_attempts] == [
            ("failed", False), ("skipped", False),
        ]
    attempts = list(db.scalars(select(ExpertRunProviderAttempt).where(
        ExpertRunProviderAttempt.user_id == user.id,
        ExpertRunProviderAttempt.run_id == run_id,
    ).order_by(ExpertRunProviderAttempt.position)))
    assert attempts[1].consent_id == (
        backup_consent.id
        if backup_consent is not None and backup_consent_purpose == "nutrition_chat"
        else None
    )
    stored = db.get(ExpertRun, (user.id, run_id))
    assert stored is not None and stored.connection_id == primary.id


@pytest.mark.parametrize(("change", "expected_state"), [
    ("revoke_backup", "failed"),
    ("change_route", "failed"),
    ("change_preferences", "failed"),
    ("cancel", "cancelled"),
])
def test_opted_in_route_revalidates_before_each_backup_dispatch(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
    change: str, expected_state: str,
) -> None:
    from app.ai.connections import ProviderConnectionService
    from app.ai.connections_contracts import ProviderConnectionPut
    from app.ai.generation import GenerationFailure

    conversation_id, payload, primary = _prepare(db, user)
    backup_id = uuid4()
    backup, _ = ProviderConnectionService(db, owner=user).put(
        backup_id,
        ProviderConnectionPut(
            label="Synthetic backup", provider_kind="anthropic", model_id="backup-model",
            enabled=True, api_key=KEY,
        ),
    )
    preferences = ExpertPreferencesService(db, owner=user).get()
    backup_consent, _ = AIConsentService(db, owner=user).put(
        uuid4(),
        AIConsentPut(
            connection_id=backup_id, configuration_version=backup.configuration_version,
            purpose="nutrition_chat", data_domains=preferences.data_domains,
            include_meal_names=preferences.include_meal_names,
            history=ConsentHistory(mode="all_available", start=None, end=None),
            policy_version=CONSENT_POLICY_VERSION,
        ),
    )
    AIRoutingService(db, owner=user).put(AIRoutingPut(
        expected_configuration_version=0,
        entries=(
            AIRoutingEntryPut(connection_id=primary.id, enabled=True),
            AIRoutingEntryPut(connection_id=backup_id, enabled=True),
        ),
    ))
    payload = payload.model_copy(update={"routing_configuration_version": 1})
    run_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, run_id, payload)
    monkeypatch.setattr(
        runs, "ContextBuilder",
        lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()),
    )
    seen: list[str] = []

    def generate(kind, **_kwargs):
        seen.append(kind.value)
        if len(seen) == 1:
            with SessionLocal() as session:
                current_owner = session.get(User, user.id)
                assert current_owner is not None
                if change == "revoke_backup":
                    AIConsentService(session, owner=current_owner).revoke(backup_consent.id)
                elif change == "change_route":
                    AIRoutingService(session, owner=current_owner).put(AIRoutingPut(
                        expected_configuration_version=1,
                        entries=(
                            AIRoutingEntryPut(connection_id=backup_id, enabled=True),
                            AIRoutingEntryPut(connection_id=primary.id, enabled=True),
                        ),
                    ))
                elif change == "change_preferences":
                    ExpertPreferencesService(session, owner=current_owner).put(
                        ExpertPreferencesPut(
                            data_domains=preferences.data_domains,
                            include_meal_names=not preferences.include_meal_names,
                        )
                    )
                else:
                    runs.ExpertRunService(session, owner=current_owner).cancel(run_id)
            raise GenerationFailure("unavailable", fallback_safe=True)
        return _success()

    monkeypatch.setattr(runs, "generate", generate)
    runs.execute_run(user.id, run_id)
    result = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert seen == ["openai_api"]
    assert result.state == expected_state


def test_interrupted_opted_in_attempt_is_reaped_and_never_replayed(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    conversation_id, payload, primary = _prepare(db, user)
    routed = AIRoutingService(db, owner=user).put(AIRoutingPut(
        expected_configuration_version=0,
        entries=(AIRoutingEntryPut(connection_id=primary.id, enabled=True),),
    ))
    run_id = uuid4()
    payload = payload.model_copy(update={
        "routing_configuration_version": routed.configuration_version,
    })
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, run_id, payload)
    run = db.get(ExpertRun, (user.id, run_id))
    attempt = db.get(ExpertRunProviderAttempt, (user.id, run_id, 0))
    assert run is not None and attempt is not None
    run.state, run.claimed_at = "running", datetime.now(UTC) - timedelta(minutes=11)
    run.created_at = datetime.now(UTC) - timedelta(minutes=11)
    attempt.state, attempt.dispatched_at = "dispatching", datetime.now(UTC) - timedelta(minutes=11)
    db.commit()

    reaped = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert reaped.state == "interrupted"
    assert reaped.provider_attempts[0].state == "interrupted"
    calls: list[bool] = []
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: calls.append(True))
    runs.execute_run(user.id, run_id)
    assert calls == []


def test_fallback_candidate_without_attachment_scope_is_never_called(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai.connections import ProviderConnectionService
    from app.ai.connections_contracts import ProviderConnectionPut
    from app.ai.generation import GenerationFailure
    from app.expert.attachments import AttachmentService

    conversation_id, payload, primary = _prepare(db, user)
    preferences = ExpertPreferencesService(db, owner=user).get()
    primary_consent, _ = AIConsentService(db, owner=user).put(
        uuid4(),
        AIConsentPut(
            connection_id=primary.id, configuration_version=primary.configuration_version,
            purpose="nutrition_chat", data_domains=preferences.data_domains,
            include_meal_names=preferences.include_meal_names, include_attachments=True,
            history=ConsentHistory(mode="all_available", start=None, end=None),
            policy_version=CONSENT_POLICY_VERSION,
        ),
    )
    backup_id, attachment_id = uuid4(), uuid4()
    backup, _ = ProviderConnectionService(db, owner=user).put(
        backup_id,
        ProviderConnectionPut(
            label="Synthetic backup", provider_kind="anthropic", model_id="backup-model",
            enabled=True, api_key=KEY,
        ),
    )
    AIConsentService(db, owner=user).put(
        uuid4(),
        AIConsentPut(
            connection_id=backup_id, configuration_version=backup.configuration_version,
            purpose="nutrition_chat", data_domains=preferences.data_domains,
            include_meal_names=preferences.include_meal_names, include_attachments=False,
            history=ConsentHistory(mode="all_available", start=None, end=None),
            policy_version=CONSENT_POLICY_VERSION,
        ),
    )
    AttachmentService(db, owner=user).put(
        conversation_id, attachment_id, name="synthetic.pdf", media_type="application/pdf",
        data=b"%PDF-1.4\n% synthetic test fixture only\n%%EOF",
    )
    AIRoutingService(db, owner=user).put(AIRoutingPut(
        expected_configuration_version=0,
        entries=(
            AIRoutingEntryPut(connection_id=primary.id, enabled=True),
            AIRoutingEntryPut(connection_id=backup_id, enabled=True),
        ),
    ))
    payload = payload.model_copy(update={
        "consent_id": primary_consent.id,
        "attachment_ids": (attachment_id,),
        "routing_configuration_version": 1,
    })
    run_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, run_id, payload)
    monkeypatch.setattr(
        runs, "ContextBuilder",
        lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()),
    )
    seen: list[str] = []

    def generate(kind, **_kwargs):
        seen.append(kind.value)
        raise GenerationFailure("unavailable", fallback_safe=True)

    monkeypatch.setattr(runs, "generate", generate)
    runs.execute_run(user.id, run_id)
    result = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert result.state == "failed"
    assert seen == ["openai_api"]
    assert result.provider_attempts[1].state == "skipped"


def test_chat_title_is_generated_in_first_successful_request_once(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id, run_id, payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    calls: list[str] = []

    def generate(_kind, **kwargs):
        calls.append(kwargs["request"].system_instruction)
        return _success(json.dumps({
            "text": "A grounded summary.", "findings": [], "conversation_title": "Ballaststoffzufuhr im Verlauf",
        }))

    monkeypatch.setattr(runs, "generate", generate)
    runs.execute_run(user.id, run_id)
    first = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert first.state == "succeeded" and first.answer is not None
    assert first.answer.conversation_title == "Ballaststoffzufuhr im Verlauf"
    assert runs.ExpertRunService(db, owner=user).get_conversation(conversation_id).title == "Ballaststoffzufuhr im Verlauf"
    assert len(calls) == 1 and '"generate_conversation_title": true' in calls[0]

    second_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, second_id, payload)
    runs.execute_run(user.id, second_id)
    second = runs.ExpertRunService(db, owner=user).get_run(second_id)
    assert second.answer is not None and second.answer.conversation_title is None
    assert runs.ExpertRunService(db, owner=user).get_conversation(conversation_id).title == "Ballaststoffzufuhr im Verlauf"
    assert len(calls) == 2 and '"generate_conversation_title": false' in calls[1]


@pytest.mark.parametrize("title", [None, 37, "  \n", "x" * 81, "\ud800"])
def test_invalid_provider_title_does_not_fail_answer_or_expose_legacy_title(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
    title: object,
) -> None:
    conversation_id, run_id, _payload = _queued(db, user)
    conversation = db.get(ExpertConversation, (user.id, conversation_id))
    assert conversation is not None
    conversation.title = "legacy first message excerpt"
    db.commit()
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(json.dumps({
        "text": "Valid answer.", "findings": [], "conversation_title": title,
    })))
    runs.execute_run(user.id, run_id)
    result = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert result.state == "succeeded" and result.answer is not None
    assert result.answer.conversation_title is None
    assert runs.ExpertRunService(db, owner=user).get_conversation(conversation_id).title is None
    stored = db.get(ExpertConversation, (user.id, conversation_id))
    assert stored is not None and stored.title == "legacy first message excerpt" and stored.title_source is None


def test_analysis_conversation_never_generates_title_even_for_chat_followup(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id, analysis_payload, _ = _prepare(db, user, mode="analysis")
    connection = ProviderConnectionService(db, owner=user).get(analysis_payload.connection_id)
    prefs = ExpertPreferencesService(db, owner=user).get()
    chat_consent, _ = AIConsentService(db, owner=user).put(
        uuid4(),
        AIConsentPut(
            connection_id=analysis_payload.connection_id,
            configuration_version=connection.configuration_version,
            purpose="nutrition_chat", data_domains=prefs.data_domains,
            include_meal_names=prefs.include_meal_names,
            history=ConsentHistory(mode="all_available", start=None, end=None),
            policy_version=CONSENT_POLICY_VERSION,
        ),
    )
    chat_payload = analysis_payload.model_copy(update={"kind": "chat", "consent_id": chat_consent.id})
    run_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, run_id, chat_payload)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    calls: list[str] = []

    def generate(_kind, **kwargs):
        calls.append(kwargs["request"].system_instruction)
        return _success(json.dumps({"text": "A grounded answer.", "findings": [], "conversation_title": "Should not show"}))

    monkeypatch.setattr(runs, "generate", generate)
    runs.execute_run(user.id, run_id)
    result = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert result.state == "succeeded" and result.answer is not None
    assert result.answer.conversation_title is None
    assert '"generate_conversation_title": false' in calls[0]
    assert runs.ExpertRunService(db, owner=user).get_conversation(conversation_id).title is None


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
    conversation_id, identifier, payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))

    def cancel_then_reply(*_args, **_kwargs):
        with SessionLocal() as session:
            owner = session.get(User, user.id)
            assert owner is not None
            runs.ExpertRunService(session, owner=owner).cancel(identifier)
        return _success(json.dumps({
            "text": "Valid answer.", "findings": [], "conversation_title": "Discarded title",
        }))

    monkeypatch.setattr(runs, "generate", cancel_then_reply)
    runs.execute_run(user.id, identifier)
    result = runs.ExpertRunService(db, owner=user).get_run(identifier)
    assert result.state == "cancelled" and result.answer is None
    assert runs.ExpertRunService(db, owner=user).get_conversation(result.conversation_id).title is None

    retry_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, retry_id, payload)
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(json.dumps({
        "text": "Valid answer.", "findings": [], "conversation_title": "Nutrition overview",
    })))
    runs.execute_run(user.id, retry_id)
    retried = runs.ExpertRunService(db, owner=user).get_run(retry_id)
    assert retried.state == "succeeded" and retried.answer is not None
    assert runs.ExpertRunService(db, owner=user).get_conversation(conversation_id).title == "Nutrition overview"


def test_failed_first_chat_attempt_allows_next_success_to_title_conversation(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id, first_id, payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: (_ for _ in ()).throw(runs.GenerationFailure("provider_unavailable")))
    runs.execute_run(user.id, first_id)
    assert runs.ExpertRunService(db, owner=user).get_run(first_id).state == "failed"

    second_id = uuid4()
    runs.ExpertRunService(db, owner=user).put_run(conversation_id, second_id, payload)
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(json.dumps({
        "text": "Valid answer.", "findings": [], "conversation_title": "Ernährung im Überblick",
    })))
    runs.execute_run(user.id, second_id)
    second = runs.ExpertRunService(db, owner=user).get_run(second_id)
    assert second.state == "succeeded" and second.answer is not None
    assert second.answer.conversation_title == "Ernährung im Überblick"
    assert runs.ExpertRunService(db, owner=user).get_conversation(conversation_id).title == "Ernährung im Überblick"


def test_credential_echo_in_provider_title_rejects_response(db: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _conversation_id, run_id, _payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(json.dumps({
        "text": "Valid answer.", "findings": [], "conversation_title": KEY,
    })))
    runs.execute_run(user.id, run_id)
    result = runs.ExpertRunService(db, owner=user).get_run(run_id)
    assert result.state == "failed" and result.error_code == "invalid_response" and result.answer is None


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


@pytest.mark.parametrize("content", [
    '{"text":',
    '{"text":37,"conversation_title":"Safe title"}',
    json.dumps({"text": "\ud800", "conversation_title": "Safe title"}),
    '{"text":"Valid answer","conversation_title":NaN}',
    '{"text":"Valid answer","conversation_title":Infinity}',
])
def test_invalid_answer_json_or_required_text_does_not_persist_title(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, content: str,
) -> None:
    conversation_id, run_id, _payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(content))
    runs.execute_run(user.id, run_id)
    service = runs.ExpertRunService(db, owner=user)
    result = service.get_run(run_id)
    assert result.state == "failed" and result.answer is None
    assert service.get_conversation(conversation_id).title is None
    row = db.get(ExpertConversation, (user.id, conversation_id))
    assert row is not None and row.title_source is None


def test_answer_json_duplicate_keys_preserve_last_value_behavior(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id, run_id, _payload = _queued(db, user)
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context()))
    monkeypatch.setattr(runs, "generate", lambda *_args, **_kwargs: _success(
        '{"text":"first","text":"second","conversation_title":"Topic title"}',
    ))
    runs.execute_run(user.id, run_id)
    service = runs.ExpertRunService(db, owner=user)
    result = service.get_run(run_id)
    assert result.state == "succeeded" and result.answer is not None
    assert result.answer.text == "second"
    assert service.get_conversation(conversation_id).title == "Topic title"
