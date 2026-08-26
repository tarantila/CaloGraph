from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from queue import Empty, Queue
from threading import Event, Thread
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app.api import settings as settings_api
from app.auth.security import hash_password
from app.models import (
    AccountRecoveryToken,
    ApiToken,
    HealthSample,
    ImportBatch,
    MfaRecoveryCode,
    NutritionTarget,
    PasskeyCredential,
    TrackingOverride,
    User,
    UserAchievement,
    UserSession,
    UserTotpCredential,
    YazioConnection,
)
from app.problem_types import DATA_EXPORT_BUSY
from app.services import data_export


def _login(client: TestClient, username: str = "admin") -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200


def _archive(response) -> ZipFile:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment; filename=\"calograph-data-export-" in response.headers[
        "content-disposition"
    ]
    assert response.headers["x-accel-buffering"] == "no"
    return ZipFile(io.BytesIO(response.content))


def _add_sample(db, user: User, batch: ImportBatch, *, external_id: str, value: Decimal) -> None:
    db.add(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=external_id,
            fingerprint=f"fingerprint-{external_id}",
            source_type="apple_health_xml",
            source_name="Apple Health",
            source_identifier="phone-a",
            metric_type="dietary_energy_kcal",
            value=value,
            unit="kcal",
            original_value=value,
            original_unit="kcal",
            start_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
            end_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
            local_date=date(2026, 8, 1),
            timezone="Europe/Berlin",
        )
    )


def test_data_export_requires_an_authenticated_session(client: TestClient) -> None:
    download_id = uuid4()
    response = client.get(f"/api/v1/settings/export?download_id={download_id}")
    assert response.status_code == 401
    assert f"calograph_export_status_{download_id.hex}=unauthenticated" in response.headers["set-cookie"]


def test_data_export_rejects_a_second_concurrent_request(
    client: TestClient,
    user: User,
    monkeypatch,
) -> None:
    del user
    _login(client)

    def reject_export(_user_id, **_kwargs) -> None:
        raise data_export.ExportBusy
    monkeypatch.setattr(settings_api, "open_user_export", reject_export)
    download_id = uuid4()
    response = client.get(f"/api/v1/settings/export?download_id={download_id}")

    assert response.status_code == 429
    assert f"calograph_export_status_{download_id.hex}=busy" in response.headers["set-cookie"]
    assert response.headers["retry-after"] == "30"
    assert response.json()["type"] == DATA_EXPORT_BUSY
    assert response.json()["detail"] == (
        "Ein anderer Datenexport läuft bereits. Bitte versuche es in Kürze erneut."
    )


@pytest.mark.asyncio
async def test_data_export_releases_slot_when_response_fails_before_body() -> None:
    stream = data_export.open_user_export(uuid4())
    response = settings_api.ExportStreamingResponse(stream, media_type="application/zip")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            raise OSError("client disconnected")

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "method": "GET", "path": "/export", "headers": [], "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

    replacement = data_export.open_user_export(uuid4())
    replacement.close()


def test_data_export_keeps_slot_until_cancelled_producer_stops(monkeypatch) -> None:
    producer_started = Event()
    producer_release = Event()

    def blocked_producer(self) -> None:
        producer_started.set()
        producer_release.wait()

    monkeypatch.setattr(data_export.ExportStream, "_produce", blocked_producer)
    stream = data_export.open_user_export(uuid4())
    consumer_finished = Event()

    def consume() -> None:
        try:
            next(stream)
        except StopIteration:
            pass
        finally:
            consumer_finished.set()

    consumer = Thread(target=consume)
    consumer.start()
    assert producer_started.wait(1)

    close_called = Event()
    original_close = stream.close

    def close() -> None:
        close_called.set()
        original_close()

    closer = Thread(target=close)
    monkeypatch.setattr(stream, "close", close)
    closer.start()
    assert close_called.wait(1)

    with pytest.raises(data_export.ExportBusy):
        data_export.open_user_export(uuid4())

    producer_release.set()
    closer.join(1)
    consumer.join(1)
    assert not closer.is_alive()
    assert consumer_finished.is_set()

    replacement = data_export.open_user_export(uuid4())
    replacement.close()


def test_data_export_propagates_producer_exception_and_releases_slot(monkeypatch) -> None:
    def failing_producer(self) -> None:
        raise RuntimeError("synthetic export failure")

    monkeypatch.setattr(data_export.ExportStream, "_produce", failing_producer)
    stream = data_export.open_user_export(uuid4())
    try:
        with pytest.raises(RuntimeError, match="synthetic export failure"):
            next(stream)
    finally:
        stream.close()
    replacement = data_export.open_user_export(uuid4())
    replacement.close()



