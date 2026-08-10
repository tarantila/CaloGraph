import json
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import security_events
from app.config import settings
from app.main import busy_user_operation, inactive_user_operation
from app.models import User
from app.security_events import (
    EVENT_SPECS,
    log_security_event,
    security_reference,
    security_request_context,
)
from app.services.user_operation_lock import InactiveUserOperation, UserOperationBusy


def _capture_security_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    monkeypatch.setattr(
        security_events.logger,
        "log",
        lambda level, message: records.append((level, message)),
    )
    return records


def test_security_event_is_bounded_json_without_raw_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_security_events(monkeypatch)
    actor_ref = security_reference("user", "admin@example.com")
    client_ref = security_reference("client", "192.0.2.10")

    with security_request_context("a" * 32, client_ref):
        log_security_event(
            "auth.login.failed",
            actor_ref=actor_ref,
            reason="invalid_credentials",
        )

    assert len(records) == 1
    level, raw = records[0]
    payload = json.loads(raw)
    assert level == logging.WARNING
    assert payload["event"] == "auth.login.failed"
    assert payload["outcome"] == "failure"
    assert payload["request_id"] == "a" * 32
    assert payload["actor_ref"] == actor_ref
    assert payload["client_ref"] == client_ref
    assert "admin@example.com" not in raw
    assert "192.0.2.10" not in raw


def test_security_event_rejects_unknown_fields_and_raw_references() -> None:
    with pytest.raises(ValueError, match="unsupported detail fields"):
        log_security_event(
            "auth.login.failed",
            details={"password": "must-not-be-logged"},
        )
    with pytest.raises(ValueError, match="pseudonymous security reference"):
        log_security_event("auth.login.failed", actor_ref="admin@example.com")


def test_security_event_catalog_covers_required_sensitive_actions() -> None:
    required = {
        "admin.user.created",
        "admin.user.deactivated",
        "admin.user.deleted",
        "admin.user.lifecycle_failed",
        "admin.user.lifecycle_rejected",
        "admin.user.reactivated",
        "auth.api_token.created",
        "auth.api_token.revoked",
        "auth.invitation.created",
        "auth.invitation.revoked",
        "auth.login.failed",
        "auth.login.succeeded",
        "auth.password.changed",
        "import.partial_failed",
        "import.rejected",
        "integration.yazio.connection_configured",
        "integration.yazio.sync_failed",
        "security.rate_limit.triggered",
    }
    assert required <= EVENT_SPECS.keys()


def test_login_security_events_do_not_contain_credentials(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del user
    records = _capture_security_events(monkeypatch)

    failed = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    succeeded = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )

    assert failed.status_code == 401
    assert succeeded.status_code == 200
    serialized = "\n".join(message for _, message in records)
    payloads = [json.loads(message) for _, message in records]
    assert {payload["event"] for payload in payloads} >= {
        "auth.login.failed",
        "auth.login.succeeded",
    }
    assert "admin" not in serialized
    assert "wrong-password" not in serialized
    assert "correct-horse-battery-staple" not in serialized


def test_rate_limit_rejection_emits_one_pseudonymous_event(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del user
    records = _capture_security_events(monkeypatch)
    monkeypatch.setattr(settings, "login_ip_rate_limit", 1)

    first = client.post(
        "/api/v1/auth/login",
        json={"username": "first-unknown", "password": "wrong-password"},
    )
    limited = client.post(
        "/api/v1/auth/login",
        json={"username": "second-unknown", "password": "wrong-password"},
    )

    assert first.status_code == 401
    assert limited.status_code == 429
    payloads = [json.loads(message) for _, message in records]
    rate_events = [
        payload
        for payload in payloads
        if payload["event"] == "security.rate_limit.triggered"
    ]
    assert len(rate_events) == 1
    assert rate_events[0]["action"] == "login-ip"
    assert rate_events[0]["retry_after"] > 0
    assert "first-unknown" not in records[-1][1]
    assert "second-unknown" not in records[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "error"),
    (
        (inactive_user_operation, InactiveUserOperation()),
        (busy_user_operation, UserOperationBusy()),
    ),
)
async def test_import_user_lock_rejection_emits_security_event(
    handler,
    error,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_security_events(monkeypatch)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/import/apple-health",
            "raw_path": b"/api/v1/import/apple-health",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }
    )

    response = await handler(request, error)

    assert response.status_code == 409
    payloads = [json.loads(message) for _, message in records]
    assert [payload["event"] for payload in payloads] == ["import.rejected"]
    assert payloads[0]["reason"] == "http_409"
    assert payloads[0]["status_code"] == 409
