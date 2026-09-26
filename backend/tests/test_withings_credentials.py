from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select

from app.config import settings
from app.models import User, WithingsConnection, WithingsOAuthFlow
from app.schemas_withings import WithingsCredentialsInput, WithingsStatus
from app.services.credential_crypto import decrypt_credential, encrypt_credential
from app.withings.credentials import (
    WithingsCredentialError,
    WithingsCredentialUnavailableError,
    credential_pair_from_input,
    encrypt_token,
    resolve_withings_credentials,
)
from app.withings.service import (
    delete_withings_credentials,
    save_withings_credentials,
    withings_status,
)


def test_credentials_input_requires_a_complete_safe_pair() -> None:
    client_id = "withings-client-id"
    client_secret = "withings-client-secret"
    payload = WithingsCredentialsInput(client_id=client_id, client_secret=client_secret)

    assert credential_pair_from_input(payload) == (client_id, client_secret)
    assert client_id not in repr(payload)
    assert client_secret not in repr(payload)
    assert client_id not in str(payload.model_dump())
    assert client_secret not in str(payload.model_dump())

    incomplete_payload = WithingsCredentialsInput.model_construct(
        client_id=client_id,
        client_secret=None,
    )
    with pytest.raises(WithingsCredentialError) as captured:
        credential_pair_from_input(incomplete_payload)
    assert str(captured.value) == "credential_required"
    assert client_id not in str(captured.value)

    invalid_values = (
        {"client_id": "partial-client-id-sentinel"},
        {"client_secret": "partial-secret-sentinel"},
        {"client_id": client_id, "client_secret": None},
        {"client_id": client_id, "client_secret": ""},
        {"client_id": "  ", "client_secret": "blank-client-secret-sentinel"},
        {"client_id": "blank-client-id-sentinel", "client_secret": "  "},
        {
            "client_id": "control-client-id-sentinel\n",
            "client_secret": "safe-client-secret",
        },
        {
            "client_id": "safe-client-id",
            "client_secret": "control-client-secret-sentinel\n",
        },
        {
            "client_id": "overlong-client-id-sentinel" + "x" * 500,
            "client_secret": "safe-client-secret",
        },
        {
            "client_id": "safe-client-id",
            "client_secret": "overlong-client-secret-sentinel" + "x" * 500,
        },
    )
    for values in invalid_values:
        with pytest.raises(ValidationError) as captured:
            WithingsCredentialsInput(**values)
        rendered_error = str(captured.value) + repr(captured.value)
        for submitted_value in values.values():
            if isinstance(submitted_value, str) and submitted_value.strip():
                marker = submitted_value.splitlines()[0]
                assert marker not in rendered_error


def test_status_contains_only_safe_configuration_metadata(db, user, monkeypatch) -> None:
    client_id = "withings-status-client-id-sentinel"
    secret = "withings-status-secret-sentinel"
    redirect_uri = "https://example.test/withings/callback"
    monkeypatch.setattr(settings, "withings_enabled", True)
    monkeypatch.setattr(settings, "withings_redirect_uri", redirect_uri)
    db.add(
        WithingsConnection(
            user_id=user.id,
            client_id=client_id,
            encrypted_client_secret=encrypt_credential(secret),
        )
    )
    db.commit()

    status = withings_status(db, user)

    assert status.credentials_configured is True
    assert status.redirect_uri == redirect_uri
    rendered_status = repr(status) + str(status.model_dump())
    for credential in (client_id, secret):
        assert credential not in rendered_status
    assert "client_id" not in status.model_dump()
    assert "client_secret" not in status.model_dump()
    assert "encrypted_client_secret" not in status.model_dump()

    with pytest.raises(ValidationError) as captured:
        WithingsStatus(
            available=True,
            configured=True,
            credentials_configured=True,
            redirect_uri=redirect_uri,
            connected=False,
            state="not_connected",
            client_secret=secret,
        )
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_credentials_are_encrypted_and_resolved_from_each_passed_connection() -> None:
    connection_a = WithingsConnection(
        id=uuid4(),
        user_id=uuid4(),
        client_id="client-a",
        encrypted_client_secret=encrypt_credential("secret-a"),
    )
    connection_b = WithingsConnection(
        id=uuid4(),
        user_id=uuid4(),
        client_id="client-b",
        encrypted_client_secret=encrypt_credential("secret-b"),
    )

    assert connection_a.encrypted_client_secret != b"secret-a"
    assert decrypt_credential(connection_a.encrypted_client_secret) == "secret-a"
    assert resolve_withings_credentials(connection_a) == ("client-a", "secret-a")
    assert resolve_withings_credentials(connection_b) == ("client-b", "secret-b")


@pytest.mark.parametrize(
    "connection",
    (
        None,
        WithingsConnection(id=uuid4(), user_id=uuid4()),
        WithingsConnection(
            id=uuid4(),
            user_id=uuid4(),
            client_id="client-id",
            encrypted_client_secret=b"not-valid-ciphertext",
        ),
    ),
)
def test_unavailable_or_undecryptable_credentials_raise_bounded_error(connection) -> None:
    with pytest.raises(WithingsCredentialUnavailableError) as captured:
        resolve_withings_credentials(connection)

    assert str(captured.value) == "credential_unavailable"
    assert len(str(captured.value)) < 64
    assert "not-valid-ciphertext" not in str(captured.value)


