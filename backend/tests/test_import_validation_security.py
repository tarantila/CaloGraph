import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import create_api_token
from app.models import ApiToken, HealthSample, ImportBatch, ImportError, User


def _import_headers(db: Session, user: User) -> dict[str, str]:
    _, raw = create_api_token(db, user, "security-test")
    return {
        "Authorization": f"Bearer {raw}",
        "Content-Type": "application/json",
    }


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
