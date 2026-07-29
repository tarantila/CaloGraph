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

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(16), default="de")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    week_starts_on: Mapped[int] = mapped_column(Integer, default=0)
    preferred_weight_unit: Mapped[str] = mapped_column(String(8), default="kg")
    raw_payload_retention_days: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    targets: Mapped[list[NutritionTarget]] = relationship(back_populates="user")
    yazio_connection: Mapped[YazioConnection | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class YazioConnection(Base):
    __tablename__ = "yazio_connections"
    __table_args__ = (
        CheckConstraint(
            "sync_interval_minutes BETWEEN 60 AND 10080",
            name="ck_yazio_sync_interval",
        ),
        CheckConstraint("sync_days BETWEEN 1 AND 366", name="ck_yazio_sync_days"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    encrypted_email: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_password: Mapped[bytes] = mapped_column(LargeBinary)
    account_hash: Mapped[str] = mapped_column(String(64))
    source_identifier: Mapped[str] = mapped_column(String(255))
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    sync_days: Mapped[int] = mapped_column(Integer, default=7)
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

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
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
            "maintenance_kcal IS NULL OR maintenance_kcal >= calories_kcal",
            name="ck_target_maintenance_at_least_budget",
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


JsonDict = dict[str, Any]
