import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from app.ai import subscription_credentials
from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.subscription_credentials import (
    SubscriptionCredentialProblem,
    SubscriptionCredentialStore,
)
from app.ai.subscription_device_contracts import RefreshedSubscriptionGrant, SubscriptionGrant
from app.models import (
    AIActiveConnection,
    AIConnectionRetirement,
    AIProviderConnection,
    SubscriptionCredential,
    User,
)
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def grant(*, expires_in: int = 3600, subject: str = "synthetic-subject", workspace: str = "synthetic-workspace") -> SubscriptionGrant:
    now = datetime.now(UTC)
    return SubscriptionGrant(
        access_token=SecretStr("synthetic-access-token"),
        refresh_token=SecretStr("synthetic-refresh-token"),
        id_token=SecretStr("synthetic-id-token"),
        token_type="Bearer", expires_in=expires_in,
        scope="openid profile email offline_access api.connectors.read api.connectors.invoke",
        client_id=CLIENT_ID, subject=subject, workspace_id=workspace,
        received_at=now, expires_at=now + timedelta(seconds=expires_in),
    )


def setup_connection(db, user: User, *, model_id: str | None = None) -> tuple[SubscriptionCredentialStore, object]:
    connection_id = uuid4()
    store = SubscriptionCredentialStore(db, owner=user)
    store.create_draft(connection_id)
    record = db.get(AIProviderConnection, (user.id, connection_id))
    assert record is not None and not record.enabled and record.model_id is None
    if model_id:
        record.model_id = model_id
        db.commit()
    return store, connection_id


def test_grant_is_encrypted_owner_scoped_and_redacted(db, user):
    store, connection_id = setup_connection(db, user)
    metadata = store.save_verified(connection_id, grant())
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert metadata.state == "ready" and metadata.has_credential and metadata.credential_version == 1
    assert row is not None and row.encrypted_tokens is not None
    for plaintext in (
        b"synthetic-access-token", b"synthetic-refresh-token", b"synthetic-id-token",
        b"synthetic-subject", b"synthetic-workspace", CLIENT_ID.encode(),
    ):
        assert plaintext not in row.encrypted_tokens
        assert plaintext not in row.encrypted_binding
    assert "synthetic" not in repr(store.get_grant(connection_id))
    assert "subject" not in repr(metadata) and "workspace" not in repr(metadata)
    clear = decrypt_credential(row.encrypted_tokens)
    assert "synthetic-access-token" in clear and "synthetic-subject" in clear


def test_owner_connection_retirement_kind_and_account_state_are_checked(db, user):
    store, connection_id = setup_connection(db, user)
    other = User(username="second-owner-subscription", password_hash="synthetic")
    db.add(other)
    db.commit()
    with pytest.raises(SubscriptionCredentialProblem, match="connection_not_found"):
        SubscriptionCredentialStore(db, owner=other).save_verified(connection_id, grant())
    ProviderConnectionService(db, owner=user).delete(connection_id)
    with pytest.raises(SubscriptionCredentialProblem, match="connection_retired"):
        store.create_draft(connection_id)

    wrong_kind = uuid4()
    db.add(AIProviderConnection(
        user_id=user.id, id=wrong_kind, label="Other", provider_kind="openai_api",
        model_id=None, base_url=None, enabled=False, timeout_seconds=60, configuration_version=1,
    ))
    db.commit()
    with pytest.raises(SubscriptionCredentialProblem, match="wrong_provider_kind"):
        store.save_verified(wrong_kind, grant())

    user.is_active = False
    user.deactivated_at = datetime.now(UTC)
    db.commit()
    from app.services.user_operation_lock import InactiveUserOperation
    with pytest.raises(InactiveUserOperation, match="inactive"):
        store.create_draft(uuid4())


def test_verified_identity_is_immutable_and_delayed_completion_is_rejected(db, user):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    with pytest.raises(SubscriptionCredentialProblem, match="identity_mismatch"):
        store.save_verified(connection_id, grant(subject="different-subject"), expected_credential_version=1)
    with pytest.raises(SubscriptionCredentialProblem, match="credential_version_conflict"):
        store.save_verified(connection_id, grant(), expected_credential_version=0)
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row is not None and row.credential_version == 1 and row.state == "ready"


