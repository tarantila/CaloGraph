from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

GoogleHealthState = Literal[
    "disabled",
    "not_connected",
    "active",
    "reauth_required",
    "scope_missing",
]


class GoogleHealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    configured: bool
    state: GoogleHealthState
    granted_scopes: tuple[str, ...] = ()
    refresh_token_expires_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class GoogleHealthOAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str
