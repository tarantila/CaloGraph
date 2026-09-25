from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class WithingsSyncDomainResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    fetched_count: int
    persisted_count: int
    requested_start: date
    requested_end: date
    covered_start: date | None = None
    covered_end: date | None = None
    error_code: str | None = None


class WithingsSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    weight: WithingsSyncDomainResult
    activity_energy: WithingsSyncDomainResult