def test_streaming_zip_writer_splits_every_queue_payload() -> None:
    chunks: Queue[bytes | data_export.ExportFailure | None] = Queue(maxsize=16)
    writer = data_export.StreamingZipWriter(chunks, Event())
    payload = b"x" * (data_export._EXPORT_CHUNK_BYTES * 2 + 1)

    assert writer.write(payload) == len(payload)

    emitted: list[bytes] = []
    while True:
        try:
            emitted.append(chunks.get_nowait())
        except Empty:
            break
    assert b"".join(emitted) == payload
    assert max(map(len, emitted)) <= data_export._EXPORT_CHUNK_BYTES


def test_json_array_writer_preserves_large_and_empty_arrays() -> None:
    records = (
        data_export.ExportTarget(
            valid_from=date(2026, 1, index + 1),
            valid_to=None,
            calories_kcal=Decimal("2000"),
            maintenance_kcal=None,
            activity_mode="off",
            activity_source_type=None,
            protein_g=Decimal("100"),
            carbs_g=None,
            fat_g=None,
            fiber_g=None,
            water_ml=None,
            created_at=datetime(2026, 1, index + 1, tzinfo=UTC),
        )
        for index in range(28)
    )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        data_export._write_json_array(archive, "targets.json", records)
        data_export._write_json_array(archive, "empty.json", iter(()))

    with ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        targets = json.loads(archive.read("targets.json"))
        empty = json.loads(archive.read("empty.json"))
    assert len(targets) == 28
    assert targets[0]["valid_from"] == "2026-01-01"
    assert targets[-1]["valid_from"] == "2026-01-28"
    assert empty == []


