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
from app.ai.subscription_authorization_contracts import (
    SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
    SubscriptionAuthorizationStart,
)
from app.ai.subscription_credentials import SubscriptionCredentialStore
from app.ai.subscription_device import SubscriptionDeviceAuthorizationService
from app.ai.subscription_device_contracts import SubscriptionGrant
from app.config import settings
from app.database import SessionLocal, engine
from app.models import (
    AIProviderConnection,
    SubscriptionCredential,
    SubscriptionDeviceAuthorization,
    User,
)
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
def test_device_start_poll_and_exchange_hold_lock_without_open_database_transaction(db, user):
    assert engine.dialect.name == "postgresql"
    now = datetime.now(UTC)
    start_count, poll_count, exchange_count = [], [], []

    def start():
        assert not db.in_transaction()
        _assert_lock_session_idle_and_exclusive(user.id)
        start_count.append(True)
        return {
            "device_auth_id": "synthetic-device-id", "user_code": "SYNTH-CODE",
            "interval": 1, "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }

    def poll(device_id, user_code):
        assert not db.in_transaction()
        _assert_lock_session_idle_and_exclusive(user.id)
        poll_count.append((device_id, user_code))
        return "authorized", {
            "authorization_code": "synthetic-code", "code_verifier": "v" * 43,
            "code_challenge": "synthetic-challenge",
        }

    def exchange(code, verifier, challenge):
        assert not db.in_transaction()
        _assert_lock_session_idle_and_exclusive(user.id)
        exchange_count.append((code, verifier, challenge))
        return SubscriptionGrant(
            access_token=SecretStr("synthetic-access"),
            refresh_token=SecretStr("synthetic-refresh"), id_token=SecretStr("synthetic-id"),
            token_type="Bearer", expires_in=3600,
            scope="openid profile email offline_access api.connectors.read api.connectors.invoke",
            client_id="app_EMoamEEZ73f0CkXaXp7hrann", subject="synthetic-subject",
            workspace_id="synthetic-workspace", received_at=now, expires_at=now + timedelta(hours=1),
        )

    service = SubscriptionDeviceAuthorizationService(
        db, owner=user, start_call=start, poll_call=poll, exchange_call=exchange,
    )
    connection_id, attempt_id = uuid4(), uuid4()
    payload = SubscriptionAuthorizationStart(
        privacy_acknowledgement=SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
        expected_credential_version=0,
    )
    started, created = service.start(connection_id, attempt_id, payload)
    assert created and started.state == "pending"
    attempt = db.get(SubscriptionDeviceAuthorization, (user.id, attempt_id))
    assert attempt is not None
    attempt.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert service.poll(connection_id, attempt_id).state == "completed"
    assert start_count == [True] and len(poll_count) == len(exchange_count) == 1


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_0065_upgrade_adds_device_attempts_and_retained_receipts(monkeypatch):
    assert engine.dialect.name == "postgresql"
    base_url = make_url(os.environ["CALOGRAPH_POSTGRES_TEST_URL"])
    schema = f"subscription_device_migration_{uuid4().hex}"
    admin_engine = sa.create_engine(base_url, pool_pre_ping=True)
    schema_engine = None
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        isolated_url = schema_url.render_as_string(hide_password=False).replace("%", "%%")
        monkeypatch.setattr(settings, "database_url", isolated_url)
        config = Config("alembic.ini")
        command.upgrade(config, "20261127_0065")
        schema_engine = sa.create_engine(schema_url, pool_pre_ping=True)
        factory = sessionmaker(bind=schema_engine, expire_on_commit=False)
        owner_id, connection_id, attempt_id = uuid4(), uuid4(), uuid4()
        with factory() as session:
            session.add(User(
                id=owner_id, username=f"subscription-device-migration-{owner_id.hex}",
                password_hash="synthetic-only",
            ))
            session.add(AIProviderConnection(
                user_id=owner_id, id=connection_id, label="Existing ChatGPT draft",
                provider_kind="chatgpt_plan", model_id=None, base_url=None, enabled=False,
                timeout_seconds=60, configuration_version=1,
            ))
            session.commit()
            session.execute(text(
                "INSERT INTO ai_subscription_credentials "
                "(user_id, connection_id, state, credential_version, encrypted_tokens, "
                "encrypted_binding, expires_at, updated_at) VALUES "
                "(:owner, :connection, 'disconnected', 3, NULL, :binding, NULL, :now)"
            ), {"owner": owner_id, "connection": connection_id,
                "binding": b"synthetic-ciphertext", "now": datetime.now(UTC)})
            session.commit()

        command.upgrade(config, "20261128_0066")
        command.check(config)
        inspector = sa.inspect(schema_engine)
        assert {"ai_subscription_device_authorizations", "ai_subscription_device_authorization_receipts"} <= set(
            inspector.get_table_names()
        )
        attempt_fks = inspector.get_foreign_keys("ai_subscription_device_authorizations")
        assert any(
            fk["constrained_columns"] == ["user_id", "connection_id"]
            and fk["referred_table"] == "ai_provider_connections"
            and fk["options"].get("ondelete") == "CASCADE"
            for fk in attempt_fks
        )
        with schema_engine.begin() as connection:
            assert connection.scalar(text("SELECT credential_version FROM ai_subscription_credentials")) == 3
            connection.execute(text(
                "INSERT INTO ai_subscription_device_authorizations "
                "(user_id,id,connection_id,configuration_version,credential_generation,request_hash,state,"
                "poll_interval_seconds,expires_at,created_at,updated_at) VALUES "
                "(:owner,:attempt,:connection,1,3,:hash,'cancelled',5,:expiry,:now,:now)"
            ), {"owner": owner_id, "attempt": attempt_id, "connection": connection_id,
                "hash": "a" * 64, "expiry": datetime.now(UTC) + timedelta(minutes=1), "now": datetime.now(UTC)})
            connection.execute(text(
                "INSERT INTO ai_subscription_device_authorization_receipts "
                "(user_id,authorization_id,connection_id,request_hash,created_at) "
                "VALUES (:owner,:attempt,:connection,:hash,:now)"
            ), {"owner": owner_id, "attempt": attempt_id, "connection": connection_id,
                "hash": "a" * 64, "now": datetime.now(UTC)})
        with pytest.raises(RuntimeError, match="Refusing to remove retained subscription device authorization state"):
            command.downgrade(config, "20261127_0065")
        with schema_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM ai_provider_connections WHERE user_id=:owner AND id=:connection"
            ), {"owner": owner_id, "connection": connection_id})
            assert connection.scalar(text("SELECT count(*) FROM ai_subscription_device_authorizations")) == 0
            assert connection.scalar(text("SELECT count(*) FROM ai_subscription_device_authorization_receipts")) == 1
            connection.execute(text(
                "DELETE FROM ai_subscription_device_authorization_receipts WHERE user_id=:owner"
            ), {"owner": owner_id})
        command.downgrade(config, "20261127_0065")
        assert "ai_subscription_device_authorizations" not in sa.inspect(schema_engine).get_table_names()
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


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
