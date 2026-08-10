import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app import security_events
from app.auth.security import create_api_token, create_session
from app.cli import configure_yazio, create_token, disable_yazio, reset_mfa, seed_demo
from app.database import SessionLocal
from app.importers.json_adapter import AdapterResult
from app.models import (
    ApiToken,
    HealthSample,
    ImportBatch,
    ImportError,
    MfaRecoveryCode,
    NutritionTarget,
    PasskeyCredential,
    RateLimitBucket,
    RawImportPayload,
    TrackingOverride,
    TrackingQualitySettings,
    User,
    UserInvitation,
    UserSession,
    UserTotpCredential,
    WebAuthnChallenge,
    WebAuthnUserHandle,
    YazioConnection,
)
from app.services.import_service import persist_import
from app.services.rate_limit import hash_rate_limit_key, normalize_account_identifier
from app.services.user_lifecycle import (
    UserLifecycleRejected,
    deactivate_user,
    delete_user,
    reactivate_user,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    exclusive_user_lifecycle_operation,
    shared_user_operation,
)
from app.services.yazio_sync import YazioSyncError, configure_yazio_connection, sync_yazio_user

PASSWORD = "correct-horse-battery-staple"


def _add_user(
    db: OrmSession,
    template: User,
    username: str,
    *,
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash=template.password_hash,
        is_admin=is_admin,
        is_active=is_active,
        deactivated_at=None if is_active else datetime(2026, 8, 10, tzinfo=UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _capture_security_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    monkeypatch.setattr(
        security_events.logger,
        "log",
        lambda _level, message: records.append(json.loads(message)),
    )
    return records


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _seed_owned_data(
    db: OrmSession,
    target: User,
) -> tuple[str, str, UserInvitation, YazioConnection]:
    session, raw_session, _ = create_session(db, target)
    token, raw_token = create_api_token(db, target, "lifecycle-test")
    invitation = UserInvitation(
        token_hash="1" * 64,
        invited_by_user_id=target.id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    connection = YazioConnection(
        user_id=target.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier=f"yazio:{target.id}",
        sync_enabled=True,
        sync_interval_minutes=360,
        sync_days=7,
        next_sync_at=datetime.now(UTC) + timedelta(hours=1),
    )
    batch = ImportBatch(
        user_id=target.id,
        source_type="calograph_json",
        status="completed",
        received=1,
        inserted=1,
    )
    db.add_all(
        [
            invitation,
            connection,
            UserTotpCredential(
                user_id=target.id,
                encrypted_secret=b"encrypted-totp",
                enabled_at=datetime.now(UTC),
            ),
            MfaRecoveryCode(user_id=target.id, code_hash="2" * 64),
            WebAuthnUserHandle(user_id=target.id, user_handle=b"user-handle"),
            PasskeyCredential(
                user_id=target.id,
                label="Testschlüssel",
                credential_id=b"credential-id",
                public_key=b"public-key",
                sign_count=0,
                transports=["internal"],
                device_type="single_device",
                backed_up=False,
            ),
            WebAuthnChallenge(
                purpose="authentication",
                challenge=b"challenge",
                user_id=target.id,
                session_id=session.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
            NutritionTarget(
                user_id=target.id,
                valid_from=date(2026, 8, 1),
                calories_kcal=Decimal("2000"),
                protein_g=Decimal("120"),
            ),
            TrackingQualitySettings(user_id=target.id),
            TrackingOverride(
                user_id=target.id,
                local_date=date(2026, 8, 1),
                status="complete",
            ),
            batch,
        ]
    )
    db.flush()
    db.add_all(
        [
            RawImportPayload(
                batch_id=batch.id,
                content_type="application/json",
                compressed_payload=b"compressed",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            ),
            ImportError(
                batch_id=batch.id,
                item_index=1,
                metric_type="dietary_energy_kcal",
                error_code="invalid",
                safe_detail="Ungültiger Testwert",
            ),
            HealthSample(
                user_id=target.id,
                import_batch_id=batch.id,
                external_sample_id="sample-1",
                fingerprint="3" * 64,
                source_type="calograph_json",
                source_identifier="test-source",
                metric_type="dietary_energy_kcal",
                value=Decimal("2000"),
                unit="kcal",
                original_value=Decimal("2000"),
                original_unit="kcal",
                start_at=datetime(2026, 8, 1, tzinfo=UTC),
                end_at=datetime(2026, 8, 1, tzinfo=UTC),
                local_date=date(2026, 8, 1),
                timezone="Europe/Berlin",
            ),
        ]
    )
    target_rate_keys = {
        f"user:{target.id}",
        f"account:{normalize_account_identifier(target.username)}",
        f"token:{token.id}",
    }
    for index, key in enumerate(sorted(target_rate_keys)):
        db.add(
            RateLimitBucket(
                key_hash=hash_rate_limit_key(key),
                action=f"lifecycle-{index}",
                window_start=datetime(2026, 8, 10, tzinfo=UTC),
            )
        )
    db.add(
        RateLimitBucket(
            key_hash=hash_rate_limit_key("unrelated"),
            action="unrelated",
            window_start=datetime(2026, 8, 10, tzinfo=UTC),
        )
    )
    db.commit()
    db.refresh(invitation)
    db.refresh(connection)
    return raw_session, raw_token, invitation, connection


def test_deactivate_and_reactivate_preserve_data_but_invalidate_access(
    client: TestClient,
    user: User,
    db: OrmSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    target = _add_user(db, user, "lifecycle-target")
    other = _add_user(db, user, "unaffected-user")
    raw_session, raw_token, invitation, connection = _seed_owned_data(db, target)
    changed_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    events = _capture_security_events(monkeypatch)

    deactivated = deactivate_user(db, user.id, target.id, now=changed_at)
    db.expire_all()

    assert deactivated.id == target.id
    stored_target = db.get(User, target.id)
    assert stored_target is not None
    assert stored_target.is_active is False
    assert _as_utc(stored_target.deactivated_at) == changed_at
    assert (
        db.scalar(
            select(func.count()).select_from(UserSession).where(UserSession.user_id == target.id)
        )
        == 0
    )
    stored_token = db.scalar(select(ApiToken).where(ApiToken.user_id == target.id))
    assert stored_token is not None
    assert _as_utc(stored_token.revoked_at) == changed_at
    assert _as_utc(db.get(UserInvitation, invitation.id).revoked_at) == changed_at
    stored_connection = db.get(YazioConnection, connection.id)
    assert stored_connection is not None
    assert stored_connection.sync_enabled is False
    assert stored_connection.next_sync_at is None
    assert db.get(UserTotpCredential, target.id) is not None
    assert (
        db.scalar(
            select(func.count())
            .select_from(PasskeyCredential)
            .where(PasskeyCredential.user_id == target.id)
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count()).select_from(HealthSample).where(HealthSample.user_id == target.id)
        )
        == 1
    )
    assert db.get(User, other.id).is_active is True

    client.cookies.set("calograph_session", raw_session)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": target.username, "password": PASSWORD},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/import/apple-health/validate",
            headers={"Authorization": f"Bearer {raw_token}"},
            json={},
        ).status_code
        == 401
    )

    deactivate_user(db, user.id, target.id, now=changed_at + timedelta(days=1))
    db.expire_all()
    assert _as_utc(db.get(User, target.id).deactivated_at) == changed_at
    reactivated = reactivate_user(db, user.id, target.id)
    db.expire_all()

    assert reactivated.is_active is True
    assert reactivated.deactivated_at is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(ApiToken)
            .where(ApiToken.user_id == target.id, ApiToken.revoked_at.is_(None))
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count()).select_from(UserSession).where(UserSession.user_id == target.id)
        )
        == 0
    )
    assert _as_utc(db.get(UserInvitation, invitation.id).revoked_at) == changed_at
    assert db.get(YazioConnection, connection.id).sync_enabled is False
    assert client.post(
        "/api/v1/auth/login",
        json={"username": target.username, "password": PASSWORD},
    ).json() == {"mfa_required": True}
    lifecycle_events = [event for event in events if str(event["event"]).startswith("admin.user.")]
    assert [event["event"] for event in lifecycle_events] == [
        "admin.user.deactivated",
        "admin.user.deactivated",
        "admin.user.reactivated",
    ]
    serialized = json.dumps(events)
    assert target.username not in serialized
    assert str(user.id) not in serialized
    assert str(target.id) not in serialized
    assert PASSWORD not in serialized


