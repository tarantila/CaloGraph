import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import ProductionConfigurationError, Settings


def valid_production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "database_url": (
            "postgresql+psycopg://calograph:"
            "strong-database-secret-7349@postgres:5432/calograph"
        ),
        "session_secret": "production-session-secret-0123456789abcdef",
        "rate_limit_secret": "production-rate-limit-secret-fedcba9876543210",
        "calograph_public_url": "https://nutrition.calograph.de",
        "cookie_secure": True,
        "trusted_hosts": "nutrition.calograph.de",
        "trusted_origins": "https://nutrition.calograph.de",
        "trusted_proxy_networks": "172.18.0.0/16",
        "enable_hsts": True,
        "credential_encryption_key": Fernet.generate_key().decode(),
        "yazio_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_public_url_is_canonical_and_extends_request_allowlists() -> None:
    configured = Settings(
        _env_file=None,
        calograph_public_url="https://nutrition.example.test/",
        trusted_hosts="localhost",
        trusted_origins="http://localhost:8180",
        trusted_proxy_networks="172.18.0.0/16,127.0.0.1",
    )

    assert configured.calograph_public_url == "https://nutrition.example.test"
    assert configured.trusted_host_list == ["localhost", "nutrition.example.test"]
    assert configured.trusted_origin_list == [
        "http://localhost:8180",
        "https://nutrition.example.test",
    ]
    assert configured.trusted_proxy_networks == "172.18.0.0/16,127.0.0.1"


@pytest.mark.parametrize(
    "value",
    [
        "nutrition.example.test",
        "ftp://nutrition.example.test",
        "https://user:password@nutrition.example.test",
        "https://nutrition.example.test/subpath",
    ],
)
def test_public_url_rejects_non_origin_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, calograph_public_url=value)


def test_proxy_networks_reject_wildcard_trust() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_proxy_networks="*")


def test_environment_is_required(monkeypatch) -> None:
    monkeypatch.delenv("ENVIRONMENT")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_validation_errors_do_not_echo_sensitive_inputs() -> None:
    invalid_key = "not-a-valid-fernet-key-with-sensitive-content"

    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            environment="development",
            credential_encryption_key=invalid_key,
        )

    assert invalid_key not in str(captured.value)


def test_valid_production_configuration_passes_runtime_check() -> None:
    valid_production_settings().validate_runtime_security()


def test_development_configuration_allows_local_http() -> None:
    configured = Settings(
        _env_file=None,
        environment="development",
        calograph_public_url="http://localhost:8180",
        cookie_secure=False,
        enable_hsts=False,
    )

    configured.validate_runtime_security()


def test_unsafe_production_configuration_reports_all_variable_names() -> None:
    secret = "CHANGE_ME_AT_LEAST_32_RANDOM_CHARACTERS"
    configured = valid_production_settings(
        database_url="postgresql+psycopg://calograph:calograph@postgres:5432/calograph",
        session_secret=secret,
        rate_limit_secret=secret,
        calograph_public_url="http://localhost:8180",
        cookie_secure=False,
        trusted_hosts="wrong.example.org",
        trusted_origins="https://wrong.example.org",
        trusted_proxy_networks="127.0.0.1/32",
        enable_hsts=False,
        credential_encryption_key="",
        max_upload_bytes=50 * 1024 * 1024,
        max_json_payload_bytes=60 * 1024 * 1024,
        max_zip_uncompressed_bytes=40 * 1024 * 1024,
        nginx_max_upload_bytes=50 * 1024 * 1024,
        backend_tmpfs_bytes=60 * 1024 * 1024,
    )

    with pytest.raises(ProductionConfigurationError) as captured:
        configured.validate_runtime_security()

    message = str(captured.value)
    for variable in (
        "CALOGRAPH_PUBLIC_URL",
        "COOKIE_SECURE",
        "ENABLE_HSTS",
        "TRUSTED_HOSTS",
        "TRUSTED_ORIGINS",
        "TRUSTED_PROXY_NETWORKS",
        "SESSION_SECRET",
        "RATE_LIMIT_SECRET",
        "DATABASE_URL",
        "CREDENTIAL_ENCRYPTION_KEY",
        "MAX_JSON_PAYLOAD_BYTES",
        "MAX_ZIP_UNCOMPRESSED_BYTES",
        "NGINX_MAX_UPLOAD_BYTES",
        "BACKEND_TMPFS_BYTES",
    ):
        assert variable in message
    assert secret not in message
