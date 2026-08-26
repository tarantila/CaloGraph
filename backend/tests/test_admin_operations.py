from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.main import app
from app.models import SecurityAuditEvent, User
from app.services.app_logs import APP_LOG_BUFFER_LIMIT, clear_app_logs, get_app_logs, record_app_log
from app.services.geoip import clear_geoip_cache, lookup_client_ip
from app.services.rate_limit import normalize_audit_client_ip


def _login(client, username: str, password: str = "correct-horse-battery-staple") -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]

def _proxy_test_client(peer: str) -> TestClient:
    return TestClient(
        ProxyHeadersMiddleware(app, trusted_hosts="172.30.0.10/32"),
        client=(peer, 50_000),
    )


def test_admin_app_logs_are_protected_and_filterable(client, user, db) -> None:
    user.is_admin = True
    db.commit()
    clear_app_logs()
    record_app_log(level="INFO", action="GET /api/v1/health", duration_ms=4, request_id="a" * 32, status=200)
    record_app_log(level="ERROR", action="POST /api/v1/import", duration_ms=8, request_id="b" * 32, status=500)

    assert client.get("/api/v1/admin/logs").status_code == 401
    _login(client, user.username)
    response = client.get("/api/v1/admin/logs", params={"level": "ERROR", "action": "import"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["request_id"] == "b" * 32
    assert set(payload["items"][0]) == {"occurred_at", "level", "action", "duration_ms", "request_id", "status"}
    assert payload["buffer_limit"] == APP_LOG_BUFFER_LIMIT
    assert payload["persistence"] == "process"


def test_non_admin_cannot_read_app_logs(client, db, user) -> None:
    normal_user = User(username="normal", password_hash=user.password_hash)
    db.add(normal_user)
    db.commit()
    _login(client, "normal")
    assert client.get("/api/v1/admin/logs").status_code == 403


def test_app_log_buffer_is_bounded_and_not_persistent() -> None:
    clear_app_logs()
    for index in range(APP_LOG_BUFFER_LIMIT + 1):
        record_app_log(
            level="INFO",
            action=f"GET /{index}",
            duration_ms=index,
            request_id=f"{index:032x}",
            status=200,
        )

    entries = get_app_logs(limit=APP_LOG_BUFFER_LIMIT)
    assert len(entries) == APP_LOG_BUFFER_LIMIT
    assert entries[0]["action"] == f"GET /{APP_LOG_BUFFER_LIMIT}"
    assert all("password" not in entry and "token" not in entry for entry in entries)
    clear_app_logs()
    assert get_app_logs() == []


def test_audit_ip_normalization_preserves_ipv4_ipv6_and_private_addresses() -> None:
    assert normalize_audit_client_ip("198.51.100.7") == "198.51.100.7"
    assert normalize_audit_client_ip("2001:db8::7") == "2001:db8::7"
    assert normalize_audit_client_ip("127.0.0.1") == "127.0.0.1"
    assert normalize_audit_client_ip("::1") == "::1"
    assert normalize_audit_client_ip("10.0.0.7") == "10.0.0.7"
    assert normalize_audit_client_ip("fd00::7") == "fd00::7"
    assert normalize_audit_client_ip("not-an-ip") is None


def test_direct_x_forwarded_for_is_not_used_as_audit_ip(client, user, db) -> None:
    _login(client, user.username)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "unknown", "password": "wrong-password"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert response.status_code == 401
    event = db.query(SecurityAuditEvent).filter_by(event="auth.login.failed").order_by(SecurityAuditEvent.occurred_at.desc()).first()
    assert event is not None
    assert event.client_ip is None

def test_untrusted_forwarded_for_preserves_the_actual_peer(user, db) -> None:
    proxy_client = _proxy_test_client("172.30.0.3")
    response = proxy_client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "wrong-password"},
        headers={"X-Forwarded-For": "203.0.113.42"},
    )

    assert response.status_code == 401
    event = (
        db.query(SecurityAuditEvent)
        .filter_by(event="auth.login.failed")
        .order_by(SecurityAuditEvent.occurred_at.desc())
        .first()
    )
    assert event is not None
    assert event.client_ip == "172.30.0.3"
    assert event.client_ref is not None
    assert len(event.client_ref) == 16
    assert event.client_ref != event.client_ip


