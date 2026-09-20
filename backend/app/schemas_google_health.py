from __future__ import annotations

from datetime import date, datetime
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


class GoogleHealthSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    nutrition: GoogleHealthDomainResult
    activity_energy: GoogleHealthDomainResult
    weight: GoogleHealthDomainResult
