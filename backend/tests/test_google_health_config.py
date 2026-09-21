from pathlib import Path

import pytest

from app.config import Settings
from app.google_health.constants import (
    GOOGLE_HEALTH_API_BASE_URL,
    GOOGLE_HEALTH_AUTH_URI,
    GOOGLE_HEALTH_CALLBACK_PATH,
    GOOGLE_HEALTH_REQUIRED_SCOPES,
    GOOGLE_HEALTH_SCOPES,
    GOOGLE_HEALTH_TOKEN_URI,
)
from app.google_health.oauth import google_health_redirect_uri


def test_google_health_constants_are_fixed_official_endpoints() -> None:
    assert GOOGLE_HEALTH_API_BASE_URL == "https://health.googleapis.com/v4"
    assert GOOGLE_HEALTH_AUTH_URI == "https://accounts.google.com/o/oauth2/v2/auth"
    assert GOOGLE_HEALTH_TOKEN_URI == "https://oauth2.googleapis.com/token"
    assert GOOGLE_HEALTH_SCOPES == (
        "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    )
    assert frozenset(GOOGLE_HEALTH_SCOPES) == GOOGLE_HEALTH_REQUIRED_SCOPES
    assert GOOGLE_HEALTH_CALLBACK_PATH == "/api/v1/google-health/oauth/callback"


def test_google_health_redirect_uri_uses_only_public_origin() -> None:
    assert (
        google_health_redirect_uri("https://nutrition.example.test/")
        == "https://nutrition.example.test/api/v1/google-health/oauth/callback"
    )
    assert (
        google_health_redirect_uri("https://nutrition.example.test")
        == "https://nutrition.example.test/api/v1/google-health/oauth/callback"
    )


@pytest.mark.parametrize(
    "public_url",
    [
        "https://nutrition.example.test/app",
        "https://nutrition.example.test/app/",
        "https://nutrition.example.test?tenant=one",
        "https://nutrition.example.test#fragment",
        "https://user:password@nutrition.example.test",
        "ftp://nutrition.example.test",
    ],
)
def test_google_health_redirect_uri_rejects_non_origin(public_url: str) -> None:
    with pytest.raises(ValueError):
        google_health_redirect_uri(public_url)


def test_google_health_settings_keep_only_enablement_and_public_url() -> None:
    configured = Settings(
        _env_file=None,
        environment="development",
        google_health_enabled=True,
        calograph_public_url="https://nutrition.example.test",
        google_health_client_id="ignored-client-id",
        google_health_client_secret="ignored-client-secret",
        google_health_client_secret_file="/ignored/path",
    )

    assert configured.google_health_enabled is True
    assert configured.calograph_public_url == "https://nutrition.example.test"
    assert "google_health_client_id" not in Settings.model_fields
    assert "google_health_client_secret" not in Settings.model_fields
    assert "google_health_client_secret_file" not in Settings.model_fields
    serialized = configured.model_dump()
    assert "google_health_client_id" not in serialized
    assert "google_health_client_secret" not in serialized
    assert "google_health_client_secret_file" not in serialized
    assert "ignored-client-secret" not in repr(configured)
    assert "ignored-client-secret" not in str(serialized)


def test_google_health_global_secret_file_is_not_loaded(tmp_path: Path) -> None:
    secret_file = tmp_path / "google-client-secret"
    secret_file.write_text("synthetic-google-secret", encoding="utf-8")

    configured = Settings(
        _env_file=None,
        environment="development",
        google_health_client_secret_file=str(secret_file),
    )

    assert not hasattr(configured, "google_health_client_secret")
    assert str(secret_file) not in str(configured.model_dump())



def test_google_health_runtime_templates_keep_enablement_without_global_credentials() -> None:
    project_root = next(
        (
            candidate
            for candidate in (Path.cwd(), Path.cwd().parent, Path("/workspace"))
            if (candidate / "docker-compose.yml").is_file()
        ),
        None,
    )
    assert project_root is not None
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    development = (project_root / ".env.example").read_text(encoding="utf-8")
    production = (project_root / ".env.production.example").read_text(encoding="utf-8")

    for template in (development, production):
        assert "GOOGLE_HEALTH_ENABLED=false" in template
        assert "GOOGLE_HEALTH_CLIENT_ID" not in template
        assert "GOOGLE_HEALTH_CLIENT_SECRET" not in template

    assert "GOOGLE_HEALTH_ENABLED: ${GOOGLE_HEALTH_ENABLED:-false}" in compose
    assert "GOOGLE_HEALTH_CLIENT_ID" not in compose
    assert "GOOGLE_HEALTH_CLIENT_SECRET" not in compose
    assert "google_health_client_secret" not in compose
