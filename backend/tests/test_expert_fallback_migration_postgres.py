from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alembic import command
from app.database import SessionLocal
from app.database import engine as application_engine
from app.models import ExpertRun, ExpertRunProviderAttempt, User

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


def _queued_fallback(db: Session, user: User):
    from app.ai.routing import AIRoutingService
    from app.ai.routing_contracts import AIRoutingEntryPut, AIRoutingPut
    from app.expert.runs import ExpertRunService
    from tests.test_expert_runs import _prepare

    conversation, payload, connection = _prepare(db, user)
    route = AIRoutingService(db, owner=user).put(AIRoutingPut(
        expected_configuration_version=0,
        entries=(AIRoutingEntryPut(connection_id=connection.id, enabled=True),),
    ))
    identifier = uuid4()
    ExpertRunService(db, owner=user).put_run(conversation, identifier, payload.model_copy(
        update={"routing_configuration_version": route.configuration_version},
    ))
    return identifier


@pytest.mark.skipif(not POSTGRES_TESTS_ENABLED, reason="isolated PostgreSQL tests are not enabled")
def test_failure_finalization_holds_run_lock_against_cancellation(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai.generation import GenerationFailure
    from app.expert import runs
    from tests.test_expert_runs import _context

    identifier, owner_id = _queued_fallback(db, user), user.id
    provider_failed, paused, release, cancel_started = (Event() for _ in range(4))
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_a, **_kw: SimpleNamespace(build=lambda _p: _context()))

    def reject(*_a, **_kw):
        provider_failed.set()
        raise GenerationFailure("authentication")

    def pause_finalization(state):
        if provider_failed.is_set() and not paused.is_set() and state.is_select and "expert_runs" in str(state.statement):
            snapshot = state.invoke_statement().freeze()
            paused.set()
            assert release.wait(10)
            return snapshot()
        return state.invoke_statement()

    def cancel():
        with SessionLocal() as session:
            owner = session.get(User, owner_id)
            assert owner is not None
            cancel_started.set()
            return runs.ExpertRunService(session, owner=owner).cancel(identifier)

    monkeypatch.setattr(runs, "generate", reject)
    event.listen(Session, "do_orm_execute", pause_finalization, retval=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            execution = pool.submit(runs.execute_run, owner_id, identifier)
            try:
                assert paused.wait(10)
                with application_engine.connect() as probe:
                    with pytest.raises(OperationalError):
                        probe.execute(select(ExpertRun.id).where(
                            ExpertRun.user_id == owner_id, ExpertRun.id == identifier,
                        ).with_for_update(nowait=True))
                    probe.rollback()
                cancellation = pool.submit(cancel)
                assert cancel_started.wait(10)
            finally:
                release.set()
            execution.result(timeout=15)
            assert cancellation.result(timeout=15).state == "failed"
        result = runs.ExpertRunService(db, owner=user).get_run(identifier)
        assert result.state == "failed" and result.answer is None
        assert not any(item.winner for item in result.provider_attempts)
    finally:
        event.remove(Session, "do_orm_execute", pause_finalization)


@pytest.mark.skipif(not POSTGRES_TESTS_ENABLED, reason="isolated PostgreSQL tests are not enabled")
def test_winner_and_answer_commit_atomically_after_provider_success(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.expert import runs
    from tests.test_expert_runs import _context, _success

    identifier = _queued_fallback(db, user)
    original_commit, injected = Session.commit, []
    monkeypatch.setattr(runs, "ContextBuilder", lambda *_a, **_kw: SimpleNamespace(build=lambda _p: _context()))
    monkeypatch.setattr(runs, "generate", lambda *_a, **_kw: _success())

    def fail_after_winner_commit(session):
        winner = any(
            isinstance(item, ExpertRunProviderAttempt) and item.run_id == identifier and item.state == "succeeded"
            for item in session.identity_map.values()
        )
        original_commit(session)
        if winner and not injected:
            injected.append(True)
            raise RuntimeError("synthetic failure after commit")

    monkeypatch.setattr(Session, "commit", fail_after_winner_commit)
    runs.execute_run(user.id, identifier)
    result = runs.ExpertRunService(db, owner=user).get_run(identifier)
    assert injected
    assert result.state == "succeeded" and result.answer is not None
    assert sum(item.winner for item in result.provider_attempts) == 1


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_fallback_migration_snapshot_attempt_cascade_and_guard() -> None:
    assert application_engine.dialect.name == "postgresql"
    url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url, pool_pre_ping=True)
    owner_id, conversation_id, run_id, candidate_id, consent_id = (uuid4() for _ in range(5))
    now = datetime.now(UTC)
    try:
        command.stamp(config, "head")
        command.downgrade(config, "20261125_0063")
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id, username, password_hash, language, timezone, week_starts_on, "
                "preferred_weight_unit, raw_payload_retention_days, is_active, is_admin, created_at, updated_at) "
                "VALUES (:id, :username, 'synthetic-hash', 'de', 'Europe/Berlin', 1, 'kg', 0, true, false, :now, :now)"
            ), {"id": owner_id, "username": f"fallback-migration-{uuid4()}", "now": now})
            connection.execute(text(
                "INSERT INTO expert_conversations (user_id, id, mode, created_at, updated_at) "
                "VALUES (:owner, :id, 'chat', :now, :now)"
            ), {"owner": owner_id, "id": conversation_id, "now": now})
            connection.execute(text(
                "INSERT INTO expert_runs (user_id, id, conversation_id, request_hash, kind, message, "
                "connection_id, connection_version, preferences_version, consent_id, provider_kind, model_id, state, created_at) "
                "VALUES (:owner, :id, :conversation, :hash, 'chat', 'synthetic prompt', :connection, 1, 1, :consent, "
                "'openai_api', 'synthetic-model', 'queued', :now)"
            ), {"owner": owner_id, "id": run_id, "conversation": conversation_id,
                "hash": "a" * 64, "connection": candidate_id, "consent": consent_id, "now": now})
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE expert_runs SET routing_version=1, routing_snapshot='[]'::json "
                "WHERE user_id=:owner AND id=:run"
            ), {"owner": owner_id, "run": run_id})
            connection.execute(text(
                "INSERT INTO expert_run_provider_attempts "
                "(user_id, run_id, position, connection_id, connection_version, provider_kind, model_id, consent_id, state) "
                "VALUES (:owner, :run, 0, :connection, 1, 'openai_api', 'synthetic-model', :consent, 'pending')"
            ), {"owner": owner_id, "run": run_id, "connection": candidate_id, "consent": consent_id})
        with pytest.raises(RuntimeError, match="persisted Expert routing snapshots"):
            command.downgrade(config, "20261125_0063")
        with engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM expert_conversations WHERE user_id=:owner AND id=:conversation"
            ), {"owner": owner_id, "conversation": conversation_id})
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM expert_run_provider_attempts WHERE user_id=:owner"
            ), {"owner": owner_id}).scalar_one() == 0
        command.downgrade(config, "20261125_0063")
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:owner"), {"owner": owner_id})
    finally:
        engine.dispose()
