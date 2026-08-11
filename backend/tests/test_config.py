import logging
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.engine import make_url

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
        "trusted_proxy_networks": "172.30.0.10/32",
        "enable_hsts": True,
        "credential_encryption_key": Fernet.generate_key().decode(),
        "mfa_encryption_key": Fernet.generate_key().decode(),
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


def test_pydantic_settings_debug_does_not_log_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "synthetic-debug-secret-0123456789abcdef"
    monkeypatch.setenv("PYDANTIC_SETTINGS_DEBUG", "1")
    caplog.set_level(logging.DEBUG)

    configured = Settings(
        _env_file=None,
        environment="development",
        session_secret=secret,
    )

    assert configured.session_secret == secret
    assert secret not in caplog.text


def write_secret(path: Path, value: str | bytes) -> Path:
    path.write_bytes(value.encode() if isinstance(value, str) else value)
    return path


def clear_direct_secret_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "DATABASE_URL",
        "DATABASE_PASSWORD",
        "SESSION_SECRET",
        "RATE_LIMIT_SECRET",
        "CREDENTIAL_ENCRYPTION_KEY",
        "MFA_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_secret_files_are_loaded_and_database_url_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_direct_secret_environment(monkeypatch)
    database_password = "database password/with?#characters"
    credential_key = Fernet.generate_key().decode()
    mfa_key = Fernet.generate_key().decode()
    configured = Settings(
        _env_file=None,
        environment="development",
        database_host="database.internal",
        database_port=5544,
        database_name="nutrition",
        database_user="calograph-user",
        database_password_file=str(
            write_secret(tmp_path / "database-password", f"{database_password}\n")
        ),
        session_secret_file=str(
            write_secret(
                tmp_path / "session-secret",
                "session-secret-from-file-0123456789\n",
            )
        ),
        rate_limit_secret_file=str(
            write_secret(
                tmp_path / "rate-limit-secret",
                "rate-limit-secret-from-file-0123456789\r\n",
            )
        ),
        credential_encryption_key_file=str(
            write_secret(tmp_path / "credential-key", f"{credential_key}\n")
        ),
        mfa_encryption_key_file=str(
            write_secret(tmp_path / "mfa-key", f"{mfa_key}\n")
        ),
    )

    database_url = make_url(configured.database_url)
    assert database_url.host == "database.internal"
    assert database_url.port == 5544
    assert database_url.database == "nutrition"
    assert database_url.username == "calograph-user"
    assert database_url.password == database_password
    assert configured.session_secret == "session-secret-from-file-0123456789"
    assert configured.rate_limit_secret == "rate-limit-secret-from-file-0123456789"
    assert configured.credential_encryption_key == credential_key
    assert configured.mfa_encryption_key == mfa_key
    rendered = repr(configured)
    serialized = configured.model_dump()
    for sensitive_value in (
        database_password,
        configured.session_secret,
        configured.rate_limit_secret,
        credential_key,
        mfa_key,
        str(tmp_path),
    ):
        assert sensitive_value not in rendered
        assert sensitive_value not in str(serialized)
    for excluded_field in (
        "database_url",
        "database_password",
        "database_password_file",
        "session_secret",
        "session_secret_file",
        "rate_limit_secret",
        "rate_limit_secret_file",
        "credential_encryption_key",
        "credential_encryption_key_file",
        "mfa_encryption_key",
        "mfa_encryption_key_file",
    ):
        assert excluded_field not in serialized


