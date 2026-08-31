from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zipfile import ZipFile

import pytest

from app.auth.security import hash_password
from app.config import settings
from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    SecurityAuditEvent,
    TrackingOverride,
    User,
    UserAchievement,
    UserProfile,
)
from app.security_events import security_reference
from app.services.security_audit import security_audit_metrics_24h

PERSONAL_PROFILE_FIELDS = (
    "display_name",
    "gender",
    "birth_date",
    "height_cm",
    "diet_type",
    "health_notes",
    "intolerances",
)


def _login(client, username: str, password: str = "correct-horse-battery-staple") -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]

def _as_portable_version_archive(export_content: bytes, version: int) -> bytes:
    versioned_archive = io.BytesIO()
    with ZipFile(io.BytesIO(export_content)) as source, ZipFile(versioned_archive, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(content)
                manifest["format_version"] = version
                content = json.dumps(manifest).encode()
            elif name == "targets.json" and version in (1, 2):
                targets_data = json.loads(content)
                for target_data in targets_data:
                    target_data.pop("target_weight_min_kg", None)
                    target_data.pop("target_weight_max_kg", None)
                content = json.dumps(targets_data).encode()
            elif name == "profile.json" and version == 1:
                profile_data = json.loads(content)
                for field in PERSONAL_PROFILE_FIELDS:
                    profile_data.pop(field)
                content = json.dumps(profile_data).encode()
            target.writestr(name, content)
    return versioned_archive.getvalue()


def _as_portable_v1_archive(export_content: bytes) -> bytes:
    return _as_portable_version_archive(export_content, 1)


def _rewrite_targets_archive(export_content: bytes, transform) -> bytes:
    rewritten_archive = io.BytesIO()
    with ZipFile(io.BytesIO(export_content)) as source, ZipFile(rewritten_archive, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "targets.json":
                targets = json.loads(content)
                transform(targets)
                content = json.dumps(targets).encode()
            target.writestr(name, content)
    return rewritten_archive.getvalue()


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
    db.add(
        UserProfile(
            user_id=user.id,
            display_name="Portable Ada",
            gender="female",
            birth_date=date(1990, 4, 5),
            height_cm=Decimal("171.25"),
            diet_type="pescetarian",
            health_notes="Portable health note",
            intolerances="Portable intolerance",
        )
    )
    db.commit()
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    user.language = "de"
    user.timezone = "Europe/Berlin"
    profile = db.get(UserProfile, user.id)
    assert profile is not None
    profile.display_name = "Changed after export"
    profile.gender = None
    profile.birth_date = None
    profile.height_cm = None
    profile.diet_type = None
    profile.health_notes = None
    profile.intolerances = None
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
    restored_profile = db.get(UserProfile, user.id)
    assert restored_profile is not None
    assert restored_profile.display_name == "Portable Ada"
    assert restored_profile.gender == "female"
    assert restored_profile.birth_date == date(1990, 4, 5)
    assert restored_profile.height_cm == Decimal("171.25")
    assert restored_profile.diet_type == "pescetarian"
    assert restored_profile.health_notes == "Portable health note"
    assert restored_profile.intolerances == "Portable intolerance"

def test_portable_preview_rejects_aggregate_record_limit(client, user, db, monkeypatch) -> None:
    db.add_all(
        [
            TrackingOverride(
                user_id=user.id,
                local_date=date(2026, 8, 12),
                status="complete",
            ),
            UserAchievement(
                user_id=user.id,
                achievement_key="aggregate-limit-regression",
                unlocked_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(settings, "max_import_records", 2)
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    with ZipFile(io.BytesIO(export.content)) as archive:
        assert len(json.loads(archive.read("targets.json"))) == 1
        assert len(json.loads(archive.read("tracking_overrides.json"))) == 1
        assert len(json.loads(archive.read("achievements.json"))) == 1
    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("aggregate-limit.zip", export.content, "application/zip")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Die Sicherung enthält zu viele Datensätze"


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        (Decimal("75"), Decimal("75")),
        (Decimal("70.125"), Decimal("82.500")),
        (None, None),
    ],
)
def test_portable_v3_target_weight_round_trip(
    client,
    user,
    db,
    minimum: Decimal | None,
    maximum: Decimal | None,
) -> None:
    target = db.query(NutritionTarget).filter_by(user_id=user.id).one()
    target.target_weight_min_kg = minimum
    target.target_weight_max_kg = maximum
    db.commit()
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    with ZipFile(io.BytesIO(export.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        exported_targets = json.loads(archive.read("targets.json"))
    assert manifest["format_version"] == 3
    assert exported_targets[0]["target_weight_min_kg"] == (
        None if minimum is None else f"{minimum:.3f}"
    )
    assert exported_targets[0]["target_weight_max_kg"] == (
        None if maximum is None else f"{maximum:.3f}"
    )

    target.target_weight_min_kg = None
    target.target_weight_max_kg = None
    db.commit()
    preview = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("v3-target.zip", export.content, "application/zip")},
    )
    apply = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("v3-target.zip", export.content, "application/zip")},
    )
    assert preview.status_code == 200
    assert preview.json()["status"] == "valid"
    assert apply.status_code == 200
    assert apply.json()["status"] == "completed"
    db.expire_all()
    restored = db.query(NutritionTarget).filter_by(user_id=user.id).one()
    assert restored.target_weight_min_kg == minimum
    assert restored.target_weight_max_kg == maximum


def test_portable_v3_rejects_missing_target_weight_keys(client, user, db) -> None:
    del user, db
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    invalid = _rewrite_targets_archive(
        export.content,
        lambda targets: (
            targets[0].pop("target_weight_min_kg"),
            targets[0].pop("target_weight_max_kg"),
        ),
    )
    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("missing-v3-target-weights.zip", invalid, "application/zip")},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        ("80", None),
        (None, "80"),
        ("80", "70"),
        ("0", "80"),
        ("80", "1001"),
        ("NaN", "80"),
        ("Infinity", "100"),
    ],
)
def test_portable_v3_rejects_invalid_target_weight_pair(
    client,
    user,
    db,
    minimum: str | None,
    maximum: str | None,
) -> None:
    del user, db
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    invalid = _rewrite_targets_archive(
        export.content,
        lambda targets: targets[0].update(
            target_weight_min_kg=minimum,
            target_weight_max_kg=maximum,
        ),
    )
    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("invalid-v3-target-weights.zip", invalid, "application/zip")},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("version", [1, 2])