def test_data_export_is_user_scoped_streams_samples_and_excludes_secrets(
    client: TestClient,
    user: User,
    db,
    monkeypatch,
) -> None:
    _login(client)
    user.is_admin = True
    user.password_hash = "PASSWORD_HASH_SECRET_MUST_NOT_EXPORT"
    other = User(
        username="other-user",
        password_hash=hash_password("correct-horse-battery-staple"),
        timezone="America/New_York",
    )
    db.add(other)
    db.flush()
    batch = ImportBatch(user_id=user.id, source_type="apple_health_xml", status="completed")
    other_batch = ImportBatch(user_id=other.id, source_type="apple_health_xml", status="completed")
    db.add_all([batch, other_batch])
    db.flush()
    for index in range(501):
        _add_sample(db, user, batch, external_id=f"mine-{index}", value=Decimal("1200"))
    _add_sample(db, other, other_batch, external_id="other-sample", value=Decimal("9999.123"))
    db.add_all(
        [
            NutritionTarget(
                user_id=user.id,
                valid_from=date(2026, 8, 1),
                calories_kcal=Decimal("2200"),
                protein_g=Decimal("130"),
            ),
            TrackingOverride(
                user_id=user.id,
                local_date=date(2026, 8, 2),
                status="complete",
                note="Manuell bestätigt",
            ),
            UserAchievement(user_id=user.id, achievement_key="first_import"),
            YazioConnection(
                user_id=user.id,
                encrypted_email=b"YAZIO_EMAIL_SECRET_MUST_NOT_EXPORT",
                encrypted_password=b"YAZIO_PASSWORD_SECRET_MUST_NOT_EXPORT",
                source_identifier="yazio-account-a",
            ),
            ApiToken(
                user_id=user.id,
                label="private import",
                token_prefix="cg_private",
                token_hash="API_TOKEN_HASH_SECRET_MUST_NOT_EXPORT",
            ),
            UserTotpCredential(
                user_id=user.id,
                encrypted_secret=b"TOTP_SECRET_MUST_NOT_EXPORT",
            ),
            AccountRecoveryToken(
                user_id=user.id,
                token_hash="RECOVERY_TOKEN_SECRET_MUST_NOT_EXPORT",
                expires_at=datetime(2026, 8, 2, tzinfo=UTC),
            ),
            MfaRecoveryCode(
                user_id=user.id,
                code_hash="RECOVERY_CODE_SECRET_MUST_NOT_EXPORT",
            ),
            UserSession(
                user_id=user.id,
                token_hash="SESSION_TOKEN_SECRET_MUST_NOT_EXPORT",
                csrf_hash="CSRF_TOKEN_SECRET_MUST_NOT_EXPORT",
                expires_at=datetime(2026, 8, 2, tzinfo=UTC),
            ),
            PasskeyCredential(
                user_id=user.id,
                label="private passkey",
                credential_id=b"PASSKEY_CREDENTIAL_SECRET_MUST_NOT_EXPORT",
                public_key=b"PASSKEY_PUBLIC_KEY_SECRET_MUST_NOT_EXPORT",
                device_type="platform",
            ),
        ]
    )
    db.commit()
    events: list[tuple[str, str | None, str | None, dict[str, object], str | None, str | None]] = []

    def capture_event(
        event: str,
        *,
        actor_ref=None,
        target_ref=None,
        details=None,
        reason=None,
        request_id=None,
        client_ref=None,
    ) -> None:
        del reason
        events.append((event, actor_ref, target_ref, details or {}, request_id, client_ref))

    monkeypatch.setattr(data_export, "log_security_event", capture_event)
    download_id = uuid4()
    response = client.get(f"/api/v1/settings/export?download_id={download_id}")
    assert f"calograph_export_status_{download_id.hex}=accepted" in response.headers["set-cookie"]

    with _archive(response) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "profile.json",
            "settings.json",
            "targets.json",
            "tracking_overrides.json",
            "health_samples.jsonl",
            "import_batches.jsonl",
            "yazio.json",
            "achievements.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        profile = json.loads(archive.read("profile.json"))
        health_lines = archive.read("health_samples.jsonl").decode().splitlines()
        settings_data = json.loads(archive.read("settings.json"))
        targets_data = json.loads(archive.read("targets.json"))
        overrides_data = json.loads(archive.read("tracking_overrides.json"))
        imports_data = [
            json.loads(line)
            for line in archive.read("import_batches.jsonl").decode().splitlines()
        ]
        yazio_data = json.loads(archive.read("yazio.json"))
        achievements_data = json.loads(archive.read("achievements.json"))
        contents = "\n".join(
            archive.read(filename).decode()
            for filename in archive.namelist()
            if filename != "health_samples.jsonl"
        ) + "\n" + "\n".join(health_lines)

    assert manifest["format"] == "calograph-data-export"
    assert manifest["format_version"] == 1
    assert manifest["application"] == "CaloGraph"
    assert profile["username"] == "admin"
    assert settings_data["tracking_quality"] is not None
    assert any(item["calories_kcal"] == "2200.000" for item in targets_data)
    assert overrides_data[0]["local_date"] == "2026-08-02"
    assert overrides_data[0]["note"] == "Manuell bestätigt"
    assert imports_data[0]["source_type"] == "apple_health_xml"
    assert yazio_data["configured"] is True
    assert yazio_data["source_identifier"] == "yazio-account-a"
    assert achievements_data[0]["achievement_key"] == "first_import"
    assert len(health_lines) == 501
    assert {json.loads(line)["external_sample_id"] for line in health_lines} == {
        f"mine-{index}" for index in range(501)
    }
    assert "other-user" not in contents
    assert "other-sample" not in contents
    assert "9999.123000" not in contents
    for secret in (
        "PASSWORD_HASH_SECRET_MUST_NOT_EXPORT",
        "YAZIO_EMAIL_SECRET_MUST_NOT_EXPORT",
        "YAZIO_PASSWORD_SECRET_MUST_NOT_EXPORT",
        "API_TOKEN_HASH_SECRET_MUST_NOT_EXPORT",
        "TOTP_SECRET_MUST_NOT_EXPORT",
        "RECOVERY_TOKEN_SECRET_MUST_NOT_EXPORT",
        "RECOVERY_CODE_SECRET_MUST_NOT_EXPORT",
        "SESSION_TOKEN_SECRET_MUST_NOT_EXPORT",
        "CSRF_TOKEN_SECRET_MUST_NOT_EXPORT",
        "PASSKEY_CREDENTIAL_SECRET_MUST_NOT_EXPORT",
        "PASSKEY_PUBLIC_KEY_SECRET_MUST_NOT_EXPORT",
    ):
        assert secret not in contents
    assert len(events) == 1
    event, actor_ref, target_ref, details, request_id, client_ref = events[0]
    assert event == "data.exported"
    assert actor_ref is not None
    assert target_ref is None
    assert details == {}
    assert request_id is not None and len(request_id) == 32 and set(request_id) <= set("0123456789abcdef")
    assert client_ref is not None and len(client_ref) == 16 and set(client_ref) <= set("0123456789abcdef")

    assert db.query(UserAchievement).filter_by(
        user_id=user.id, achievement_key="ordered_takeout"
    ).count() == 1


def test_data_export_handles_an_empty_data_set(client: TestClient, db) -> None:
    empty = User(
        username="empty-user",
        password_hash=hash_password("correct-horse-battery-staple"),
    )
    db.add(empty)
    db.commit()
    _login(client, "empty-user")

    with _archive(client.get("/api/v1/settings/export")) as archive:
        assert archive.read("health_samples.jsonl") == b""
        assert archive.read("import_batches.jsonl") == b""
        assert json.loads(archive.read("targets.json")) == []
        assert json.loads(archive.read("tracking_overrides.json")) == []
        assert json.loads(archive.read("achievements.json")) == []
        assert json.loads(archive.read("yazio.json"))["configured"] is False
        assert json.loads(archive.read("settings.json")) == {"tracking_quality": None}
