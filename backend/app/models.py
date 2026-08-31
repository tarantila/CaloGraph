import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.activity import ACTIVITY_MODES, ACTIVITY_SOURCE_TYPES
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username"),
        CheckConstraint(
            "(is_active AND deactivated_at IS NULL) "
            "OR (NOT is_active AND deactivated_at IS NOT NULL)",
            name="ck_users_active_deactivation_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(190), index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(16), default="de")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    week_starts_on: Mapped[int] = mapped_column(Integer, default=0)
    preferred_weight_unit: Mapped[str] = mapped_column(String(8), default="kg")
    raw_payload_retention_days: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    targets: Mapped[list[NutritionTarget]] = relationship(back_populates="user")
    yazio_connection: Mapped[YazioConnection | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "gender IS NULL OR gender IN "
            "('female', 'male', 'non_binary', 'other', 'prefer_not_to_say')",
            name="ck_user_profiles_gender",
        ),
        CheckConstraint(
            "height_cm IS NULL OR (height_cm > 0 AND height_cm <= 300)",
            name="ck_user_profiles_height_cm",
        ),
        CheckConstraint(
            "diet_type IS NULL OR diet_type IN "
            "('no_special_diet', 'vegetarian', 'vegan', 'pescetarian', 'other', "
            "'prefer_not_to_say')",
            name="ck_user_profiles_diet_type",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str | None] = mapped_column(String(120))
    gender: Mapped[str | None] = mapped_column(String(32))
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    diet_type: Mapped[str | None] = mapped_column(String(32))
    health_notes: Mapped[str | None] = mapped_column(String(4000))
    intolerances: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class YazioConnection(Base):
    __tablename__ = "yazio_connections"
    __table_args__ = (
        UniqueConstraint("user_id"),
        CheckConstraint(
            "sync_interval_minutes IS NULL OR sync_interval_minutes BETWEEN 60 AND 10080",
            name="ck_yazio_sync_interval",
        ),
        CheckConstraint(
            "sync_days IS NULL OR sync_days BETWEEN 1 AND 366",
            name="ck_yazio_sync_days",
        ),
        CheckConstraint(
            "historical_sync_state IN ('idle', 'pending', 'running', 'completed', 'failed')",
            name="ck_yazio_historical_sync_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    encrypted_email: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_password: Mapped[bytes] = mapped_column(LargeBinary)
    source_identifier: Mapped[str] = mapped_column(String(255))
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Null means inherit the deployment-wide settings values.
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    sync_days: Mapped[int | None] = mapped_column(Integer)
    historical_sync_state: Mapped[str] = mapped_column(String(16), default="idle")
    historical_sync_start_date: Mapped[date | None] = mapped_column(Date)
    historical_sync_end_date: Mapped[date | None] = mapped_column(Date)
    historical_sync_cursor_date: Mapped[date | None] = mapped_column(Date)
    historical_sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    historical_sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    historical_sync_last_error: Mapped[str | None] = mapped_column(String(500))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_micronutrient_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="yazio_connection")


class UserInvitation(Base):
    __tablename__ = "user_invitations"
    __table_args__ = (UniqueConstraint("token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountRecoveryToken(Base):
    __tablename__ = "account_recovery_tokens"
    __table_args__ = (
        Index(
            "ix_account_recovery_tokens_user_open",
            "user_id",
            "used_at",
            "revoked_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



class UserUsageDay(Base):
    __tablename__ = "user_usage_days"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_date", name="uq_user_usage_day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    activity_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class UserTotpCredential(Base):
    __tablename__ = "user_totp_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebAuthnUserHandle(Base):
    __tablename__ = "webauthn_user_handles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_handle: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PasskeyCredential(Base):
    __tablename__ = "passkey_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    label: Mapped[str] = mapped_column(String(100))
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    transports: Mapped[list[str]] = mapped_column(JSON, default=list)
    device_type: Mapped[str] = mapped_column(String(32))
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebAuthnChallenge(Base):
    __tablename__ = "webauthn_challenges"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    challenge: Mapped[bytes] = mapped_column(LargeBinary)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    token_prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["import"])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NutritionTarget(Base):
    __tablename__ = "nutrition_targets"
    __table_args__ = (
        UniqueConstraint("user_id", "valid_from", name="uq_target_user_valid_from"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_target_date_range"),
        CheckConstraint(
            "maintenance_kcal IS NULL OR "
            "(maintenance_kcal > 0 AND maintenance_kcal < 'Infinity')",
            name="ck_target_maintenance_positive_finite",
        ),
        CheckConstraint(
            "("
            "target_weight_min_kg IS NULL AND target_weight_max_kg IS NULL"
            ") OR ("
            "target_weight_min_kg IS NOT NULL AND target_weight_max_kg IS NOT NULL "
            "AND target_weight_min_kg > 0 "
            "AND target_weight_min_kg <= target_weight_max_kg "
            "AND target_weight_max_kg <= 1000"
            ")",
            name="ck_target_weight_range",
        ),
        CheckConstraint(
            f"activity_mode IN ({', '.join(repr(mode) for mode in sorted(ACTIVITY_MODES))})",
            name="ck_target_activity_mode",
        ),
        CheckConstraint(
            "("
            "activity_mode = 'off' AND activity_source_type IS NULL"
            ") OR ("
            "activity_mode = 'full' AND activity_source_type IS NOT NULL "
            "AND activity_source_type IN ("
            f"{', '.join(repr(source) for source in sorted(ACTIVITY_SOURCE_TYPES))}"
            "))",
            name="ck_target_activity_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    calories_kcal: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    maintenance_kcal: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    target_weight_min_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    target_weight_max_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    activity_mode: Mapped[str] = mapped_column(String(16), default="off", server_default="off")
    activity_source_type: Mapped[str | None] = mapped_column(String(64))
    protein_g: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    carbs_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    water_ml: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="targets")


class TrackingQualitySettings(Base):
    __tablename__ = "tracking_quality_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    calories_full_ratio: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("0.60"))
    calories_partial_ratio: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("0.35"))
    median_full_ratio: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("0.50"))
    median_partial_ratio: Mapped[Decimal] = mapped_column(Numeric(5, 3), default=Decimal("0.30"))
    complete_score: Mapped[int] = mapped_column(Integer, default=7)
    probably_complete_score: Mapped[int] = mapped_column(Integer, default=5)
    probably_incomplete_score: Mapped[int] = mapped_column(Integer, default=3)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    client_identifier: Mapped[str | None] = mapped_column(String(190))
    status: Mapped[str] = mapped_column(String(32), default="processing", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    unknown_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    payload_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)


class RawImportPayload(Base):
    __tablename__ = "raw_import_payloads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), unique=True
    )
    content_encoding: Mapped[str] = mapped_column(String(32), default="zstd")
    content_type: Mapped[str] = mapped_column(String(100))
    compressed_payload: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ImportError(Base):
    __tablename__ = "import_errors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), index=True
    )
    item_index: Mapped[int | None] = mapped_column(Integer)
    metric_type: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str] = mapped_column(String(64))
    safe_detail: Mapped[str] = mapped_column(String(500))


class HealthSample(Base):
    __tablename__ = "health_samples"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_sample_user_fingerprint"),
        UniqueConstraint(
            "user_id",
            "source_type",
            "source_identifier",
            "external_sample_id",
            name="uq_sample_external_identity",
        ),
        CheckConstraint("value >= 0", name="ck_sample_non_negative"),
        Index("ix_samples_user_local_metric", "user_id", "local_date", "metric_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    import_batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id"), index=True)
    external_sample_id: Mapped[str | None] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str | None] = mapped_column(String(190))
    source_identifier: Mapped[str] = mapped_column(String(255), default="unknown")
    metric_type: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    unit: Mapped[str] = mapped_column(String(32))
    original_value: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    original_unit: Mapped[str] = mapped_column(String(64))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date] = mapped_column(Date, index=True)
    timezone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserAchievement(Base):
    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "achievement_key",
            name="uq_user_achievement_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    achievement_key: Mapped[str] = mapped_column(String(64))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackingOverride(Base):
    __tablename__ = "tracking_overrides"
    __table_args__ = (UniqueConstraint("user_id", "local_date", name="uq_tracking_override_day"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    local_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint("key_hash", "action", "window_start", name="uq_rate_bucket"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)

class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_events_occurred_at", "occurred_at"),
        Index("ix_security_audit_events_event_outcome", "event", "outcome"),
        Index("ix_security_audit_events_actor_user_id", "actor_user_id"),
        Index("ix_security_audit_events_target_user_id", "target_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    auth_method: Mapped[str | None] = mapped_column(String(32))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_security_audit_events_actor_user_id_users", ondelete="SET NULL")
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_security_audit_events_target_user_id_users", ondelete="SET NULL")
    )
    actor_ref: Mapped[str | None] = mapped_column(String(16))
    target_ref: Mapped[str | None] = mapped_column(String(16))
    username_snapshot: Mapped[str | None] = mapped_column(String(190))
    request_id: Mapped[str | None] = mapped_column(String(32))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    client_ref: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(64))


JsonDict = dict[str, Any]
