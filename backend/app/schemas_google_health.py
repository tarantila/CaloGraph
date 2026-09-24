from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GoogleHealthState = Literal[
    "disabled",
    "not_configured",
    "not_connected",
    "active",
    "reauth_required",
    "scope_missing",
]


class GoogleHealthCredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    client_id: str = Field(max_length=512)
    client_secret: str | None = Field(default=None, max_length=512, repr=False)

    @field_validator("client_id", "client_secret")
    @classmethod
    def validate_credential_value(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if not value.strip():
            raise ValueError("credential values must not be blank")
        if any(ord(char) < 0x20 for char in value):
            raise ValueError("credential values contain unsupported characters")
        return value


class GoogleHealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    configured: bool
    client_id_configured: bool = False
    client_secret_configured: bool = False
    redirect_uri: str = ""
    state: GoogleHealthState
    sync_state: str = "idle"
    retry_attempt: int = 0
    retry_max_attempts: int = 0
    next_retry_at: datetime | None = None
    granted_scopes: tuple[str, ...] = ()
    refresh_token_expires_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_category: str | None = None


GoogleHealthConnectionTestStatus = Literal["connected", "reauth_required", "failed"]
GoogleHealthConnectionTestErrorCategory = Literal[
    "credential_unavailable",
    "credentials_unavailable",
    "reauth_required",
    "scope_missing",
    "rate_limited",
    "provider_error",
    "transient_error",
    "invalid_response",
]


class GoogleHealthConnectionTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: GoogleHealthConnectionTestStatus
    error_category: GoogleHealthConnectionTestErrorCategory | None = None


class GoogleHealthOAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str


class GoogleHealthDomainDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    operation: str
    endpoint_key: str
    parser_stage: str
    structural_reason_code: str | None = None
    error_category: str
    upstream_status_code: int | None = None
    retryable: bool
    reauth_required: bool
    chunk_index: int | None = None
    page_index: int | None = None
    point_index: int | None = None
    field_path: str | None = None
    validation_rule: str | None = None
    numeric_reason_code: str | None = None
    observed_json_type: str | None = None
    expected_json_type: str | None = None


class GoogleHealthDomainResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    fetched_count: int
    persisted_count: int
    requested_start: date
    requested_end: date
    covered_start: date | None = None
    covered_end: date | None = None
    error_code: str | None = None
    diagnostic: GoogleHealthDomainDiagnostic | None = None

class GoogleHealthSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    nutrition: GoogleHealthDomainResult
    activity_energy: GoogleHealthDomainResult
    weight: GoogleHealthDomainResult
