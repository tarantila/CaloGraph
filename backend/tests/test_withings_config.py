from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.withings.constants import WITHINGS_SCOPES


def test_withings_configuration_has_no_global_oauth_credentials() -> None:
    configured = Settings(_env_file=None, environment="test")

    assert configured.withings_enabled is False
    assert configured.withings_redirect_uri is None
    assert not hasattr(configured, "withings_client_id")
    assert not hasattr(configured, "withings_client_secret")


@pytest.mark.parametrize(
    "redirect_uri",
    (
        "http://localhost:8180/api/v1/withings/oauth/callback",
        "http://127.0.0.1:8280/api/v1/withings/oauth/callback",
        "http://[::1]:8180/api/v1/withings/oauth/callback",
        "https://calograph.example.com/api/v1/withings/oauth/callback",
    ),
)
def test_withings_redirect_uri_accepts_loopback_http_and_public_https(redirect_uri: str) -> None:
    configured = Settings(
        _env_file=None,
        environment="test",
        withings_enabled=True,
        withings_redirect_uri=redirect_uri,
    )

    assert configured.withings_redirect_uri == redirect_uri


def test_withings_redirect_uri_preserves_trailing_callback_slash() -> None:
    redirect_uri = "https://example.test/api/v1/withings/oauth/callback/"
    configured = Settings(
        _env_file=None,
        environment="test",
        withings_enabled=True,
        withings_redirect_uri=redirect_uri,
    )

    assert configured.withings_redirect_uri == redirect_uri


@pytest.mark.parametrize(
    "redirect_uri",
    (
        "http://calograph.example.com/api/v1/withings/oauth/callback",
        "ftp://calograph.example.com/api/v1/withings/oauth/callback",
        "javascript:alert(1)",
        "data:text/plain,callback",
        "/api/v1/withings/oauth/callback",
        "https://client:secret@example.test/withings/callback",
        "https://example.test",
        "https://example.test/callback?next=elsewhere",
        "https://example.test/callback#fragment",
    ),
)
def test_withings_redirect_uri_rejects_unsafe_or_incomplete_values(redirect_uri: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="test",
            withings_enabled=True,
            withings_redirect_uri=redirect_uri,
        )


def test_withings_scope_is_minimal_and_exact() -> None:
    assert WITHINGS_SCOPES == ("user.metrics", "user.activity")
