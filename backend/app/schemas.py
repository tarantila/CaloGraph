import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

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


class AdminReauthenticationRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, max_length=64)


class HardDeleteRequest(AdminReauthenticationRequest):
    confirm_username: str = Field(min_length=1, max_length=190)


class AccountRecoveryCompleteRequest(BaseModel):
    recovery_token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=1024)


class AccountRecoveryIssuedResponse(BaseModel):
    recovery_token: str
    expires_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    language: str
    timezone: str
    week_starts_on: int
    raw_payload_retention_days: int
    is_admin: bool
    is_active: bool
    deactivated_at: datetime | None


class CsrfResponse(BaseModel):
    csrf_token: str


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class MfaManagementRequest(MfaCodeRequest):
    current_password: str = Field(min_length=8, max_length=1024)


class TotpSetupRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=1024)


class TotpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    qr_svg_data_url: str


class MfaStatusResponse(BaseModel):
    totp_enabled: bool
    totp_setup_pending: bool
    recovery_codes_remaining: int


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


WebAuthnTransport = Literal["usb", "nfc", "ble", "smart-card", "hybrid", "internal"]


class WebAuthnAttestationResponseInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_data_json: str = Field(alias="clientDataJSON", min_length=1, max_length=16_384)
    attestation_object: str = Field(
        alias="attestationObject",
        min_length=1,
        max_length=2 * 1024 * 1024,
    )
    transports: list[WebAuthnTransport] = Field(default_factory=list, max_length=10)


class WebAuthnAssertionResponseInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_data_json: str = Field(alias="clientDataJSON", min_length=1, max_length=16_384)
    authenticator_data: str = Field(
        alias="authenticatorData",
        min_length=1,
        max_length=16_384,
    )
    signature: str = Field(min_length=1, max_length=16_384)
    user_handle: str | None = Field(
        default=None,
        alias="userHandle",
        max_length=2_048,
    )


class WebAuthnRegistrationCredentialInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=2_048, pattern=r"^[A-Za-z0-9_-]+$")
    raw_id: str = Field(
        alias="rawId",
        min_length=1,
        max_length=2_048,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    response: WebAuthnAttestationResponseInput
    authenticator_attachment: Literal["platform", "cross-platform"] | None = Field(
        default=None,
        alias="authenticatorAttachment",
    )
    client_extension_results: dict[str, object] = Field(
        default_factory=dict,
        alias="clientExtensionResults",
        max_length=20,
    )
    type: Literal["public-key"]


class WebAuthnAuthenticationCredentialInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=2_048, pattern=r"^[A-Za-z0-9_-]+$")
    raw_id: str = Field(
        alias="rawId",
        min_length=1,
        max_length=2_048,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    response: WebAuthnAssertionResponseInput
    authenticator_attachment: Literal["platform", "cross-platform"] | None = Field(
        default=None,
        alias="authenticatorAttachment",
    )
    client_extension_results: dict[str, object] = Field(
        default_factory=dict,
        alias="clientExtensionResults",
        max_length=20,
    )
    type: Literal["public-key"]


class PasskeyRegistrationOptionsRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=1024)
    code: str | None = Field(default=None, min_length=6, max_length=64)


class PasskeyRegistrationCompleteRequest(BaseModel):
    challenge_id: uuid.UUID
    label: str = Field(min_length=1, max_length=100)
    credential: WebAuthnRegistrationCredentialInput


class PasskeyAuthenticationCompleteRequest(BaseModel):
    challenge_id: uuid.UUID
    credential: WebAuthnAuthenticationCredentialInput


class PasskeyDeleteRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=1024)
    code: str | None = Field(default=None, min_length=6, max_length=64)


class WebAuthnOptionsResponse(BaseModel):
    challenge_id: uuid.UUID
    public_key: dict[str, Any]


class PasskeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    device_type: str
    backed_up: bool
    created_at: datetime
    last_used_at: datetime | None


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


