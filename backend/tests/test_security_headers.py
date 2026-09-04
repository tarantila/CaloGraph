import re

from fastapi.testclient import TestClient

from app.config import settings


def test_api_responses_include_security_and_privacy_headers(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["x-permitted-cross-domain-policies"] == "none"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert "strict-transport-security" not in response.headers


def test_hsts_is_only_sent_when_enabled(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_hsts", True)
    monkeypatch.setattr(settings, "hsts_include_subdomains", False, raising=False)

    response = client.get("/health/live")

    assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_hsts_subdomains_require_explicit_opt_in(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "enable_hsts", True)
    monkeypatch.setattr(settings, "hsts_include_subdomains", True, raising=False)

    response = client.get("/health/live")

    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_api_docs_are_disabled_unless_enabled(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "enable_api_docs", False, raising=False)

    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_api_docs_can_be_enabled(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_api_docs", True, raising=False)

    docs = client.get("/api/docs")
    schema = client.get("/api/openapi.json")

    assert docs.status_code == 200
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "CaloGraph API"
    assert schema.json()["info"]["version"] == "0.6.3"


def test_request_ids_are_bounded_and_invalid_client_values_are_replaced(
    client: TestClient,
) -> None:
    invalid = client.get("/health/live", headers={"X-Request-ID": "attacker-controlled"})
    valid_id = "a" * 32
    valid = client.get("/health/live", headers={"X-Request-ID": valid_id})

    assert invalid.headers["x-request-id"] != "attacker-controlled"
    assert re.fullmatch(r"[a-f0-9]{32}", invalid.headers["x-request-id"])
    assert valid.headers["x-request-id"] == valid_id
