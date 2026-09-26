from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CheckConstraint

from app.models import User, WithingsConnection, WithingsOAuthFlow


def test_withings_tables_exclude_raw_oauth_material() -> None:
    connection = WithingsConnection.__table__
    flow = WithingsOAuthFlow.__table__

    assert connection.name == "withings_connections"
    assert flow.name == "withings_oauth_flows"
    forbidden = {
        "raw_state",
        "authorization_code",
        "access_token",
        "refresh_token",
        "client_secret",
        "raw_payload",
        "nutrition_data",
    }
    assert not forbidden & {column.name for column in connection.columns}
    assert not forbidden & {column.name for column in flow.columns}


def test_withings_connection_contract_is_per_user_and_encrypted() -> None:
    table = WithingsConnection.__table__
    columns = table.c
    assert columns.id.primary_key
    assert columns.user_id.index
    assert columns.client_id.nullable
    assert columns.client_id.type.length == 512
    assert columns.encrypted_client_secret.type.python_type is bytes
    assert columns.encrypted_client_secret.nullable
    assert columns.withings_user_id.type.python_type is str
    assert columns.withings_user_id.nullable
    assert columns.encrypted_access_token.type.python_type is bytes
    assert columns.encrypted_access_token.nullable
    assert columns.encrypted_refresh_token.type.python_type is bytes
    assert columns.encrypted_refresh_token.nullable
    assert not columns.granted_scopes.nullable
    assert not columns.state.nullable
    assert columns.state.type.length == 16
    assert columns.access_token_expires_at.nullable
    assert columns.last_attempt_at.nullable
    assert columns.last_success_at.nullable
    assert columns.last_error_category.type.length == 32
    assert columns.last_activity_error_category.type.length == 32
    assert columns.last_error.type.length == 128
    assert {fk.ondelete for fk in columns.user_id.foreign_keys} == {"CASCADE"}
    assert any(
        constraint.name == "uq_withings_connections_user_id"
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_withings_connections_state"
        and "active" in str(constraint.sqltext)
        and "reauth_required" in str(constraint.sqltext)
        and "not_connected" in str(constraint.sqltext)
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_withings_connections_sync_state"
        and "idle" in str(constraint.sqltext)
        and "failed" in str(constraint.sqltext)
        for constraint in table.constraints
    )


def test_withings_flow_contract_has_expiry_consumption_and_state_hash_index() -> None:
    table = WithingsOAuthFlow.__table__
    columns = table.c
    assert columns.id.primary_key
    assert not columns.state_hash.nullable
    assert columns.state_hash.type.length == 64
    assert columns.connection_id.nullable
    assert columns.connection_id.index
    assert {fk.target_fullname for fk in columns.connection_id.foreign_keys} == {
        "withings_connections.id"
    }
    assert {fk.ondelete for fk in columns.connection_id.foreign_keys} == {"CASCADE"}
    assert columns.expires_at.index
    assert columns.consumed_at.nullable
    assert {fk.ondelete for fk in columns.user_id.foreign_keys} == {"CASCADE"}
    assert any(
        constraint.name == "uq_withings_oauth_flows_state_hash"
        for constraint in table.constraints
    )
    assert any(index.name == "ix_withings_oauth_flows_state_hash" for index in table.indexes)
    assert any(index.name == "ix_withings_oauth_flows_expires_at" for index in table.indexes)


def test_withings_models_persist_defaults_and_relationships(db, user: User) -> None:
    connection = WithingsConnection(user_id=user.id)
    flow = WithingsOAuthFlow(
        user_id=user.id,
        state_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add_all([connection, flow])
    db.flush()
    db.refresh(connection)
    db.refresh(flow)

    assert connection.state == "not_connected"
    assert connection.sync_state == "idle"
    assert connection.encrypted_access_token is None
    assert connection.encrypted_refresh_token is None
    assert connection.granted_scopes == []
    assert connection.created_at is not None
    assert connection.updated_at is not None
    assert flow.created_at is not None
    assert flow.consumed_at is None
    assert db.scalar(select(WithingsConnection).where(WithingsConnection.user_id == user.id)) is connection
    assert db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.state_hash == "a" * 64)) is flow
    assert user.withings_connection is connection
    assert flow in user.withings_oauth_flows


def test_withings_connection_is_unique_per_user(db, user: User) -> None:
    db.add(WithingsConnection(user_id=user.id))
    db.flush()
    db.add(WithingsConnection(user_id=user.id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_withings_flow_consumed_at_round_trips(db, user: User) -> None:
    consumed_at = datetime.now(UTC)
    flow = WithingsOAuthFlow(
        id=uuid4(),
        user_id=user.id,
        state_hash="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        consumed_at=consumed_at,
    )
    db.add(flow)
    db.commit()
    loaded = db.get(WithingsOAuthFlow, flow.id)
    assert loaded is not None
    assert loaded.consumed_at is not None


def test_withings_relationship_foreign_key_metadata() -> None:
    mapper = inspect(User).mapper
    assert mapper.relationships["withings_connection"].uselist is False
    assert mapper.relationships["withings_oauth_flows"].uselist is True