def test_trusted_proxy_resolves_ipv4_ipv6_and_forwarded_chains(user, db) -> None:
    proxy_client = _proxy_test_client("172.30.0.10")
    forwarded_cases = [
        ("203.0.113.42", "203.0.113.42"),
        ("[2001:db8::42]:443", "2001:db8::42"),
        ("203.0.113.42, 172.30.0.10", "203.0.113.42"),
        ("198.51.100.77, 203.0.113.42", "203.0.113.42"),
    ]

    for index, (forwarded_for, _) in enumerate(forwarded_cases):
        response = proxy_client.post(
            "/api/v1/auth/login",
            json={"username": f"{user.username}-proxy-{index}", "password": "wrong-password"},
            headers={"X-Forwarded-For": forwarded_for},
        )
        assert response.status_code == 401

    events = (
        db.query(SecurityAuditEvent)
        .filter_by(event="auth.login.failed")
        .order_by(SecurityAuditEvent.occurred_at)
        .all()
    )
    assert [event.client_ip for event in events] == [
        expected_ip for _, expected_ip in forwarded_cases
    ]
    assert all(
        event.client_ref is not None
        and len(event.client_ref) == 16
        and event.client_ref != event.client_ip
        for event in events
    )


def test_geoip_disabled_and_private_addresses_skip_external_lookup(monkeypatch) -> None:
    import app.services.geoip as geoip
    from app.config import settings

    clear_geoip_cache()
    monkeypatch.setattr(settings, "security_audit_geoip_provider", "disabled")
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("external lookup must remain disabled")

    monkeypatch.setattr(geoip.requests, "get", fail_if_called)
    assert lookup_client_ip("8.8.8.8") is None
    assert lookup_client_ip("127.0.0.1") is not None
    assert lookup_client_ip("fd00::7") is not None
    assert lookup_client_ip("172.30.0.10") is not None
    assert called is False


def test_geoip_lookup_is_cached_and_provider_failure_is_non_fatal(monkeypatch) -> None:
    import app.services.geoip as geoip
    from app.config import settings

    clear_geoip_cache()
    monkeypatch.setattr(settings, "security_audit_geoip_provider", "ipwhois")
    monkeypatch.setattr(settings, "security_audit_geoip_cache_seconds", 3600)
    calls = 0

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "city": "Bochum",
                "country_code": "DE",
                "connection": {"org": "ExampleNet"},
            }

    def get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr(geoip.requests, "get", get)
    assert lookup_client_ip("8.8.8.8") == lookup_client_ip("8.8.8.8")
    assert calls == 1

    def fail(*args, **kwargs):
        raise geoip.requests.RequestException("timeout")

    clear_geoip_cache()
    monkeypatch.setattr(geoip.requests, "get", fail)
    assert lookup_client_ip("9.9.9.9") is None


def test_audit_ip_field_is_persisted_without_secret_material(client, user, db) -> None:
    user.is_admin = True
    db.commit()
    event = SecurityAuditEvent(
        occurred_at=datetime.now(UTC),
        event="auth.login.failed",
        outcome="failure",
        client_ip="2001:db8::42",
        client_ref="a" * 16,
    )
    db.add(event)
    db.commit()
    _login(client, user.username)
    response = client.get("/api/v1/admin/audit")
    assert response.status_code == 200
    item = next(item for item in response.json()["items"] if item["id"] == str(event.id))
    assert item["client_ip"] == "2001:db8::42"
    assert item["client_ref"] == "a" * 16
    assert "password_hash" not in response.text
