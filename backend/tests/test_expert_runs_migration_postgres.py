from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.database import engine as application_engine

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_expert_runs_migration_chain_and_active_owner_uniqueness() -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    owner_id, other_owner_id = uuid4(), uuid4()
    conversation_id, other_conversation_id = uuid4(), uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    try:
        # The supported test runner supplies an isolated disposable PostgreSQL DB.
        command.stamp(config, "head")
        command.downgrade(config, "20261114_0047")
        command.upgrade(config, "20261114_0048")
        with engine.begin() as connection:
            for identifier, name in ((owner_id, "expert-run-owner"), (other_owner_id, "expert-run-other")):
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, username, password_hash, language, timezone, week_starts_on, "
                        "preferred_weight_unit, raw_payload_retention_days, is_active, is_admin, "
                        "created_at, updated_at) VALUES (:id, :name, 'synthetic-hash', 'de', "
                        "'Europe/Berlin', 1, 'kg', 0, true, false, :now, :now)"
                    ),
                    {"id": identifier, "name": f"{name}-{uuid4()}", "now": now},
                )
            connection.execute(
                text(
                    "INSERT INTO expert_conversations (user_id, id, mode, created_at, updated_at) "
                    "VALUES (:owner, :id, 'chat', :now, :now), (:other, :other_id, 'analysis', :now, :now)"
                ),
                {"owner": owner_id, "id": conversation_id, "other": other_owner_id,
                 "other_id": other_conversation_id, "now": now},
            )
            values = {
                "owner": owner_id, "id": run_id, "conversation": conversation_id,
                "hash": "a" * 64, "now": now,
            }
            connection.execute(
                text(
                    "INSERT INTO expert_runs (user_id, id, conversation_id, request_hash, kind, message, "
                    "connection_id, connection_version, preferences_version, consent_id, provider_kind, "
                    "model_id, state, created_at) VALUES (:owner, :id, :conversation, :hash, 'chat', "
                    "'synthetic question', :connection, 1, 1, :consent, 'openai_api', 'synthetic-model', "
                    "'queued', :now)"
                ),
                {**values, "connection": uuid4(), "consent": uuid4()},
            )
        # Exercise additive receipt backfill for a database that had the
        # conversation schema before the receipt migration was applied.
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM expert_conversation_identities "
                    "WHERE user_id=:owner AND conversation_id=:id"
                ), {"owner": owner_id, "id": conversation_id},
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    "SELECT count(*) FROM expert_run_identities "
                    "WHERE user_id=:owner AND run_id=:id"
                ), {"owner": owner_id, "id": run_id},
            ).scalar_one() == 1
        with pytest.raises(RuntimeError, match="Expert request identity receipts"):
            command.downgrade(config, "20261124_0058")
        with engine.begin() as connection, pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                    text(
                        "INSERT INTO expert_runs (user_id, id, conversation_id, request_hash, kind, message, "
                        "connection_id, connection_version, preferences_version, consent_id, provider_kind, "
                        "model_id, state, created_at) VALUES (:owner, :id, :conversation, :hash, 'chat', "
                        "'second active request', :connection, 1, 1, :consent, 'openai_api', 'synthetic-model', "
                        "'running', :now)"
                    ),
                    {"owner": owner_id, "id": uuid4(), "conversation": conversation_id,
                     "hash": "b" * 64, "connection": uuid4(), "consent": uuid4(), "now": now},
                )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE expert_runs SET state='failed' WHERE user_id=:owner AND id=:id"),
                {"owner": owner_id, "id": run_id},
            )
            connection.execute(
                text(
                    "INSERT INTO expert_runs (user_id, id, conversation_id, request_hash, kind, message, "
                    "connection_id, connection_version, preferences_version, consent_id, provider_kind, "
                    "model_id, state, created_at) VALUES (:owner, :id, :conversation, :hash, 'chat', "
                    "'next request', :connection, 1, 1, :consent, 'openai_api', 'synthetic-model', 'queued', :now)"
                ),
                {"owner": owner_id, "id": uuid4(), "conversation": conversation_id,
                 "hash": "c" * 64, "connection": uuid4(), "consent": uuid4(), "now": now},
            )
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM users WHERE id IN (:owner, :other)"),
                {"owner": owner_id, "other": other_owner_id},
            )
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM expert_conversation_identities WHERE user_id=:owner"
            ), {"owner": owner_id}).scalar_one() == 0
            assert connection.execute(text(
                "SELECT count(*) FROM expert_run_identities WHERE user_id=:owner"
            ), {"owner": owner_id}).scalar_one() == 0
        command.downgrade(config, "20261114_0047")
        with engine.connect() as connection:
            table_names = inspect(connection).get_table_names()
            assert "expert_runs" not in table_names
            assert "expert_conversations" not in table_names
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_concurrent_run_workers_dispatch_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date
    from threading import Barrier, Lock
    from types import SimpleNamespace

    import app.expert.runs as runs
    from app.database import SessionLocal
    from app.models import User
    from tests.test_expert_runs import _context, _prepare, _success

    assert application_engine.dialect.name == "postgresql"
    with SessionLocal() as session:
        owner = User(
            username=f"expert-worker-{uuid4()}", password_hash="synthetic-hash",
            timezone="Europe/Berlin",
        )
        session.add(owner)
        session.commit()
        owner_id = owner.id
        conversation_id, payload, _ = _prepare(session, owner)
        run_id = uuid4()
        result, created = runs.ExpertRunService(session, owner=owner).put_run(
            conversation_id, run_id, payload
        )
        assert created and result.state == "queued"

    barrier = Barrier(2)
    calls: list[bool] = []
    calls_lock = Lock()
    monkeypatch.setattr(
        runs, "ContextBuilder",
        lambda *_args, **_kwargs: SimpleNamespace(build=lambda _prefs: _context(end=date(2026, 1, 2))),
    )

    def generate_once(*_args, **_kwargs):
        with calls_lock:
            calls.append(True)
        return _success()

    monkeypatch.setattr(runs, "generate", generate_once)

    def worker() -> None:
        barrier.wait(timeout=10)
        runs.execute_run(owner_id, run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)

    assert calls == [True]
    with SessionLocal() as session:
        owner = session.get(User, owner_id)
        assert owner is not None
        stored = runs.ExpertRunService(session, owner=owner).get_run(run_id)
        assert stored.state == "succeeded"
        session.delete(owner)
        session.commit()