def test_reactivating_an_active_user_preserves_enabled_yazio_connection(
    user: User,
    db: OrmSession,
) -> None:
    user.is_admin = True
    target = _add_user(db, user, "already-active-target")
    connection = YazioConnection(
        user_id=target.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier=f"yazio:{target.id}",
        sync_enabled=True,
        sync_interval_minutes=360,
        sync_days=7,
        next_sync_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    db.add(connection)
    db.commit()

    reactivated = reactivate_user(db, user.id, target.id)
    db.expire_all()

    assert reactivated.is_active is True
    assert reactivated.deactivated_at is None
    stored_connection = db.get(YazioConnection, connection.id)
    assert stored_connection is not None
    assert stored_connection.sync_enabled is True
    assert _as_utc(stored_connection.next_sync_at) == datetime(2026, 8, 11, tzinfo=UTC)


def test_hard_delete_requires_inactive_target_and_cascades_owned_data(
    user: User,
    db: OrmSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    target = _add_user(db, user, "delete-target")
    _seed_owned_data(db, target)
    target_id = target.id
    events = _capture_security_events(monkeypatch)

    with pytest.raises(UserLifecycleRejected, match="target_active"):
        delete_user(db, user.id, target.id)
    deactivate_user(db, user.id, target.id)
    delete_user(db, user.id, target.id)
    db.expire_all()

    assert db.get(User, target_id) is None
    direct_user_models = (
        ApiToken,
        HealthSample,
        ImportBatch,
        MfaRecoveryCode,
        NutritionTarget,
        PasskeyCredential,
        TrackingOverride,
        TrackingQualitySettings,
        UserInvitation,
        UserSession,
        UserTotpCredential,
        WebAuthnChallenge,
        WebAuthnUserHandle,
        YazioConnection,
    )
    for model in direct_user_models:
        user_column = model.invited_by_user_id if model is UserInvitation else model.user_id
        assert (
            db.scalar(select(func.count()).select_from(model).where(user_column == target_id)) == 0
        )
    assert db.scalar(select(func.count()).select_from(RawImportPayload)) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(RateLimitBucket)) == 1
    assert [event["event"] for event in events] == [
        "admin.user.lifecycle_rejected",
        "admin.user.deactivated",
        "admin.user.deleted",
    ]


def test_lifecycle_api_enforces_admin_self_and_active_target_contracts(
    client: TestClient,
    user: User,
    db: OrmSession,
) -> None:
    user.is_admin = True
    second_admin = _add_user(db, user, "second-admin", is_admin=True)
    regular = _add_user(db, user, "regular-user")
    viewer = _add_user(db, user, "viewer")
    db.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
    )
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    self_action = client.post(f"/api/v1/users/{user.id}/deactivate", headers=headers)
    assert self_action.status_code == 409
    assert self_action.json()["detail"] == "Die Aktion auf dem eigenen Konto ist nicht erlaubt."
    active_delete = client.delete(f"/api/v1/users/{regular.id}", headers=headers)
    assert active_delete.status_code == 409
    assert (
        client.post(f"/api/v1/users/{second_admin.id}/deactivate", headers=headers).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/users/{second_admin.id}/reactivate", headers=headers).status_code
        == 200
    )
    assert client.post(f"/api/v1/users/{regular.id}/deactivate", headers=headers).status_code == 200
    assert client.delete(f"/api/v1/users/{regular.id}", headers=headers).status_code == 204
    assert client.post(f"/api/v1/users/{regular.id}/reactivate", headers=headers).status_code == 404

    client.cookies.clear()
    _, viewer_session, viewer_csrf = create_session(db, viewer)
    client.cookies.set("calograph_session", viewer_session)
    forbidden = client.post(
        f"/api/v1/users/{second_admin.id}/deactivate",
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert forbidden.status_code == 403
    assert db.get(User, second_admin.id).is_active is True


def test_inactive_user_is_rejected_by_all_operator_mutation_paths(
    user: User,
    db: OrmSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _add_user(db, user, "inactive-operator", is_active=False)

    with pytest.raises(InactiveUserOperation):
        create_api_token(db, target, "blocked")
    with pytest.raises(InactiveUserOperation):
        persist_import(
            db,
            target,
            AdapterResult(source_type="calograph_json"),
            None,
            "application/json",
            "inactive-test",
        )
    with pytest.raises(YazioSyncError, match="nicht aktiv"):
        configure_yazio_connection(
            target,
            "owner@example.com",
            "secret",
            sync_interval_minutes=360,
            sync_days=7,
        )
    with pytest.raises(InactiveUserOperation):
        create_session(db, target)
    credential_validation_called = False

    def validate_credentials(*_args, **_kwargs):
        nonlocal credential_validation_called
        credential_validation_called = True

    monkeypatch.setattr("app.cli.validate_yazio_credentials", validate_credentials)
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: "secret")
    with pytest.raises(SystemExit, match="nicht konfiguriert"):
        configure_yazio(
            argparse.Namespace(
                username=target.username,
                email="owner@example.com",
                interval_hours=6,
                days=7,
            )
        )
    assert credential_validation_called is False

    fetch_called = False

    def fetcher(*_args, **_kwargs):
        nonlocal fetch_called
        fetch_called = True
        return {}

    with pytest.raises(YazioSyncError, match="nicht aktiv"):
        sync_yazio_user(
            target,
            "owner@example.com",
            "secret",
            date(2026, 8, 1),
            date(2026, 8, 1),
            fetcher=fetcher,
        )
    assert fetch_called is False
    connection = YazioConnection(
        user_id=target.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier=f"yazio:{target.id}",
        sync_enabled=True,
        sync_interval_minutes=360,
        sync_days=7,
    )
    db.add(connection)
    db.commit()

    monkeypatch.setattr("app.cli.SessionLocal", SessionLocal)
    with pytest.raises(SystemExit, match="inaktiven Benutzer"):
        create_token(argparse.Namespace(username=target.username, label="blocked"))
    with pytest.raises(SystemExit, match="MFA nicht zurückgesetzt"):
        reset_mfa(
            argparse.Namespace(
                username=target.username,
                confirm=target.username,
            )
        )
    with pytest.raises(SystemExit, match="keine Demodaten"):
        seed_demo(argparse.Namespace(username=target.username))
    with pytest.raises(SystemExit, match="nicht deaktiviert"):
        disable_yazio(argparse.Namespace(username=target.username))
    db.refresh(connection)
    assert connection.sync_enabled is True
    assert (
        db.scalar(select(func.count()).select_from(ApiToken).where(ApiToken.user_id == target.id))
        == 0
    )


def test_lifecycle_rollback_emits_failure_but_no_success_event(
    user: User,
    db: OrmSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user.is_admin = True
    target = _add_user(db, user, "rollback-target", is_active=False)
    db.add(
        ApiToken(
            user_id=target.id,
            label="rollback-token",
            token_prefix="rollback",
            token_hash="4" * 64,
        )
    )
    db.commit()
    events = _capture_security_events(monkeypatch)

    def fail_commit(_session: OrmSession) -> None:
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(OrmSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        delete_user(db, user.id, target.id)
    db.expire_all()

    assert db.get(User, target.id) is not None
    assert (
        db.scalar(select(func.count()).select_from(ApiToken).where(ApiToken.user_id == target.id))
        == 1
    )
    assert [event["event"] for event in events] == ["admin.user.lifecycle_failed"]


def test_two_concurrent_admin_deactivations_cannot_both_succeed(
    user: User,
    db: OrmSession,
) -> None:
    user.is_admin = True
    second_admin = _add_user(db, user, "concurrent-admin", is_admin=True)
    db.commit()
    barrier = Barrier(2)

    def run(actor_id, target_id) -> str:
        barrier.wait()
        with SessionLocal() as worker_db:
            try:
                deactivate_user(worker_db, actor_id, target_id)
            except UserLifecycleRejected as exc:
                return exc.reason
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: run(*pair),
                [(user.id, second_admin.id), (second_admin.id, user.id)],
            )
        )

    db.expire_all()
    assert results.count("success") == 1
    assert sum(db.scalars(select(User.is_active).where(User.is_admin.is_(True)))) == 1



def test_local_shared_lock_supports_cross_thread_dependency_teardown(
    user: User,
    db: OrmSession,
) -> None:
    target = _add_user(db, user, "cross-thread-lock-target")
    manager = shared_user_operation(db, target.id)
    assert manager.__enter__().id == target.id

    def nested_shared_operation() -> None:
        with (
            SessionLocal() as worker_db,
            shared_user_operation(worker_db, target.id),
        ):
            pass

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(nested_shared_operation).result()
        assert executor.submit(manager.__exit__, None, None, None).result() is False

    with exclusive_user_lifecycle_operation(db, target.id):
        pass