class YazioHistoricalSyncResponse(BaseModel):
    state: Literal["idle", "pending", "running", "completed", "failed"]
    start_date: date | None = None
    end_date: date | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None


class YazioStatusResponse(BaseModel):
    available: bool = True
    configured: bool
    sync_enabled: bool
    sync_interval_minutes: int | None = None
    sync_days: int | None = None
    sync_interval_override_minutes: int | None = None
    sync_days_override: int | None = None
    historical_sync: YazioHistoricalSyncResponse | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_sync_at: datetime | None = None
    last_error: str | None = None


class YazioConnectionInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    sync_days: int | None = Field(default=None, ge=1, le=366)
    from_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def date_range_is_complete(self) -> YazioConnectionInput:
        if (self.from_date is None) != (self.end_date is None):
            raise ValueError("Von- und Bis-Datum müssen zusammen angegeben werden.")
        if (
            self.from_date is not None
            and self.end_date is not None
            and self.from_date > self.end_date
        ):
            raise ValueError("Das Von-Datum darf nicht nach dem Bis-Datum liegen.")
        return self


class YazioHistoricalRangeInput(BaseModel):
    from_date: date
    end_date: date

    @model_validator(mode="after")
    def dates_are_ordered(self) -> YazioHistoricalRangeInput:
        if self.from_date > self.end_date:
            raise ValueError("Das Von-Datum darf nicht nach dem Bis-Datum liegen.")
        return self


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
    activity_mode: Literal["off", "full"] = "off"
    activity_source_type: Literal[
        "yazio_export_v1", "apple_health_xml", "health_auto_export_v2"
    ] | None = None
    protein_g: Decimal = Field(ge=0, max_digits=12, decimal_places=3)
    carbs_g: Decimal | None = Field(default=None, ge=0)
    fat_g: Decimal | None = Field(default=None, ge=0)
    fiber_g: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def activity_source_matches_mode(self) -> TargetInput:
        if self.activity_mode == "off" and self.activity_source_type is not None:
            raise ValueError("Im deaktivierten Modus darf keine Aktivitätsquelle gesetzt sein")
        if self.activity_mode == "full" and self.activity_source_type is None:
            raise ValueError("Für Aktivitätskalorien muss eine Quelle ausgewählt werden")
        return self


class TargetResponse(TargetInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    valid_to: date | None


class ProfileUpdate(BaseModel):
    language: Literal["de", "en"] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    week_starts_on: int | None = Field(default=None, ge=0, le=6)
    raw_payload_retention_days: int | None = Field(default=None, ge=0, le=3650)


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


ActivityDataStatus = Literal["disabled", "disabled_with_data", "missing", "credited"]


class DailyPoint(BaseModel):
    date: date
    calories_kcal: Decimal | None
    target_kcal: Decimal | None
    maintenance_kcal: Decimal | None
    deviation_kcal: Decimal | None
    activity_mode: Literal["off", "full"] | None
    activity_source_type: str | None
    active_energy_kcal: Decimal | None
    activity_credit_kcal: Decimal
    activity_data_status: ActivityDataStatus
    effective_budget_kcal: Decimal | None
    effective_maintenance_kcal: Decimal | None
    effective_deviation_kcal: Decimal | None
    protein_g: Decimal | None
    carbs_g: Decimal | None
    fat_g: Decimal | None
    tracking_status: TrackingStatus
    tracking_score: int
    tracking_reasons: list[str]


class TargetSettingsResponse(BaseModel):
    targets: list[TargetResponse]




class ActivitySourceResponse(BaseModel):
    source_type: Literal["yazio_export_v1", "apple_health_xml", "health_auto_export_v2"]
class AchievementResponse(BaseModel):
    key: str | None = None
    category: str
    kind: str | None = None
    hidden: bool
    unlocked: bool
    unlocked_at: datetime | None
    progress: int | None
    target: int | None
    sort_order: int


class AchievementListResponse(BaseModel):
    achievements: list[AchievementResponse]


class AchievementReconcileResponse(AchievementListResponse):
    newly_unlocked: list[AchievementResponse]
