from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zipfile import ZipFile

from app.auth.security import hash_password
from app.config import settings
from app.models import HealthSample, ImportBatch, SecurityAuditEvent, User, UserAchievement
from app.security_events import security_reference
from app.services.security_audit import security_audit_metrics_24h


def _login(client, username: str, password: str = "correct-horse-battery-staple") -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_admin_endpoints_are_protected_and_do_not_expose_user_secrets(client, user, db) -> None:
    user.is_admin = True
    db.commit()
    _login(client, "admin")
    response = client.get("/api/v1/admin/users")
    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert client.get("/api/v1/admin/overview").status_code == 200
    non_admin = User(username="normal-user", password_hash=hash_password("correct-horse-battery-staple"))

    db.add(non_admin)
    db.commit()
    client.cookies.clear()
    _login(client, "normal-user", "correct-horse-battery-staple")
    assert client.get("/api/v1/admin/overview").status_code == 403


def test_admin_audit_and_overview_return_persisted_login_activity(client, user, db) -> None:
    user.is_admin = True
    db.commit()
    _login(client, "admin")

    overview = client.get("/api/v1/admin/overview")
    assert overview.status_code == 200
    assert overview.json()["recent_events"][0]["event"] == "auth.login.succeeded"
    audit = client.get("/api/v1/admin/audit")
    assert audit.status_code == 200
    assert audit.json()["items"][0]["event"] == "auth.login.succeeded"


def test_system_and_overview_share_security_audit_metrics(client, user, db) -> None:
    user.is_admin = True
    db.commit()
    _login(client, "admin")
    db.query(SecurityAuditEvent).delete()
    db.commit()

    empty_system = client.get("/api/v1/admin/system")
    empty_overview = client.get("/api/v1/admin/overview")
    assert empty_system.status_code == 200
    assert empty_overview.status_code == 200
    assert empty_system.json()["security_audit_events_24h"] == 0
    assert empty_system.json()["failed_logins_24h"] == 0
    assert empty_system.json()["security_audit_retention_days"] == 90
    assert empty_overview.json()["failed_logins_24h"] == 0

    now = datetime.now(UTC)
    db.add_all(
        [
            SecurityAuditEvent(
                event="auth.login.succeeded",
                outcome="success",
                occurred_at=now,
            ),
            SecurityAuditEvent(
                event="auth.login.failed",
                outcome="failure",
                occurred_at=now,
            ),
            SecurityAuditEvent(
                event="auth.bearer.failure",
                outcome="failure",
                occurred_at=now,
            ),
            SecurityAuditEvent(
                event="admin.user.lifecycle_failed",
                outcome="failure",
                occurred_at=now,
            ),
            SecurityAuditEvent(
                event="auth.login.failed",
                outcome="failure",
                occurred_at=now - timedelta(hours=25),
            ),
        ]
    )
    db.commit()

    system = client.get("/api/v1/admin/system")
    overview = client.get("/api/v1/admin/overview")
    assert system.status_code == 200
    assert overview.status_code == 200
    assert system.json()["security_audit_events_24h"] == 4
    assert system.json()["failed_logins_24h"] == 1
    assert overview.json()["successful_logins_24h"] == 1
    assert overview.json()["failed_logins_24h"] == system.json()["failed_logins_24h"]


def test_security_audit_metrics_include_exact_twenty_four_hour_boundary(db) -> None:
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)
    cutoff = now - timedelta(hours=24)
    db.add_all(
        [
            SecurityAuditEvent(
                event="auth.login.succeeded",
                outcome="success",
                occurred_at=cutoff,
            ),
            SecurityAuditEvent(
                event="auth.login.failed",
                outcome="failure",
                occurred_at=cutoff,
            ),
            SecurityAuditEvent(
                event="auth.login.failed",
                outcome="failure",
                occurred_at=cutoff - timedelta(microseconds=1),
            ),
        ]
    )
    db.commit()

    metrics = security_audit_metrics_24h(db, now=now)

    assert metrics.audit_events == 2
    assert metrics.successful_logins == 1
    assert metrics.failed_logins == 1


def test_login_is_persisted_without_secrets(client, user, db) -> None:
    _login(client, "admin")
    db.expire_all()
    event = db.query(SecurityAuditEvent).filter_by(event="auth.login.succeeded").first()
    assert event is not None
    assert event.auth_method == "password"
    assert event.username_snapshot == security_reference("user", user.id)
    assert event.client_ip is None
    assert event.client_ref is not None


def test_security_audit_retention_removes_old_events(client, user, db) -> None:
    from datetime import UTC, datetime, timedelta

    db.add(SecurityAuditEvent(event="auth.login.failed", outcome="failure", occurred_at=datetime.now(UTC) - timedelta(days=91)))
    db.commit()
    _login(client, "admin")
    db.expire_all()
    assert db.query(SecurityAuditEvent).filter_by(event="auth.login.failed").count() == 0