def test_encrypted_binding_cannot_be_swapped_between_connections(db, user):
    first_store, first_id = setup_connection(db, user)
    second_store, second_id = setup_connection(db, user)
    first_store.save_verified(first_id, grant(subject="synthetic-first-subject"))
    second_store.save_verified(second_id, grant(subject="synthetic-second-subject"))
    first = db.get(SubscriptionCredential, (user.id, first_id))
    second = db.get(SubscriptionCredential, (user.id, second_id))
    assert first is not None and second is not None
    first.encrypted_binding, second.encrypted_binding = second.encrypted_binding, first.encrypted_binding
    db.commit()
    with pytest.raises(SubscriptionCredentialProblem, match="identity_mismatch"):
        first_store.get_grant(first_id)
    assert first.state == "reauth_required" and first.encrypted_tokens is None


def test_fresh_grant_does_not_refresh_and_rotation_is_atomic(db, user):
    calls = []
    now = datetime.now(UTC)
    rotated = RefreshedSubscriptionGrant(
        access_token=SecretStr("rotated-access-token"),
        refresh_token=SecretStr("rotated-refresh-token"),
        id_token=SecretStr("rotated-id-token"), token_type="Bearer", expires_in=3600,
        scope=grant().scope, client_id=CLIENT_ID, subject="synthetic-subject",
        workspace_id="synthetic-workspace", received_at=now,
        expires_at=now + timedelta(hours=1),
    )
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    store._refresh = lambda _grant: calls.append("refresh") or rotated
    fresh = store.get_grant(connection_id, now=now)
    assert "synthetic-access-token" not in repr(fresh) and calls == []

    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row is not None
    refreshed = store.get_grant(connection_id, now=now + timedelta(minutes=59))
    assert calls == ["refresh"] and "rotated-access-token" not in repr(refreshed)
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row is not None and row.state == "ready" and row.credential_version == 3
    assert refreshed.credential_version == 3 and refreshed.connection_id == connection_id
    assert decrypt_credential(row.encrypted_tokens).find("rotated-refresh-token") >= 0


def test_ambiguous_refresh_is_claimed_once_and_terminal_requires_reauth(db, user):
    from app.ai.chatgpt_codex_device import ProtocolError

    calls = []
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant(expires_in=60))
    store._refresh = lambda _grant: (calls.append("refresh"), (_ for _ in ()).throw(ProtocolError("provider_failure")))[1]
    with pytest.raises(SubscriptionCredentialProblem, match="refresh_unconfirmed"):
        store.get_grant(connection_id)
    with pytest.raises(SubscriptionCredentialProblem, match="credential_not_ready"):
        store.get_grant(connection_id)
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert calls == ["refresh"] and row is not None
    assert row.state == "refresh_unconfirmed" and row.encrypted_tokens is None
    connection = db.get(AIProviderConnection, (user.id, connection_id))
    assert connection is not None and not connection.enabled
    with pytest.raises(SubscriptionCredentialProblem, match="credential_version_conflict"):
        store.save_verified(connection_id, grant(), expected_credential_version=1)

    other_store, other_connection = setup_connection(db, user)
    other_store.save_verified(other_connection, grant(expires_in=60))
    other_store._refresh = lambda _grant: (_ for _ in ()).throw(ProtocolError("refresh_terminal"))
    with pytest.raises(SubscriptionCredentialProblem, match="reauthentication_required"):
        other_store.get_grant(other_connection)
    record = db.get(AIProviderConnection, (user.id, other_connection))
    row = db.get(SubscriptionCredential, (user.id, other_connection))
    assert record is not None and not record.enabled
    assert row is not None and row.state == "reauth_required" and row.encrypted_tokens is None
    with pytest.raises(SubscriptionCredentialProblem, match="credential_version_conflict"):
        other_store.save_verified(other_connection, grant(), expected_credential_version=1)