def test_portable_legacy_targets_restore_null_weights(client, user, db, version: int) -> None:
    target = db.query(NutritionTarget).filter_by(user_id=user.id).one()
    target.target_weight_min_kg = Decimal("70")
    target.target_weight_max_kg = Decimal("80")
    db.commit()
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    legacy_archive = _as_portable_version_archive(export.content, version)
    target.target_weight_min_kg = Decimal("60")
    target.target_weight_max_kg = Decimal("90")
    db.commit()
    preview = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": (f"v{version}-target.zip", legacy_archive, "application/zip")},
    )
    apply = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": (f"v{version}-target.zip", legacy_archive, "application/zip")},
    )
    assert preview.status_code == 200
    assert apply.status_code == 200
    db.expire_all()
    restored = db.query(NutritionTarget).filter_by(user_id=user.id).one()
    assert restored.target_weight_min_kg is None
    assert restored.target_weight_max_kg is None


def test_portable_backup_v1_preserves_existing_personal_profile(
    client,
    user,
    db,
) -> None:
    expected_profile = {
        "display_name": "Existing profile",
        "gender": "other",
        "birth_date": date(1980, 1, 2),
        "height_cm": Decimal("180.00"),
        "diet_type": "vegetarian",
        "health_notes": "Existing note",
        "intolerances": "Existing intolerance",
    }
    db.add(UserProfile(user_id=user.id, **expected_profile))
    db.commit()
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    v1_archive = _as_portable_v1_archive(export.content)

    preview = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("v1-backup.zip", v1_archive, "application/zip")},
    )
    apply = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("v1-backup.zip", v1_archive, "application/zip")},
    )

    assert preview.status_code == 200
    assert preview.json()["status"] == "valid"
    assert apply.status_code == 200
    assert apply.json()["status"] == "completed"
    db.expire_all()
    stored = db.get(UserProfile, user.id)
    assert stored is not None
    for field, expected in expected_profile.items():
        assert getattr(stored, field) == expected


def test_portable_backup_v1_does_not_create_personal_profile(
    client,
    user,
    db,
) -> None:
    assert db.get(UserProfile, user.id) is None
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    v1_archive = _as_portable_v1_archive(export.content)

    apply = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("v1-backup.zip", v1_archive, "application/zip")},
    )

    assert apply.status_code == 200
    assert apply.json()["status"] == "completed"
    db.expire_all()
    assert db.get(UserProfile, user.id) is None


def test_portable_backup_v2_null_profile_is_current_user_scoped(
    client,
    user,
    db,
) -> None:
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    db.add(
        UserProfile(
            user_id=user.id,
            display_name="Clear me",
            gender="female",
            birth_date=date(1990, 1, 1),
            height_cm=Decimal("170.00"),
            diet_type="vegan",
            health_notes="Clear this note",
            intolerances="Clear this intolerance",
        )
    )
    other = User(
        username="portable-profile-other",
        password_hash=hash_password("correct-horse-battery-staple"),
    )
    db.add(other)
    db.flush()
    db.add(
        UserProfile(
            user_id=other.id,
            display_name="Other profile remains",
            health_notes="Other note remains",
        )
    )
    db.commit()

    apply = client.post(
        "/api/v1/import/calo/apply",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("v2-backup.zip", export.content, "application/zip")},
    )

    assert apply.status_code == 200
    assert apply.json()["status"] == "completed"
    db.expire_all()
    stored = db.get(UserProfile, user.id)
    assert stored is not None
    assert all(getattr(stored, field) is None for field in PERSONAL_PROFILE_FIELDS)
    other_stored = db.get(UserProfile, other.id)
    assert other_stored is not None
    assert other_stored.display_name == "Other profile remains"
    assert other_stored.health_notes == "Other note remains"


@pytest.mark.parametrize(
    "profile_patch",
    [
        {"gender": "PRIVATE-invalid-gender"},
        {"birth_date": "2999-01-01"},
        {"PRIVATE-unknown-field": "PRIVATE-unknown-value"},
    ],
)
def test_portable_backup_v2_strictly_rejects_invalid_personal_profile(
    client,
    user,
    profile_patch,
) -> None:
    del user
    csrf = _login(client, "admin")
    export = client.get("/api/v1/settings/export")
    assert export.status_code == 200
    invalid = io.BytesIO()
    with ZipFile(io.BytesIO(export.content)) as source, ZipFile(invalid, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "profile.json":
                profile_data = json.loads(content)
                profile_data.update(profile_patch)
                content = json.dumps(profile_data).encode()
            target.writestr(name, content)

    response = client.post(
        "/api/v1/import/calo/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("invalid-v2-profile.zip", invalid.getvalue(), "application/zip")},
    )

    assert response.status_code == 422
    assert "PRIVATE-" not in response.text


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
