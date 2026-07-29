import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    request_id: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=190)
    password: str = Field(min_length=8, max_length=1024)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    language: str
    timezone: str
    week_starts_on: int
    raw_payload_retention_days: int
    is_admin: bool


class CsrfResponse(BaseModel):
    csrf_token: str


class RegistrationRequest(BaseModel):
    username: str = Field(
        min_length=1,
        max_length=190,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,189}$",
    )
    password: str = Field(min_length=1, max_length=1024)


class InvitationExchangeRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class InvitationStateResponse(BaseModel):
    valid: bool


class InvitationCreateRequest(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=7)


class InvitationCreatedResponse(BaseModel):
    id: uuid.UUID
    invitation_url: str
    expires_at: datetime


class InvitationResponse(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


class ImportSummary(BaseModel):
    batch_id: uuid.UUID | None = None
    status: str
    received: int
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    unknown_types: list[str] = Field(default_factory=list)


class ImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str
    client_identifier: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    received: int
    inserted: int
    updated: int
    skipped: int
    failed: int
    unknown_types: list[str]
    error_message: str | None


class ImportErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_index: int | None
    metric_type: str | None
    error_code: str
    safe_detail: str


class ImportBatchDetailResponse(ImportBatchResponse):
    errors: list[ImportErrorResponse] = Field(default_factory=list)


class YazioStatusResponse(BaseModel):
    available: bool = True
    configured: bool
    sync_enabled: bool
    sync_interval_minutes: int | None = None
    sync_days: int | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_sync_at: datetime | None = None
    last_error: str | None = None


class YazioConnectionInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    interval_hours: int = Field(default=6, ge=1, le=168)
    sync_days: int = Field(default=7, ge=1, le=30)


class TokenCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None


class TokenCreatedResponse(BaseModel):
    id: uuid.UUID
    label: str
    token: str
    expires_at: datetime | None


class TokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    token_prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class TargetInput(BaseModel):
    valid_from: date
    calories_kcal: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    maintenance_kcal: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=3
    )
    protein_g: Decimal = Field(ge=0, max_digits=12, decimal_places=3)
    carbs_g: Decimal | None = Field(default=None, ge=0)
    fat_g: Decimal | None = Field(default=None, ge=0)
    fiber_g: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def maintenance_is_not_below_budget(self) -> Self:
        if (
            self.maintenance_kcal is not None
            and self.maintenance_kcal < self.calories_kcal
        ):
            raise ValueError(
                "Der Erhaltungsbedarf darf nicht unter dem Kalorienbudget liegen"
            )
        return self


class TargetResponse(TargetInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    valid_to: date | None


class ProfileUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)
    week_starts_on: int = Field(ge=0, le=6)
    raw_payload_retention_days: int = Field(default=0, ge=0, le=3650)


class TrackingQualityInput(BaseModel):
    calories_full_ratio: Decimal = Field(gt=0, le=2)
    calories_partial_ratio: Decimal = Field(gt=0, le=2)
    median_full_ratio: Decimal = Field(gt=0, le=2)
    median_partial_ratio: Decimal = Field(gt=0, le=2)
    complete_score: int = Field(ge=1, le=8)
    probably_complete_score: int = Field(ge=1, le=8)
    probably_incomplete_score: int = Field(ge=1, le=8)


class TrackingQualityResponse(TrackingQualityInput):
    model_config = ConfigDict(from_attributes=True)


TrackingStatus = Literal[
    "complete", "probably_complete", "probably_incomplete", "incomplete", "no_data"
]


class TrackingOverrideInput(BaseModel):
    status: TrackingStatus
    note: str | None = Field(default=None, max_length=500)


class DailyPoint(BaseModel):
    date: date
    calories_kcal: Decimal | None
    target_kcal: Decimal | None
    maintenance_kcal: Decimal | None
    deviation_kcal: Decimal | None
    protein_g: Decimal | None
    carbs_g: Decimal | None
    fat_g: Decimal | None
    tracking_status: TrackingStatus
    tracking_score: int
    tracking_reasons: list[str]


class TargetSettingsResponse(BaseModel):
    targets: list[TargetResponse]