def test_oauth_flow_can_bind_a_specific_user_connection() -> None:
    connection = WithingsConnection(id=uuid4(), user_id=uuid4())
    flow = WithingsOAuthFlow(
        id=uuid4(),
        user_id=connection.user_id,
        connection_id=connection.id,
        state_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    assert flow.connection_id == connection.id
    connection_column = WithingsConnection.__table__.c.client_id
    encrypted_column = WithingsConnection.__table__.c.encrypted_client_secret
    connection_id_column = WithingsOAuthFlow.__table__.c.connection_id
    assert connection_column.nullable
    assert encrypted_column.nullable
    assert connection_id_column.nullable
    assert connection_id_column.index
    assert {fk.target_fullname for fk in connection_id_column.foreign_keys} == {
        "withings_connections.id"
    }
    assert any(
        constraint.name == "uq_withings_connections_user_id"
        for constraint in WithingsConnection.__table__.constraints
    )
    assert inspect(WithingsOAuthFlow).mapper.column_attrs.connection_id


def _other_user(db) -> User:
    other = User(username="other-withings-user", password_hash="unused-test-hash")
    db.add(other)
    db.flush()
    return other


def test_saving_credentials_replaces_only_that_users_tokens_and_oauth_flows(
    db, user, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "withings_enabled", True)
    other = _other_user(db)
    now = datetime.now(UTC)
    connection_a = WithingsConnection(
        user_id=user.id,
        client_id="old-client-a",
        encrypted_client_secret=encrypt_credential("old-secret-a"),
        withings_user_id="withings-user-a",
        encrypted_access_token=encrypt_token("old-access-a"),
        encrypted_refresh_token=encrypt_token("old-refresh-a"),
        granted_scopes=["user.metrics", "user.activity"],
        state="active",
    )
    connection_b = WithingsConnection(
        user_id=other.id,
        client_id="client-b",
        encrypted_client_secret=encrypt_credential("secret-b"),
        withings_user_id="withings-user-b",
        encrypted_access_token=encrypt_token("access-b"),
        encrypted_refresh_token=encrypt_token("refresh-b"),
        granted_scopes=["user.metrics", "user.activity"],
        state="active",
    )
    db.add_all((connection_a, connection_b))
    db.flush()
    pending_a = WithingsOAuthFlow(
        user_id=user.id,
        connection_id=connection_a.id,
        state_hash="a" * 64,
        expires_at=now + timedelta(minutes=10),
    )
    pending_b = WithingsOAuthFlow(
        user_id=other.id,
        connection_id=connection_b.id,
        state_hash="b" * 64,
        expires_at=now + timedelta(minutes=10),
    )
    db.add_all((pending_a, pending_b))
    db.commit()

    result = save_withings_credentials(
        db,
        user,
        WithingsCredentialsInput(client_id="new-client-a", client_secret="new-secret-a"),
    )

    db.refresh(connection_a)
    db.refresh(connection_b)
    assert result.state == "not_connected"
    assert connection_a.client_id == "new-client-a"
    assert decrypt_credential(connection_a.encrypted_client_secret) == "new-secret-a"
    assert connection_a.withings_user_id is None
    assert connection_a.encrypted_access_token is None
    assert connection_a.encrypted_refresh_token is None
    assert connection_a.granted_scopes == []
    assert connection_b.client_id == "client-b"
    assert decrypt_credential(connection_b.encrypted_client_secret) == "secret-b"
    assert connection_b.withings_user_id == "withings-user-b"
    assert connection_b.encrypted_access_token is not None
    assert connection_b.encrypted_refresh_token is not None
    assert db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.id == pending_a.id)) is None
    assert db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.id == pending_b.id)) is not None
    assert withings_status(db, other).credentials_configured is True


def test_deleting_credentials_removes_only_that_users_connection_credentials_and_flows(
    db, user, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "withings_enabled", True)
    other = _other_user(db)
    now = datetime.now(UTC)
    connection_a = WithingsConnection(
        user_id=user.id,
        client_id="client-a",
        encrypted_client_secret=encrypt_credential("secret-a"),
        withings_user_id="withings-user-a",
        encrypted_access_token=encrypt_token("access-a"),
        encrypted_refresh_token=encrypt_token("refresh-a"),
        granted_scopes=["user.metrics", "user.activity"],
        state="active",
    )
    connection_b = WithingsConnection(
        user_id=other.id,
        client_id="client-b",
        encrypted_client_secret=encrypt_credential("secret-b"),
        withings_user_id="withings-user-b",
        encrypted_access_token=encrypt_token("access-b"),
        encrypted_refresh_token=encrypt_token("refresh-b"),
        granted_scopes=["user.metrics", "user.activity"],
        state="active",
    )
    db.add_all((connection_a, connection_b))
    db.flush()
    flow_a = WithingsOAuthFlow(
        user_id=user.id,
        connection_id=connection_a.id,
        state_hash="c" * 64,
        expires_at=now + timedelta(minutes=10),
    )
    flow_b = WithingsOAuthFlow(
        user_id=other.id,
        connection_id=connection_b.id,
        state_hash="d" * 64,
        expires_at=now + timedelta(minutes=10),
    )
    db.add_all((flow_a, flow_b))
    db.commit()

    result = delete_withings_credentials(db, user)

    db.refresh(connection_a)
    db.refresh(connection_b)
    assert result.credentials_configured is False
    assert connection_a.client_id is None
    assert connection_a.encrypted_client_secret is None
    assert connection_a.withings_user_id is None
    assert connection_a.encrypted_access_token is None
    assert connection_a.encrypted_refresh_token is None
    assert connection_b.client_id == "client-b"
    assert decrypt_credential(connection_b.encrypted_client_secret) == "secret-b"
    assert connection_b.withings_user_id == "withings-user-b"
    assert connection_b.encrypted_access_token is not None
    assert connection_b.encrypted_refresh_token is not None
    assert db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.id == flow_a.id)) is None
    assert db.scalar(select(WithingsOAuthFlow).where(WithingsOAuthFlow.id == flow_b.id)) is not None
    assert withings_status(db, other).credentials_configured is True