def test_portable_backup_preview_apply_is_idempotent(client, user, db) -> None:
    user.language = "en"
    user.timezone = "UTC"
    db.commit()
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    user.language = "de"
    user.timezone = "Europe/Berlin"
    db.commit()
    preview = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("backup.zip", export.content, "application/zip")},
    )
    assert preview.status_code == 200
    assert preview.json()["status"] == "valid"
    apply = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("backup.zip", export.content, "application/zip")},
    )
    assert apply.status_code == 200
    assert apply.json()["status"] == "completed"
    second = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("backup.zip", export.content, "application/zip")},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    db.expire_all()
    assert db.query(UserAchievement).filter_by(user_id=user.id, achievement_key="welcome_back").count() == 1
    assert db.query(UserAchievement).filter_by(user_id=user.id, achievement_key="deja_vu").count() == 1
    db.refresh(user)
    assert user.language == "en"
    assert user.timezone == "UTC"


def test_portable_preview_rejects_oversized_jsonl_line(client, user, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_json_payload_bytes", 32)
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    invalid = io.BytesIO()
    oversized_line = b'{"value":"' + (b"x" * 64) + b'"}\n'
    with ZipFile(io.BytesIO(export.content)) as source, ZipFile(invalid, "w") as target:
        for name in source.namelist():
            target.writestr(
                name,
                oversized_line if name == "health_samples.jsonl" else source.read(name),
            )
    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("oversized-line.zip", invalid.getvalue(), "application/zip")},
    )

    assert response.status_code == 422

def test_portable_backup_rejects_out_of_domain_target(client, user) -> None:
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    invalid = io.BytesIO()
    with ZipFile(io.BytesIO(export.content)) as source, ZipFile(invalid, "w") as target:
        for name in source.namelist():
            if name == "targets.json":
                values = json.loads(source.read(name))
                values[0]["calories_kcal"] = "0"
                target.writestr(name, json.dumps(values))
            else:
                target.writestr(name, source.read(name))
    invalid.seek(0)

    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("invalid-target.zip", invalid.getvalue(), "application/zip")},
    )

    assert response.status_code == 422


def test_portable_preview_accepts_point_samples_from_own_export(client, user, db) -> None:
    batch = ImportBatch(user_id=user.id, source_type="apple_health_xml", status="completed")
    db.add(batch)
    db.flush()
    recorded_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id="point-sample",
            fingerprint="point-sample-fingerprint",
            source_type="apple_health_xml",
            source_name="Apple Health",
            source_identifier="phone-a",
            metric_type="dietary_energy_kcal",
            value=Decimal("123"),
            unit="kcal",
            original_value=Decimal("123"),
            original_unit="kcal",
            start_at=recorded_at,
            end_at=recorded_at,
            local_date=date(2026, 8, 1),
            timezone="Europe/Berlin",
        )
    )
    db.commit()
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    preview = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("backup.zip", export.content, "application/zip")},
    )
    assert preview.status_code == 200
    assert preview.json()["health_samples"] == 1
    invalid = io.BytesIO()
    with ZipFile(io.BytesIO(export.content)) as source, ZipFile(invalid, "w") as target:
        for name in source.namelist():
            if name == "health_samples.jsonl":
                sample = json.loads(source.read(name).splitlines()[0])
                sample["unit"] = "g"
                target.writestr(name, json.dumps(sample).encode() + b"\n")
            else:
                target.writestr(name, source.read(name))
    invalid.seek(0)
    invalid_preview = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("invalid-unit.zip", invalid.getvalue(), "application/zip")},
    )
    assert invalid_preview.status_code == 422


def test_portable_backup_validation_returns_client_error_for_invalid_fields(client, user) -> None:
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    invalid = io.BytesIO()
    with ZipFile(io.BytesIO(export.content)) as source, ZipFile(invalid, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"{}" if name == "manifest.json" else source.read(name))
    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("invalid.zip", invalid.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert "ungültige" in response.json()["detail"].lower()


def test_csv_export_is_separate_and_formula_safe(client, user, db) -> None:
    csrf = _login(client, "admin")
    del csrf
    response = client.get("/api/v1/settings/csv-export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert {"profile.csv", "targets.csv", "tracking-overrides.csv", "samples.csv"} <= set(archive.namelist())
        assert archive.read("targets.csv").startswith(b"valid_from,valid_to")
    assert db.query(UserAchievement).filter_by(
        user_id=user.id, achievement_key="spreadsheet_ready"
    ).count() == 1