@pytest.mark.parametrize(
    ("direct_field", "file_field", "direct_value"),
    [
        (
            "session_secret",
            "session_secret_file",
            "direct-session-secret-value-0123456789",
        ),
        (
            "rate_limit_secret",
            "rate_limit_secret_file",
            "direct-rate-limit-secret-0123456789",
        ),
        (
            "credential_encryption_key",
            "credential_encryption_key_file",
            Fernet.generate_key().decode(),
        ),
        (
            "mfa_encryption_key",
            "mfa_encryption_key_file",
            Fernet.generate_key().decode(),
        ),
        (
            "database_password",
            "database_password_file",
            "direct-database-password",
        ),
        (
            "database_url",
            "database_password_file",
            "postgresql+psycopg://calograph:direct-password@postgres/calograph",
        ),
    ],
)
def test_direct_and_file_secret_sources_are_rejected_without_leaking_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    direct_field: str,
    file_field: str,
    direct_value: str,
) -> None:
    clear_direct_secret_environment(monkeypatch)
    secret_file = write_secret(tmp_path / "sensitive-path-name", "file-secret-value")

    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            environment="development",
            **{
                direct_field: direct_value,
                file_field: str(secret_file),
            },
        )

    message = str(captured.value)
    assert direct_value not in message
    assert str(secret_file) not in message
    assert "file-secret-value" not in message


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"private-value\x00nul",
        b"\xff\xfe",
        b"x" * (16 * 1024 + 1),
    ],
)
def test_invalid_secret_files_fail_without_leaking_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
) -> None:
    clear_direct_secret_environment(monkeypatch)
    secret_file = write_secret(tmp_path / "database-password", content)

    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            environment="development",
            database_password_file=str(secret_file),
        )

    message = str(captured.value)
    assert str(secret_file) not in message
    assert "private-value" not in message
    assert "x" * 64 not in message


def test_scheduler_production_validation_only_requires_its_own_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_direct_secret_environment(monkeypatch)
    database_secret = write_secret(
        tmp_path / "database-password",
        "scheduler-database-password-0123456789",
    )
    credential_secret = write_secret(
        tmp_path / "credential-key",
        Fernet.generate_key(),
    )
    rate_limit_secret = write_secret(
        tmp_path / "rate-limit-secret",
        "scheduler-rate-limit-secret-0123456789",
    )
    configured = Settings(
        _env_file=None,
        environment="production",
        database_password_file=str(database_secret),
        credential_encryption_key_file=str(credential_secret),
        rate_limit_secret_file=str(rate_limit_secret),
        yazio_enabled=True,
    )

    configured.validate_runtime_security("scheduler")
    with pytest.raises(ProductionConfigurationError):
        configured.validate_runtime_security("backend")


def test_scheduler_rejects_default_rate_limit_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_direct_secret_environment(monkeypatch)
    configured = Settings(
        _env_file=None,
        environment="production",
        database_password_file=str(
            write_secret(
                tmp_path / "database-password",
                "scheduler-database-password-0123456789",
            )
        ),
        credential_encryption_key_file=str(
            write_secret(tmp_path / "credential-key", Fernet.generate_key())
        ),
        yazio_enabled=True,
    )

    with pytest.raises(ProductionConfigurationError, match="RATE_LIMIT_SECRET"):
        configured.validate_runtime_security("scheduler")


def test_valid_production_configuration_passes_runtime_check() -> None:
    valid_production_settings().validate_runtime_security()


@pytest.mark.parametrize(
    "proxy_network",
    ["172.30.0.0/24", "172.18.0.0/16", "2001:db8::/64"],
)
def test_production_rejects_proxy_subnets(proxy_network: str) -> None:
    configured = valid_production_settings(trusted_proxy_networks=proxy_network)

    with pytest.raises(ProductionConfigurationError, match="exact proxy IP"):
        configured.validate_runtime_security()


@pytest.mark.parametrize(
    "proxy_address",
    ["172.30.0.10/32", "2001:db8::10/128"],
)
def test_production_accepts_exact_proxy_addresses(proxy_address: str) -> None:
    configured = valid_production_settings(trusted_proxy_networks=proxy_address)

    configured.validate_runtime_security()


def test_production_backend_requires_dedicated_mfa_encryption_key() -> None:
    configured = valid_production_settings(mfa_encryption_key="")

    with pytest.raises(ProductionConfigurationError, match="MFA_ENCRYPTION_KEY"):
        configured.validate_runtime_security("backend")


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
        mfa_encryption_key="",
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
        "MFA_ENCRYPTION_KEY",
        "MAX_JSON_PAYLOAD_BYTES",
        "MAX_ZIP_UNCOMPRESSED_BYTES",
        "NGINX_MAX_UPLOAD_BYTES",
        "BACKEND_TMPFS_BYTES",
    ):
        assert variable in message
    assert secret not in message
