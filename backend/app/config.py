import ipaddress
from functools import lru_cache
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_name: str = "CaloGraph"
    environment: str = "production"
    database_url: str = "postgresql+psycopg://calograph:calograph@postgres:5432/calograph"
    session_secret: str = Field(default="development-session-secret-change-me-32", min_length=32)
    rate_limit_secret: str = Field(default="development-rate-limit-secret-change-me", min_length=32)
    calograph_timezone: str = "Europe/Berlin"
    calograph_public_url: str = "http://localhost:8180"
    cookie_secure: bool = False
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    trusted_origins: str = "http://localhost:8180,http://127.0.0.1:8180"
    trusted_proxy_networks: str = "127.0.0.1/32"
    enable_hsts: bool = False
    max_json_payload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024**2)
    max_upload_bytes: int = Field(default=500 * 1024 * 1024, ge=1024, le=2 * 1024**3)
    max_zip_uncompressed_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=1024,
        le=8 * 1024**3,
    )
    nginx_max_upload_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024,
        le=2 * 1024**3,
    )
    backend_tmpfs_bytes: int = Field(
        default=600 * 1024 * 1024,
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
    rate_limit_retention_hours: int = Field(default=24, ge=1, le=720)
    import_rate_limit: int = Field(default=30, ge=1, le=100_000)
    import_rate_limit_window_seconds: int = Field(default=60, ge=60, le=86_400)
    import_ip_rate_limit: int = Field(default=60, ge=1, le=100_000)
    file_import_user_rate_limit: int = Field(default=3, ge=1, le=1_000)
    file_import_ip_rate_limit: int = Field(default=6, ge=1, le=10_000)
    file_import_rate_limit_window_seconds: int = Field(
        default=3_600,
        ge=60,
        le=604_800,
    )
    credential_encryption_key: str = ""
    yazio_scheduler_poll_seconds: int = 60
    yazio_scheduler_jitter_minutes: int = 30

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

    @field_validator("credential_encryption_key")
    @classmethod
    def valid_credential_key(cls, value: str) -> str:
        if not value:
            return value
        try:
            Fernet(value.encode())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key"
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
