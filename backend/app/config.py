from functools import lru_cache

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

    @field_validator("raw_payload_retention_days")
    @classmethod
    def retention_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("RAW_PAYLOAD_RETENTION_DAYS must be zero or positive")
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
