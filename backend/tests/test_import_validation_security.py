import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import create_api_token
from app.models import ApiToken, HealthSample, ImportBatch, ImportError, RawImportPayload, User
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionProvenance,
    NutritionServingObservation,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)


def _import_headers(db: Session, user: User) -> dict[str, str]:
    _, raw = create_api_token(db, user, "security-test")
    return {
        "Authorization": f"Bearer {raw}",
        "Content-Type": "application/json",
    }


@pytest.mark.usefixtures("block_yazio_runtime")
@pytest.mark.parametrize("explicit_client_identifier", [None, "synthetic-file-client"])
def test_compatible_yazio_token_import_is_offline_and_idempotent(
    client: TestClient,
    user: User,
    db: Session,
    compatible_yazio_file,
    yazio_file_source_identifier: str,
    explicit_client_identifier: str | None,
) -> None:
    headers = _import_headers(db, user)
    if explicit_client_identifier is not None:
        headers["X-Client-Identifier"] = explicit_client_identifier
    expected_identifier = explicit_client_identifier or yazio_file_source_identifier
    expected_values = compatible_yazio_file["values"]

    first = client.post(
        "/api/v1/import/yazio", headers=headers, content=compatible_yazio_file["raw"]
    )
    assert first.status_code == 200
    assert first.json()["inserted"] == len(expected_values)
    assert first.json()["updated"] == first.json()["failed"] == 0
    samples = list(db.scalars(select(HealthSample).where(HealthSample.user_id == user.id)))
    assert {sample.metric_type: sample.value for sample in samples} == expected_values
    assert {sample.metric_type: sample.unit for sample in samples} == compatible_yazio_file["units"]
    assert {sample.source_identifier for sample in samples} == {expected_identifier}
    sample_ids = {sample.id for sample in samples}
    batches = list(db.scalars(select(ImportBatch).where(ImportBatch.user_id == user.id)))
    assert len(batches) == 1
    assert batches[0].source_type == "yazio_export_v1"
    assert batches[0].connector_variant is None
    assert batches[0].client_identifier == (explicit_client_identifier or "security-test")

    repeated = client.post(
        "/api/v1/import/yazio", headers=headers, content=compatible_yazio_file["raw"]
    )
    assert repeated.status_code == 200
    assert repeated.json()["inserted"] == repeated.json()["updated"] == 0
    assert repeated.json()["skipped"] == len(expected_values)
    db.expire_all()
    samples = list(db.scalars(select(HealthSample).where(HealthSample.user_id == user.id)))
    assert {sample.id for sample in samples} == sample_ids
    assert {sample.metric_type: sample.value for sample in samples} == expected_values
    assert {sample.source_identifier for sample in samples} == {expected_identifier}
    assert all(batch.connector_variant is None for batch in db.scalars(select(ImportBatch)))


@pytest.mark.usefixtures("block_yazio_runtime")
def test_compatible_yazio_validation_is_offline_and_creates_no_data(
    client: TestClient,
    user: User,
    db: Session,
    compatible_yazio_file,
    yazio_file_source_identifier: str,
) -> None:
    models = (
        HealthSample,
        ImportBatch,
        ImportError,
        RawImportPayload,
        NutritionIngestionRun,
        NutritionSourceObservation,
        NutritionExternalIdentity,
        NutritionConsumptionEvent,
        NutritionFoodProfile,
        NutritionFoodSnapshot,
        NutritionExternalIdentityLink,
        NutritionServingObservation,
        NutritionFieldObservation,
        NutritionSourceTombstone,
        NutritionProvenance,
        NutritionDailyProjection,
        NutritionDailyProjectionFact,
        NutritionDailyProjectionLineage,
        NutritionProjectionHead,
    )
    headers = _import_headers(db, user)
    before = {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in models
    }
    assert set(before.values()) == {0}

    for _ in range(2):
        response = client.post(
            "/api/v1/import/yazio/validate",
            headers=headers,
            content=compatible_yazio_file["raw"],
        )
        assert response.status_code == 200
        assert response.json()["status"] == "valid"
        assert response.json()["received"] == len(compatible_yazio_file["values"])
        assert response.json()["inserted"] == response.json()["updated"] == 0
        assert response.json()["failed"] == response.json()["skipped"] == 0
        db.expire_all()
        assert {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in models
        } == before