def test_disconnect_commits_local_disable_before_single_best_effort_revoke(db, user):
    observations = []
    store, connection_id = setup_connection(db, user, model_id="synthetic-model")
    record = db.get(AIProviderConnection, (user.id, connection_id))
    assert record is not None
    store.save_verified(connection_id, grant())
    record.enabled = True
    db.add(AIActiveConnection(user_id=user.id, connection_id=connection_id))
    db.commit()

    def revoke(_grant):
        row = db.get(SubscriptionCredential, (user.id, connection_id))
        current = db.get(AIProviderConnection, (user.id, connection_id))
        active = db.get(AIActiveConnection, user.id)
        observations.append((row.state, row.encrypted_tokens, current.enabled, active))
        return False

    store._revoke = revoke
    result = store.disconnect(connection_id)
    assert result is not None and result.state == "revocation_unconfirmed"
    assert observations == [("revocation_pending", None, False, None)]
    assert store.disconnect(connection_id).state == "revocation_unconfirmed"
    assert len(observations) == 1
    ProviderConnectionService(db, owner=user).delete(connection_id)
    db.expire_all()
    assert db.get(AIProviderConnection, (user.id, connection_id)) is None


def test_successful_disconnect_allows_delete_and_identity_binding_stays_encrypted(db, user):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    with pytest.raises(ConnectionProblem, match="Trenne zuerst"):
        ProviderConnectionService(db, owner=user).delete(connection_id)
    db.commit()
    assert db.get(AIConnectionRetirement, (user.id, connection_id)) is None
    store._revoke = lambda _grant: True
    result = store.disconnect(connection_id)
    assert result is not None and result.state == "disconnected" and not result.has_credential
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row is not None and row.encrypted_tokens is None
    assert b"synthetic-subject" not in row.encrypted_binding
    ProviderConnectionService(db, owner=user).delete(connection_id)
    db.expire_all()
    assert db.get(SubscriptionCredential, (user.id, connection_id)) is None


@pytest.mark.parametrize("different_owner", [False, True])
@pytest.mark.parametrize("copy_binding", [False, True])
def test_token_envelopes_reject_cross_owner_and_connection_copy_even_for_same_identity(
    db, user, different_owner, copy_binding,
):
    source_store, source_id = setup_connection(db, user)
    target_owner = user
    if different_owner:
        target_owner = User(username="subscription-other-admin", password_hash="synthetic", is_admin=True)
        db.add(target_owner)
        db.commit()
    target_store = SubscriptionCredentialStore(db, owner=target_owner)
    target_id = source_id if different_owner else uuid4()
    target_store.create_draft(target_id)
    seed = grant()
    source_store.save_verified(source_id, seed)
    target_store.save_verified(target_id, seed)
    source = db.get(SubscriptionCredential, (user.id, source_id))
    target = db.get(SubscriptionCredential, (target_owner.id, target_id))
    target.encrypted_tokens = source.encrypted_tokens
    if copy_binding:
        target.encrypted_binding = source.encrypted_binding
    db.commit()
    with pytest.raises(SubscriptionCredentialProblem, match="identity_mismatch"):
        target_store.get_grant(target_id)
    assert target.state == "reauth_required" and target.encrypted_tokens is None
    assert source_store.get_metadata(source_id).state == "ready"


def test_old_token_envelope_cannot_be_replayed_under_new_generation(db, user):
    store, connection_id = setup_connection(db, user)
    seed = grant()
    store.save_verified(connection_id, seed)
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    old_ciphertext = row.encrypted_tokens
    store.save_verified(connection_id, seed, expected_credential_version=1)
    row.encrypted_tokens = old_ciphertext
    db.commit()
    with pytest.raises(SubscriptionCredentialProblem, match="identity_mismatch"):
        store.get_grant(connection_id)
    assert row.state == "reauth_required" and row.encrypted_tokens is None


def test_disconnect_invalidates_prior_authorization_generation(db, user):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    store._revoke = lambda _grant: True
    disconnected = store.disconnect(connection_id)
    assert disconnected.credential_version == 2
    with pytest.raises(SubscriptionCredentialProblem, match="credential_version_conflict"):
        store.save_verified(connection_id, grant(), expected_credential_version=1)
    assert store.get_metadata(connection_id).state == "disconnected"
    restored = store.save_verified(connection_id, grant(), expected_credential_version=2)
    assert restored.credential_version == 3 and restored.state == "ready"


