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

    response = client.get("/health/live")

    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