def test_import_token_activity_timestamp_is_throttled(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    token, raw = create_api_token(db, user, "activity-test")
    headers = {
        "Authorization": f"Bearer {raw}",
        "Content-Type": "application/json",
    }

    first = client.post(
        "/api/v1/import/apple-health/validate",
        headers=headers,
        json={"samples": []},
    )
    assert first.status_code == 200
    db.expire_all()
    stored = db.get(ApiToken, token.id)
    assert stored is not None
    first_last_used_at = stored.last_used_at
    assert first_last_used_at is not None

    second = client.post(
        "/api/v1/import/apple-health/validate",
        headers=headers,
        json={"samples": []},
    )
    assert second.status_code == 200
    db.expire_all()
    stored = db.get(ApiToken, token.id)
    assert stored is not None
    assert stored.last_used_at == first_last_used_at

    stale_last_used_at = datetime.now(UTC) - timedelta(minutes=6)
    stored.last_used_at = stale_last_used_at
    db.commit()
    third = client.post(
        "/api/v1/import/apple-health/validate",
        headers=headers,
        json={"samples": []},
    )
    assert third.status_code == 200
    db.expire_all()
    stored = db.get(ApiToken, token.id)
    assert stored is not None
    assert stored.last_used_at is not None
    refreshed_last_used_at = stored.last_used_at
    if refreshed_last_used_at.tzinfo is None:
        refreshed_last_used_at = refreshed_last_used_at.replace(tzinfo=UTC)
    assert refreshed_last_used_at > stale_last_used_at


@pytest.mark.parametrize(
    "payload",
    [
        {"metrics": ["do-not-reflect-this-scalar"]},
        {
            "metrics": [
                {
                    "name": "dietary_energy",
                    "data": ["do-not-reflect-this-point"],
                }
            ]
        },
        {"samples": [None]},
        {
            "samples": [
                {
                    "type": "dietary_energy",
                    "value": 1,
                    "start_at": "2026-07-20T12:00:00+00:00",
                    "source_identifier": "s" * 256,
                }
            ]
        },
    ],
)
def test_structurally_invalid_imports_return_422_without_writes_or_reflection(
    client: TestClient,
    user: User,
    db: Session,
    payload: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = client.post(
        "/api/v1/import/apple-health",
        headers=_import_headers(db, user),
        content=json.dumps(payload),
    )

    assert response.status_code == 422
    assert "do-not-reflect" not in response.text
    assert "do-not-reflect" not in caplog.text
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0


def test_invalid_client_identifier_is_rejected_after_authentication(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    headers = _import_headers(db, user)
    headers["X-Client-Identifier"] = "client-secret-" + ("x" * 190)

    response = client.post(
        "/api/v1/import/apple-health",
        headers=headers,
        json={"samples": []},
    )

    assert response.status_code == 422
    assert "client-secret" not in response.text
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def test_unauthenticated_malformed_import_remains_unauthorized(
    client: TestClient,
    db: Session,
) -> None:
    response = client.post(
        "/api/v1/import/apple-health",
        headers={"X-Client-Identifier": "x" * 191},
        content=b'{"samples":[null]}',
    )

    assert response.status_code == 401
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def test_every_json_import_entry_point_uses_safe_422_responses(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    token_headers = _import_headers(db, user)
    api_responses = [
        client.post(
            "/api/v1/import/apple-health/validate",
            headers=token_headers,
            json={"samples": ["private-apple-value"]},
        ),
        client.post(
            "/api/v1/import/yazio",
            headers=token_headers,
            json={"days": ["private-yazio-value"]},
        ),
        client.post(
            "/api/v1/import/yazio/validate",
            headers=token_headers,
            json={"days": ["private-yazio-value"]},
        ),
    ]

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    file_response = client.post(
        "/api/v1/import/yazio/file",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        files={
            "file": (
                "days.json",
                b'{"days":["private-file-value"]}',
                "application/json",
            )
        },
    )

    responses = [*api_responses, file_response]
    assert all(response.status_code == 422 for response in responses)
    assert all("private-" not in response.text for response in responses)
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


@pytest.mark.parametrize(
    "raw",
    [
        b'{"samples":[\xff]}',
        b'{"samples":' + (b"[" * 1100) + b"0" + (b"]" * 1100) + b"}",
    ],
)
def test_invalid_encoding_and_deep_json_return_422(
    client: TestClient,
    user: User,
    db: Session,
    raw: bytes,
) -> None:
    response = client.post(
        "/api/v1/import/apple-health",
        headers=_import_headers(db, user),
        content=raw,
    )

    assert response.status_code == 422
    assert response.json()["detail"] in {
        "Ungültiges JSON",
        "JSON-Struktur enthält ungültige oder zu lange Felder",
    }
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def test_semantic_sample_errors_remain_safe_partial_imports(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    hidden_timezone = "Mars/private-zone"
    response = client.post(
        "/api/v1/import/apple-health",
        headers=_import_headers(db, user),
        json={
            "samples": [
                {
                    "type": "dietary_energy",
                    "value": 1,
                    "start_at": "2026-07-20T12:00:00+00:00",
                    "timezone": hidden_timezone,
                },
                {
                    "type": "dietary_energy",
                    "value": 1e100,
                    "start_at": "2026-07-20T12:00:00+00:00",
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed_with_errors"
    assert response.json()["failed"] == 2
    assert response.json()["inserted"] == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0
    safe_details = list(db.scalars(select(ImportError.safe_detail)))
    assert safe_details
    assert all(hidden_timezone not in detail for detail in safe_details)
