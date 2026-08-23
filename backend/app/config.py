import ipaddress
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# pydantic-settings can emit raw source values when its debug flag is enabled.
logging.getLogger("pydantic_settings").setLevel(logging.INFO)


MAX_SECRET_FILE_BYTES = 16 * 1024
APPLE_HEALTH_UPLOAD_GLOBAL_SLOTS = 2
BACKEND_TMPFS_RESERVE_BYTES = 16 * 1024 * 1024
RuntimeRole = Literal["backend", "scheduler"]

KNOWN_INSECURE_SECRETS = frozenset(
    {
        "development-session-secret-change-me-32",
        "development-rate-limit-secret-change-me",
        "change_me_at_least_32_random_characters",
        "change_me_another_random_secret",
    }
)
KNOWN_INSECURE_DATABASE_PASSWORDS = frozenset(
    {
        "calograph",
        "admin",
        "root",
        "secret",
        "letmein",
        "postgres",
        "password",
        "changeme",
        "change_me",
        "change_me_database_password",
    }
)


class ProductionConfigurationError(RuntimeError):
    pass


def _read_secret_file(
    path_value: object,
    variable_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(path_value, (str, os.PathLike)):
        raise ValueError(f"{variable_name} must point to a readable secret file")
    try:
        with Path(path_value).open("rb") as handle:
            raw = handle.read(MAX_SECRET_FILE_BYTES + 1)
    except OSError:
        raise ValueError(f"{variable_name} must point to a readable secret file") from None
    if len(raw) > MAX_SECRET_FILE_BYTES:
        raise ValueError(f"{variable_name} exceeds the secret file size limit")
    try:
        value = raw.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        raise ValueError(f"{variable_name} must contain UTF-8 text") from None
    if "\x00" in value or (not value and not allow_empty):
        raise ValueError(f"{variable_name} contains an invalid secret")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "CaloGraph"
    environment: Literal["development", "test", "production"]
    database_url: str = Field(default="", exclude=True, repr=False)
    database_host: str = Field(default="postgres", min_length=1, max_length=253)
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(default="calograph", min_length=1, max_length=63)
    database_user: str = Field(default="calograph", min_length=1, max_length=63)
    database_password: str | None = Field(default=None, exclude=True, repr=False)
    database_password_file: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )
    session_secret: str = Field(
        default="development-session-secret-change-me-32",
        min_length=32,
        exclude=True,
        repr=False,
    )
    session_secret_file: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )
    rate_limit_secret: str = Field(
        default="development-rate-limit-secret-change-me",
        min_length=32,
        exclude=True,
        repr=False,
    )
    rate_limit_secret_file: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )
    calograph_timezone: str = "Europe/Berlin"
    calograph_public_url: str = "http://localhost:8180"
    cookie_secure: bool = False
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    trusted_origins: str = "http://localhost:8180,http://127.0.0.1:8180"
    trusted_proxy_networks: str = "127.0.0.1/32"
    enable_api_docs: bool = False
    enable_hsts: bool = False
    hsts_include_subdomains: bool = False
    max_json_payload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024**2)
    max_upload_bytes: int = Field(default=500 * 1024 * 1024, ge=1024, le=2 * 1024**3)
    max_zip_uncompressed_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024,
        le=8 * 1024**3,
    )
    nginx_max_upload_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024,
        le=2 * 1024**3,
    )
    backend_tmpfs_bytes: int = Field(
        default=APPLE_HEALTH_UPLOAD_GLOBAL_SLOTS * (512 * 1024 * 1024)
        + BACKEND_TMPFS_RESERVE_BYTES,
        ge=1024,
        le=4 * 1024**3,
    )
    max_zip_entries: int = Field(default=20, ge=1, le=1_000)
    max_import_records: int = Field(default=1_000_000, ge=1, le=10_000_000)
    max_import_samples: int = Field(default=250_000, ge=1, le=5_000_000)
    max_import_errors: int = Field(default=1_000, ge=1, le=100_000)
    max_import_unknown_types: int = Field(default=100, ge=1, le=10_000)
    import_batch_size: int = Field(default=500, ge=50, le=5_000)
    raw_payload_retention_days: int = 0
    login_rate_limit: int = Field(default=10, ge=1, le=10_000)
    login_rate_limit_window_seconds: int = Field(default=900, ge=60, le=86_400)
    login_ip_rate_limit: int = Field(default=30, ge=1, le=100_000)
    login_ip_rate_limit_window_seconds: int = Field(default=300, ge=60, le=86_400)
    password_change_rate_limit: int = Field(default=5, ge=1, le=1_000)
    password_change_rate_limit_window_seconds: int = Field(
        default=900, ge=60, le=86_400
    )
    mfa_rate_limit: int = Field(default=10, ge=1, le=1_000)
    mfa_rate_limit_window_seconds: int = Field(default=300, ge=60, le=86_400)
    mfa_ip_rate_limit: int = Field(default=30, ge=1, le=10_000)
    passkey_ip_rate_limit: int = Field(default=30, ge=1, le=10_000)
    passkey_rate_limit_window_seconds: int = Field(default=300, ge=60, le=86_400)
    recovery_rate_limit: int = Field(default=10, ge=1, le=1_000)
    recovery_ip_rate_limit: int = Field(default=30, ge=1, le=10_000)
    recovery_rate_limit_window_seconds: int = Field(default=900, ge=60, le=86_400)
    session_idle_timeout_hours: int = Field(default=168, ge=1, le=168)
    session_absolute_timeout_days: int = Field(default=30, ge=1, le=30)
    rate_limit_retention_hours: int = Field(default=24, ge=1, le=720)
    import_rate_limit: int = Field(default=30, ge=1, le=100_000)
    import_rate_limit_window_seconds: int = Field(default=60, ge=60, le=86_400)
    import_ip_rate_limit: int = Field(default=60, ge=1, le=100_000)
    reconcile_rate_limit: int = Field(default=12, ge=1, le=100_000)
    reconcile_ip_rate_limit: int = Field(default=24, ge=1, le=100_000)
    reconcile_rate_limit_window_seconds: int = Field(default=300, ge=60, le=86_400)
    file_import_user_rate_limit: int = Field(default=3, ge=1, le=1_000)
    file_import_ip_rate_limit: int = Field(default=6, ge=1, le=10_000)
    file_import_rate_limit_window_seconds: int = Field(
        default=3_600,
        ge=60,
        le=604_800,
    )
    credential_encryption_key: str = Field(default="", exclude=True, repr=False)
    credential_encryption_key_file: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )
    mfa_encryption_key: str = Field(default="", exclude=True, repr=False)
    mfa_encryption_key_file: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )
    yazio_enabled: bool = True
    yazio_sync_interval_hours: int = Field(default=6, ge=1, le=168)
    yazio_sync_days: int = Field(default=7, ge=1, le=366)
    yazio_connect_timeout_seconds: float = Field(default=3.05, ge=0.1, le=30)
    yazio_read_timeout_seconds: float = Field(default=15, ge=1, le=120)
    yazio_login_deadline_seconds: int = Field(default=25, ge=5, le=120)
    yazio_operation_deadline_seconds: int = Field(default=300, ge=30, le=1_800)
    yazio_request_workers: int = Field(default=3, ge=1, le=10)
    yazio_rate_limit: int = Field(default=2, ge=1, le=100)
    yazio_rate_limit_window_seconds: int = Field(default=600, ge=60, le=86_400)
    yazio_max_parallel_operations: int = Field(default=2, ge=1, le=8)
    yazio_circuit_failure_limit: int = Field(default=5, ge=1, le=100)
    yazio_circuit_window_seconds: int = Field(default=600, ge=60, le=86_400)
    yazio_scheduler_poll_seconds: int = 60
    yazio_scheduler_jitter_minutes: int = 30

    @model_validator(mode="before")
    @classmethod
    def load_secret_files(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        secret_fields = (
            ("session_secret", "session_secret_file", "SESSION_SECRET_FILE", False),
            ("rate_limit_secret", "rate_limit_secret_file", "RATE_LIMIT_SECRET_FILE", False),
            (
                "credential_encryption_key",
                "credential_encryption_key_file",
                "CREDENTIAL_ENCRYPTION_KEY_FILE",
                True,
            ),
            (
                "mfa_encryption_key",
                "mfa_encryption_key_file",
                "MFA_ENCRYPTION_KEY_FILE",
                False,
            ),
        )
        for value_field, file_field, variable_name, allow_empty in secret_fields:
            file_path = values.get(file_field)
            if file_path is None:
                continue
            if value_field in values:
                direct_name = variable_name.removesuffix("_FILE")
                raise ValueError(f"{direct_name} and {variable_name} must not both be set")
            values[value_field] = _read_secret_file(
                file_path,
                variable_name,
                allow_empty=allow_empty,
            )

        password_file = values.get("database_password_file")
        if password_file is not None:
            if "database_url" in values or "database_password" in values:
                raise ValueError(
                    "DATABASE_URL or DATABASE_PASSWORD and DATABASE_PASSWORD_FILE "
                    "must not both be set"
                )
            values["database_password"] = _read_secret_file(
                password_file,
                "DATABASE_PASSWORD_FILE",
            )
        return values

    @model_validator(mode="after")
    def build_database_url(self) -> Settings:
        if self.database_url:
            return self
        password = self.database_password or "calograph"
        self.database_url = URL.create(
            drivername="postgresql+psycopg",
            username=self.database_user,
            password=password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)
        return self

    @field_validator("raw_payload_retention_days")
    @classmethod
    def retention_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("RAW_PAYLOAD_RETENTION_DAYS must be zero or positive")
        return value

    @field_validator("calograph_public_url")
    @classmethod
    def valid_public_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "CALOGRAPH_PUBLIC_URL must be an absolute HTTP(S) origin without a path"
            )
        return normalized

    @field_validator("trusted_proxy_networks")
    @classmethod
    def valid_proxy_networks(cls, value: str) -> str:
        entries = [item.strip() for item in value.split(",") if item.strip()]
        if not entries:
            raise ValueError("TRUSTED_PROXY_NETWORKS must contain at least one IP or network")
        for entry in entries:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(
                    "TRUSTED_PROXY_NETWORKS must contain comma-separated IP addresses or CIDRs"
                ) from exc
        return ",".join(entries)

    @field_validator("credential_encryption_key", "mfa_encryption_key")
    @classmethod
    def valid_credential_key(cls, value: str) -> str:
        if not value:
            return value
        try:
            Fernet(value.encode())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Encryption keys must be valid Fernet keys"
            ) from exc
        return value

    @field_validator("yazio_scheduler_poll_seconds")
    @classmethod
    def valid_scheduler_poll(cls, value: int) -> int:
        if not 10 <= value <= 3600:
            raise ValueError("YAZIO_SCHEDULER_POLL_SECONDS must be between 10 and 3600")
        return value

    @field_validator("yazio_scheduler_jitter_minutes")
    @classmethod
    def valid_scheduler_jitter(cls, value: int) -> int:
        if not 0 <= value <= 180:
            raise ValueError("YAZIO_SCHEDULER_JITTER_MINUTES must be between 0 and 180")
        return value

    def validate_runtime_security(self, role: RuntimeRole = "backend") -> None:
        if role not in {"backend", "scheduler"}:
            raise ValueError("unknown runtime role")
        if self.environment != "production":
            return

        errors: list[str] = []
        if role == "backend":
            public_url = urlsplit(self.calograph_public_url)
            public_host = (public_url.hostname or "").casefold()
            configured_hosts = {
                item.strip().casefold()
                for item in self.trusted_hosts.split(",")
                if item.strip()
            }
            configured_origins = {
                item.strip().rstrip("/")
                for item in self.trusted_origins.split(",")
                if item.strip()
            }

            if public_url.scheme != "https":
                errors.append("CALOGRAPH_PUBLIC_URL must use https://")
            if (
                not public_host
                or public_host in {"localhost", "127.0.0.1", "::1"}
                or public_host in {"example.com", "example.net", "example.org"}
                or public_host.endswith(
                    (".example", ".example.com", ".example.net", ".example.org", ".example.test")
                )
            ):
                errors.append("CALOGRAPH_PUBLIC_URL must contain the real public hostname")
            if not self.cookie_secure:
                errors.append("COOKIE_SECURE must be true")
            if not self.enable_hsts:
                errors.append("ENABLE_HSTS must be true")
            if public_host not in configured_hosts:
                errors.append("TRUSTED_HOSTS must explicitly contain the public hostname")
            if self.calograph_public_url not in configured_origins:
                errors.append("TRUSTED_ORIGINS must explicitly contain CALOGRAPH_PUBLIC_URL")
            if any("*" in host for host in configured_hosts):
                errors.append("TRUSTED_HOSTS must not contain wildcard entries")
            if any(not origin.startswith("https://") for origin in configured_origins):
                errors.append("all production TRUSTED_ORIGINS entries must use https://")

            proxy_networks = [
                ipaddress.ip_network(item, strict=False)
                for item in self.trusted_proxy_networks.split(",")
                if item
            ]
            if all(network.is_loopback for network in proxy_networks):
                errors.append(
                    "TRUSTED_PROXY_NETWORKS must contain the actual frontend proxy IP"
                )
            if any(
                network.prefixlen != network.max_prefixlen
                for network in proxy_networks
            ):
                errors.append(
                    "TRUSTED_PROXY_NETWORKS must use exact proxy IP addresses (/32 or /128)"
                )

            normalized_session_secret = self.session_secret.strip().casefold()
            if (
                normalized_session_secret in KNOWN_INSECURE_SECRETS
                or "change_me" in normalized_session_secret
                or "changeme" in normalized_session_secret
            ):
                errors.append("SESSION_SECRET uses a known development or placeholder value")
            if self.session_secret == self.rate_limit_secret:
                errors.append("SESSION_SECRET and RATE_LIMIT_SECRET must be independent")

        normalized_rate_limit_secret = self.rate_limit_secret.strip().casefold()
        if (
            normalized_rate_limit_secret in KNOWN_INSECURE_SECRETS
            or "change_me" in normalized_rate_limit_secret
            or "changeme" in normalized_rate_limit_secret
        ):
            errors.append("RATE_LIMIT_SECRET uses a known development or placeholder value")

        database_url = urlsplit(self.database_url)
        database_password = unquote(database_url.password or "").strip().casefold()
        if not database_url.scheme.startswith("postgresql"):
            errors.append("DATABASE_URL must use PostgreSQL")
        if (
            not database_password
            or database_password in KNOWN_INSECURE_DATABASE_PASSWORDS
            or "change_me" in database_password
            or "changeme" in database_password
        ):
            errors.append("DATABASE_URL must contain a non-default database password")

        if self.yazio_enabled and not self.credential_encryption_key:
            errors.append(
                "CREDENTIAL_ENCRYPTION_KEY is required when YAZIO_ENABLED is true"
            )
        if role == "backend" and not self.mfa_encryption_key:
            errors.append("MFA_ENCRYPTION_KEY is required for the backend")

        if role == "backend":
            if self.max_json_payload_bytes > self.max_upload_bytes:
                errors.append("MAX_JSON_PAYLOAD_BYTES must not exceed MAX_UPLOAD_BYTES")
            if self.max_zip_uncompressed_bytes < self.max_upload_bytes:
                errors.append(
                    "MAX_ZIP_UNCOMPRESSED_BYTES must not be smaller than MAX_UPLOAD_BYTES"
                )
            if self.nginx_max_upload_bytes < self.max_upload_bytes + 1024 * 1024:
                errors.append(
                    "NGINX_MAX_UPLOAD_BYTES needs multipart overhead above MAX_UPLOAD_BYTES"
                )
            required_backend_tmpfs_bytes = (
                APPLE_HEALTH_UPLOAD_GLOBAL_SLOTS * self.nginx_max_upload_bytes
                + BACKEND_TMPFS_RESERVE_BYTES
            )
            if self.backend_tmpfs_bytes < required_backend_tmpfs_bytes:
                errors.append(
                    "BACKEND_TMPFS_BYTES must cover "
                    f"{APPLE_HEALTH_UPLOAD_GLOBAL_SLOTS} concurrent uploads plus "
                    f"{BACKEND_TMPFS_RESERVE_BYTES // (1024 * 1024)} MiB reserve"
                )

        if errors:
            formatted = "\n".join(f"- {error}" for error in errors)
            raise ProductionConfigurationError(
                f"Unsafe production configuration:\n{formatted}"
            )

    @property
    def trusted_host_list(self) -> list[str]:
        hosts = [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]
        public_host = urlsplit(self.calograph_public_url).hostname
        if public_host and public_host not in hosts:
            hosts.append(public_host)
        return hosts

    @property
    def trusted_origin_list(self) -> list[str]:
        origins = [
            item.strip().rstrip("/") for item in self.trusted_origins.split(",") if item.strip()
        ]
        if self.calograph_public_url not in origins:
            origins.append(self.calograph_public_url)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


if __name__ == "__main__":
    if sys.argv[1:] != ["--check-runtime"]:
        raise SystemExit("Usage: python -m app.config --check-runtime")
    try:
        settings.validate_runtime_security()
    except ProductionConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(78) from None
