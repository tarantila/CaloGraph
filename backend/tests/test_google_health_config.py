from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_google_health_settings_are_disabled_and_secret_safe() -> None:
    client_id = "google-client-id.apps.googleusercontent.com"
    client_secret = "synthetic-google-client-secret"
    configured = Settings(
        _env_file=None,
        environment="development",
        google_health_client_id=client_id,
        google_health_client_secret=client_secret,
    )

    assert Settings(_env_file=None, environment="development").google_health_enabled is False
    assert configured.google_health_client_id == client_id
    assert configured.google_health_client_secret == client_secret
    rendered = repr(configured)
    serialized = configured.model_dump()
    assert client_secret not in rendered
    assert client_secret not in str(serialized)
    assert "google_health_client_secret" not in serialized
    assert "google_health_client_secret_file" not in serialized


def test_google_health_secret_file_is_loaded_and_excluded(
    tmp_path: Path,
) -> None:
    secret = "google-secret-from-file"
    secret_file = tmp_path / "google-client-secret"
    secret_file.write_text(secret + "\n", encoding="utf-8")

    configured = Settings(
        _env_file=None,
        environment="development",
        google_health_client_secret_file=str(secret_file),
    )

    assert configured.google_health_client_secret == secret
    assert secret not in repr(configured)
    assert secret not in str(configured.model_dump())
    assert str(secret_file) not in repr(configured)
    assert str(secret_file) not in str(configured.model_dump())


def test_google_health_direct_and_file_secret_sources_conflict_without_leaks(
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "google-client-secret"
    secret_file.write_text("file-google-secret", encoding="utf-8")
    direct = "direct-google-secret"

    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            environment="development",
            google_health_client_secret=direct,
            google_health_client_secret_file=str(secret_file),
        )

    message = str(captured.value)
    assert direct not in message
    assert str(secret_file) not in message
    assert "file-google-secret" not in message


def test_google_health_runtime_templates_wire_non_secret_google_values() -> None:
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
        assert "GOOGLE_HEALTH_ENABLED=" in template
        assert "GOOGLE_HEALTH_CLIENT_ID=" in template
        assert "GOOGLE_HEALTH_CLIENT_SECRET_FILE=" in template
        assert "GOOGLE_HEALTH_CLIENT_SECRET=" not in template

    assert "GOOGLE_HEALTH_ENABLED: ${GOOGLE_HEALTH_ENABLED:-false}" in compose
    assert "GOOGLE_HEALTH_CLIENT_ID: ${GOOGLE_HEALTH_CLIENT_ID-}" in compose
    assert "GOOGLE_HEALTH_CLIENT_SECRET_FILE: /run/secrets/google_health_client_secret" in compose
    assert "google_health_client_secret:" in compose
    assert "file: ${GOOGLE_HEALTH_CLIENT_SECRET_FILE:-/dev/null}" in compose
