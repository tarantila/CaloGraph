from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

WithingsState = Literal[
    "disabled",
    "not_configured",
    "not_connected",
    "active",
    "reauth_required",
    "error",
]


class WithingsCredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    client_id: str = Field(max_length=512, repr=False, exclude=True)
    client_secret: str = Field(max_length=512, repr=False, exclude=True)

    @field_validator("client_id", "client_secret")
    @classmethod
    def validate_credential_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("credential values must not be blank")
        if any(ord(char) < 0x20 for char in value):
            raise ValueError("credential values contain unsupported characters")
        return value


class WithingsStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    available: bool
    credentials_configured: bool = False
    redirect_uri: str = ""
    configured: bool
    connected: bool
    state: WithingsState
    granted_scopes: tuple[str, ...] = ()
    access_token_expires_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_category: str | None = None


class WithingsOAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str


class WithingsConnectionTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    state: WithingsState
    error_category: str | None = None