def test_malformed_decrypted_token_payload_raises_static_failure(db, user):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    row.encrypted_tokens = encrypt_credential(json.dumps({
        "owner": str(user.id), "connection": str(connection_id), "credential_version": 1,
        "payload": "[]",
    }))
    db.commit()
    with pytest.raises(SubscriptionCredentialProblem, match=r"^credential_unavailable$"):
        store.get_grant(connection_id)
    assert row.state == "reauth_required" and row.encrypted_tokens is None


def test_disconnect_with_malformed_binding_clears_tokens_without_claiming_remote_success(db, user):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    row.encrypted_binding = encrypt_credential("[]")
    db.commit()
    store._revoke = lambda _grant: pytest.fail("malformed identity must not reach the provider")
    metadata = store.disconnect(connection_id)
    assert metadata.state == "revocation_unconfirmed" and row.encrypted_tokens is None


def test_disconnect_does_not_need_encryption_key_and_preserves_identity_binding(db, user, monkeypatch):
    from app.config import settings

    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant())
    store._revoke = lambda _grant: pytest.fail("unreadable credentials must not reach the provider")
    with monkeypatch.context() as patch:
        patch.setattr(settings, "credential_encryption_key", "")
        metadata = store.disconnect(connection_id)
    assert metadata.state == "revocation_unconfirmed" and metadata.credential_version == 2
    restored = store.save_verified(connection_id, grant(), expected_credential_version=2)
    assert restored.state == "ready" and restored.credential_version == 3


def _rotation_result():
    return RefreshedSubscriptionGrant(**grant().model_dump())


def test_crash_after_refresh_claim_never_reuses_refresh_token(db, user):
    class InterruptedProcess(BaseException):
        pass

    calls = []
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant(expires_in=60))

    def crash(_grant):
        assert not db.in_transaction()
        calls.append("refresh")
        raise InterruptedProcess

    store._refresh = crash
    with pytest.raises(InterruptedProcess):
        store.get_grant(connection_id)
    db.expire_all()
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row.state == "refresh_claimed" and row.credential_version == 2 and row.encrypted_tokens is None
    with pytest.raises(SubscriptionCredentialProblem, match="credential_not_ready"):
        store.get_grant(connection_id)
    assert calls == ["refresh"]


def test_encryption_failure_after_rotation_keeps_old_credentials_unusable(db, user, monkeypatch):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant(expires_in=60))
    calls = []
    store._refresh = lambda _grant: calls.append("refresh") or _rotation_result()

    def fail_encrypt(_value):
        raise CredentialEncryptionError("synthetic encryption failure")

    monkeypatch.setattr(subscription_credentials, "encrypt_credential", fail_encrypt)
    with pytest.raises(SubscriptionCredentialProblem, match="credential_unavailable"):
        store.get_grant(connection_id)
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    assert row.state == "refresh_unconfirmed" and row.encrypted_tokens is None
    with pytest.raises(SubscriptionCredentialProblem, match="credential_not_ready"):
        store.get_grant(connection_id)
    assert calls == ["refresh"]


@pytest.mark.parametrize("committed", [False, True])
def test_persistence_failure_after_rotation_never_repeats_refresh(db, user, monkeypatch, committed):
    store, connection_id = setup_connection(db, user)
    store.save_verified(connection_id, grant(expires_in=60))
    calls = []
    store._refresh = lambda _grant: calls.append("refresh") or _rotation_result()
    original_commit = db.commit
    injected = []

    def failing_commit():
        row = db.get(SubscriptionCredential, (user.id, connection_id))
        if row.state == "ready" and row.credential_version == 3 and not injected:
            injected.append(True)
            if committed:
                original_commit()
            raise OperationalError("synthetic commit failure", None, RuntimeError("synthetic"))
        original_commit()

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(SubscriptionCredentialProblem, match="credential_persistence_unconfirmed"):
        store.get_grant(connection_id)
    row = db.get(SubscriptionCredential, (user.id, connection_id))
    if committed:
        assert row.state == "ready" and row.credential_version == 3
        store.get_grant(connection_id)
    else:
        assert row.state == "refresh_claimed" and row.encrypted_tokens is None
        with pytest.raises(SubscriptionCredentialProblem, match="credential_not_ready"):
            store.get_grant(connection_id)
    assert calls == ["refresh"] and injected == [True]
