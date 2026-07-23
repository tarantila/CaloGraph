from functools import lru_cache

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
    cookie_secure: bool = False
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    trusted_origins: str = "http://localhost:8180,http://127.0.0.1:8180"
    trusted_proxy_networks: str = "127.0.0.1/32"
    enable_hsts: bool = False
    max_json_payload_bytes: int = 10 * 1024 * 1024
    max_upload_bytes: int = 1024 * 1024 * 1024
    max_zip_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    max_zip_entries: int = 20
    raw_payload_retention_days: int = 0
    login_rate_limit: int = 10
    import_rate_limit: int = 30
    credential_encryption_key: str = ""
    yazio_scheduler_poll_seconds: int = 60
    yazio_scheduler_jitter_minutes: int = 30

    @field_validator("raw_payload_retention_days")
    @classmethod
    def retention_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("RAW_PAYLOAD_RETENTION_DAYS must be zero or positive")
        return value

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
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def trusted_origin_list(self) -> list[str]:
        return [
            item.strip().rstrip("/") for item in self.trusted_origins.split(",") if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
