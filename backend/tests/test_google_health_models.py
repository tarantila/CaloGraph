from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CheckConstraint

from app.models import GoogleHealthConnection, GoogleHealthOAuthFlow, User


def test_google_health_models_have_only_encrypted_connection_and_flow_fields() -> None:
    connection = GoogleHealthConnection.__table__
    flow = GoogleHealthOAuthFlow.__table__

    assert connection.name == "google_health_connections"
    assert flow.name == "google_health_oauth_flows"
    assert {column.name for column in connection.columns} == {
        "id",
        "user_id",
        "client_id",
        "encrypted_client_secret",
        "encrypted_refresh_token",
        "granted_scopes",
        "state",
        "refresh_token_expires_at",
        "last_attempt_at",
        "last_success_at",
        "last_error",
        "last_error_category",
        "sync_state",
        "retry_attempt",
        "retry_max_attempts",
        "next_retry_at",
        "created_at",
        "updated_at",
    }
    assert {column.name for column in flow.columns} == {
        "id",
        "user_id",
        "state_hash",
        "encrypted_pkce_verifier",
        "expires_at",
        "consumed_at",
        "created_at",
    }
    forbidden = {
        "raw_state",
        "authorization_code",
        "auth_code",
        "access_token",
        "client_secret",
        "raw_payload",
        "nutrition_data",
    }
    assert not forbidden & {column.name for column in connection.columns}
    assert not forbidden & {column.name for column in flow.columns}


def test_google_health_connection_contract_is_per_user_and_encrypted() -> None:
    table = GoogleHealthConnection.__table__
    columns = table.c
    assert columns.id.primary_key
    assert columns.client_id.type.python_type is str
    assert columns.client_id.nullable
    assert columns.client_id.type.length == 512
    assert columns.encrypted_client_secret.type.python_type is bytes
    assert columns.encrypted_client_secret.nullable
    assert columns.encrypted_refresh_token.type.python_type is bytes
    assert columns.encrypted_refresh_token.nullable
    assert not columns.granted_scopes.nullable
    assert not columns.state.nullable
    assert columns.state.type.length == 16
    assert columns.refresh_token_expires_at.nullable
    assert columns.last_attempt_at.nullable
    assert columns.last_success_at.nullable
    assert columns.last_error.nullable
    assert columns.last_error.type.length == 128
    assert {fk.ondelete for fk in columns.user_id.foreign_keys} == {"CASCADE"}
    assert any(
        constraint.name == "uq_google_health_connections_user_id"
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_google_health_connections_state"
        and "active" in str(constraint.sqltext)
        and "reauth_required" in str(constraint.sqltext)
        and "not_connected" in str(constraint.sqltext)
        for constraint in table.constraints
    )
    assert any(index.name == "ix_google_health_connections_user_id" for index in table.indexes)


def test_google_health_flow_contract_has_expiry_consumption_and_state_hash_index() -> None:
    table = GoogleHealthOAuthFlow.__table__
    columns = table.c
    assert columns.id.primary_key
    assert columns.encrypted_pkce_verifier.type.python_type is bytes
    assert not columns.encrypted_pkce_verifier.nullable
    assert not columns.state_hash.nullable
    assert columns.state_hash.type.length == 64
    assert columns.expires_at.index
    assert columns.consumed_at.nullable
    assert {fk.ondelete for fk in columns.user_id.foreign_keys} == {"CASCADE"}

    assert any(
        constraint.name == "uq_google_health_oauth_flows_state_hash"
        for constraint in table.constraints
    )
    assert any(index.name == "ix_google_health_oauth_flows_state_hash" for index in table.indexes)
    assert any(index.name == "ix_google_health_oauth_flows_expires_at" for index in table.indexes)


def test_google_health_models_persist_defaults_and_relationships(db, user: User) -> None:
    connection = GoogleHealthConnection(
        user_id=user.id,
        client_id="client-id.apps.googleusercontent.com",
        encrypted_client_secret=b"encrypted-client-secret",
        state="not_connected",
    )
    flow = GoogleHealthOAuthFlow(
        user_id=user.id,
        state_hash="a" * 64,
        encrypted_pkce_verifier=b"encrypted-pkce-verifier",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add_all([connection, flow])
    db.flush()
    db.refresh(connection)
    db.refresh(flow)

    assert connection.state == "not_connected"
    assert connection.encrypted_refresh_token is None
    assert connection.granted_scopes == []
    assert connection.created_at is not None
    assert connection.updated_at is not None
    assert flow.created_at is not None
    assert flow.consumed_at is None
    assert (
        db.scalar(select(GoogleHealthConnection).where(GoogleHealthConnection.user_id == user.id))
        is connection
    )
    assert (
        db.scalar(select(GoogleHealthOAuthFlow).where(GoogleHealthOAuthFlow.state_hash == "a" * 64))
        is flow
    )
    assert user.google_health_connection is connection
    assert flow in user.google_health_oauth_flows


def test_google_health_connection_is_unique_per_user(db, user: User) -> None:
    db.add(
        GoogleHealthConnection(
            client_id="models-client",
            encrypted_client_secret=b"encrypted-models-secret",
            user_id=user.id,
            encrypted_refresh_token=b"first",
        )
    )
    db.flush()
    db.add(
        GoogleHealthConnection(
            client_id="models-client",
            encrypted_client_secret=b"encrypted-models-secret",
            user_id=user.id,
            encrypted_refresh_token=b"second",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_google_health_flow_consumed_at_round_trips(db, user: User) -> None:
    consumed_at = datetime.now(UTC)
    flow = GoogleHealthOAuthFlow(
        id=uuid4(),
        user_id=user.id,
        state_hash="b" * 64,
        encrypted_pkce_verifier=b"ciphertext",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        consumed_at=consumed_at,
    )
    db.add(flow)
    db.commit()
    loaded = db.get(GoogleHealthOAuthFlow, flow.id)
    assert loaded is not None


def test_google_health_relationship_foreign_key_metadata() -> None:
    inspector = inspect(User)
    assert inspector.mapper.relationships["google_health_connection"].uselist is False
    assert inspector.mapper.relationships["google_health_oauth_flows"].uselist is True


def test_google_health_rows_carry_user_ownership_for_every_secret_bearing_record() -> None:
    connection = GoogleHealthConnection.__table__
    flow = GoogleHealthOAuthFlow.__table__
    assert not connection.c.user_id.nullable
    assert not flow.c.user_id.nullable
    assert connection.c.user_id.index
    assert flow.c.user_id.index
    assert {
        fk.column.table.name
        for fk in connection.c.user_id.foreign_keys
    } == {"users"}
    assert {fk.column.table.name for fk in flow.c.user_id.foreign_keys} == {"users"}
    assert connection.c.encrypted_client_secret.type.python_type is bytes
    assert connection.c.encrypted_refresh_token.type.python_type is bytes
    assert flow.c.encrypted_pkce_verifier.type.python_type is bytes
