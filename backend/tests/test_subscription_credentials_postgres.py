"""Opt-in PostgreSQL coverage for the credential lifecycle lock boundary."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.ai.subscription_credentials import SubscriptionCredentialStore
from app.ai.subscription_device_contracts import SubscriptionGrant
from app.config import settings
from app.database import SessionLocal, engine
from app.models import AIProviderConnection, SubscriptionCredential, User
from app.services.user_operation_lock import UserOperationBusy, exclusive_user_lifecycle_operation

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)


def _assert_lock_session_idle_and_exclusive(user_id) -> None:
    with SessionLocal() as observer:
        holders = observer.execute(text(
            "SELECT activity.state, activity.xact_start "
            "FROM pg_locks AS lock JOIN pg_stat_activity AS activity USING (pid) "
            "WHERE lock.locktype = 'advisory' AND lock.granted "
            "AND activity.pid <> pg_backend_pid()"
        )).all()
        assert any(state == "idle" and xact_start is None for state, xact_start in holders)
        with pytest.raises(UserOperationBusy), exclusive_user_lifecycle_operation(observer, user_id):
            pytest.fail("second lifecycle operation entered while the first was in provider I/O")


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_provider_io_holds_exclusive_lock_without_open_database_transaction(db, user):
    assert engine.dialect.name == "postgresql"
    now = datetime.now(UTC)
    grant = SubscriptionGrant(
        access_token=SecretStr("synthetic-access"),
        refresh_token=SecretStr("synthetic-refresh"),
        id_token=SecretStr("synthetic-id"), token_type="Bearer", expires_in=3600,
        scope="openid profile email offline_access api.connectors.read api.connectors.invoke",
        client_id="app_EMoamEEZ73f0CkXaXp7hrann", subject="synthetic-subject",
        workspace_id="synthetic-workspace", received_at=now,
        expires_at=now + timedelta(hours=1),
    )
    connection_id = uuid4()
    store = SubscriptionCredentialStore(db, owner=user)
    store.create_draft(connection_id)
    store.save_verified(connection_id, grant)
    connection = db.get(AIProviderConnection, (user.id, connection_id))
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert connection is not None and row is not None
    def refresh(_grant):
        assert not db.in_transaction()
        _assert_lock_session_idle_and_exclusive(user.id)
        from app.ai.subscription_device_contracts import RefreshedSubscriptionGrant

        return RefreshedSubscriptionGrant(
            access_token=SecretStr("synthetic-rotated-access"),
            refresh_token=SecretStr("synthetic-rotated-refresh"),
            id_token=SecretStr("synthetic-rotated-id"), token_type="Bearer", expires_in=3600,
            scope=grant.scope, client_id=grant.client_id, subject=grant.subject,
            workspace_id=grant.workspace_id, received_at=now,
            expires_at=now + timedelta(hours=1),
        )

    store._refresh = refresh
    store.get_grant(connection_id, now=now + timedelta(minutes=59))

    def revoke(_grant):
        assert not db.in_transaction()
        _assert_lock_session_idle_and_exclusive(user.id)
        return True

    store._revoke = revoke
    store.disconnect(connection_id)


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_0064_upgrade_preserves_connections_and_refuses_loss_of_credentials(monkeypatch):
    assert engine.dialect.name == "postgresql"
    base_url = make_url(os.environ["CALOGRAPH_POSTGRES_TEST_URL"])
    schema = f"subscription_migration_{uuid4().hex}"
    admin_engine = sa.create_engine(base_url, pool_pre_ping=True)
    schema_engine = None
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        isolated_url = schema_url.render_as_string(hide_password=False).replace("%", "%%")
        monkeypatch.setattr(settings, "database_url", isolated_url)
        config = Config("alembic.ini")
        command.upgrade(config, "20261125_0064")
        schema_engine = sa.create_engine(schema_url, pool_pre_ping=True)
        factory = sessionmaker(bind=schema_engine, expire_on_commit=False)
        owner_id, connection_id = uuid4(), uuid4()
        with factory() as session:
            session.add(User(
                id=owner_id, username=f"subscription-migration-{owner_id.hex}",
                password_hash="synthetic-only",
            ))
            session.add(AIProviderConnection(
                user_id=owner_id, id=connection_id, label="Existing ChatGPT draft",
                provider_kind="chatgpt_plan", model_id=None, base_url=None, enabled=False,
                timeout_seconds=60, configuration_version=1,
            ))
            session.commit()

        command.upgrade(config, "20261127_0065")
        inspector = sa.inspect(schema_engine)
        assert "ai_subscription_credentials" in inspector.get_table_names()
        foreign_keys = inspector.get_foreign_keys("ai_subscription_credentials")
        assert any(
            fk["constrained_columns"] == ["user_id", "connection_id"]
            and fk["referred_table"] == "ai_provider_connections"
            and fk["options"].get("ondelete") == "CASCADE"
            for fk in foreign_keys
        )
        with schema_engine.begin() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM ai_provider_connections WHERE user_id=:owner AND id=:connection"
            ), {"owner": owner_id, "connection": connection_id}) == 1
            connection.execute(text(
                "INSERT INTO ai_subscription_credentials "
                "(user_id, connection_id, state, credential_version, encrypted_tokens, "
                "encrypted_binding, expires_at, updated_at) VALUES "
                "(:owner, :connection, 'disconnected', 1, NULL, :binding, NULL, :now)"
            ), {"owner": owner_id, "connection": connection_id, "binding": b"synthetic-ciphertext", "now": datetime.now(UTC)})
        with pytest.raises(RuntimeError, match="Refusing to remove persisted subscription credential state"):
            command.downgrade(config, "20261125_0064")
        with schema_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_subscription_credentials")) == 1
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261127_0065"
        with schema_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM ai_provider_connections WHERE user_id=:owner AND id=:connection"
            ), {"owner": owner_id, "connection": connection_id})
            assert connection.scalar(text("SELECT count(*) FROM ai_subscription_credentials")) == 0
        command.downgrade(config, "20261125_0064")
        assert "ai_subscription_credentials" not in sa.inspect(schema_engine).get_table_names()
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
