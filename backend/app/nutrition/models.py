from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.nutrition.enums import (
    CONSUMPTION_EVENT_KIND_VALUES,
    COVERAGE_VALUES,
    LINEAGE_VALUES,
    OBSERVATION_KIND_VALUES,
    OBSERVATION_ROLE_VALUES,
    PRESENCE_VALUES,
    PROJECTION_GRANULARITY_VALUES,
    PROJECTION_LINEAGE_ROLE_VALUES,
    PROJECTION_STATUS_VALUES,
    RESOLUTION_VALUES,
    RUN_STATUS_VALUES,
    SERVING_SCOPE_VALUES,
)

_DECIMAL_TYPE = Numeric(24, 12)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _in_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


class NutritionIngestionRun(Base):
    __tablename__ = "nutrition_ingestion_runs"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_ingestion_runs_id_user"),
        _in_check("status", RUN_STATUS_VALUES, "ck_nutrition_runs_status"),
        CheckConstraint("source_instance_id IS NOT NULL", name="ck_nutrition_runs_source_instance"),
        _in_check("coverage_state", COVERAGE_VALUES, "ck_nutrition_runs_coverage"),
        Index("ix_nutrition_ingestion_runs_user_provider", "user_id", "provider_key"),
        Index("ix_nutrition_ingestion_runs_user_dates", "user_id", "requested_start_date", "requested_end_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_key: Mapped[str] = mapped_column(String(64))
    source_instance_id: Mapped[uuid.UUID] = mapped_column()
    connector_variant: Mapped[str | None] = mapped_column(String(64))
    requested_start_date: Mapped[date | None] = mapped_column(Date)
    requested_end_date: Mapped[date | None] = mapped_column(Date)
    covered_start_date: Mapped[date | None] = mapped_column(Date)
    covered_end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    coverage_state: Mapped[str] = mapped_column(String(16), default="unknown")
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionSourceObservation(Base):
    __tablename__ = "nutrition_source_observations"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_source_observations_id_user"),
        ForeignKeyConstraint(
            ["ingestion_run_id", "user_id"],
            ["nutrition_ingestion_runs.id", "nutrition_ingestion_runs.user_id"],
            name="fk_nutrition_observations_run_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("source_revision >= 1", name="ck_nutrition_observations_revision"),
        CheckConstraint(
            "source_record_id IS NULL OR length(source_record_id) > 0",
            name="ck_nutrition_observations_source_record",
        ),
        CheckConstraint(
            "length(observation_fingerprint) = 64",
            name="ck_nutrition_observations_fingerprint_length",
        ),
        _in_check("observation_kind", OBSERVATION_KIND_VALUES, "ck_nutrition_observations_kind"),
        _in_check("presence_state", PRESENCE_VALUES, "ck_nutrition_observations_presence"),
        _in_check("coverage_state", COVERAGE_VALUES, "ck_nutrition_observations_coverage"),
        _in_check("resolution_state", RESOLUTION_VALUES, "ck_nutrition_observations_resolution"),
        _in_check("lineage_state", LINEAGE_VALUES, "ck_nutrition_observations_lineage"),
        Index("ix_nutrition_source_observations_user_local_date", "user_id", "local_date"),
        Index(
            "ix_nutrition_source_observations_user_provider_kind_date",
            "user_id",
            "provider_key",
            "observation_kind",
            "local_date",
        ),
        Index("ix_nutrition_source_observations_user_fingerprint", "user_id", "observation_fingerprint"),
        Index("ix_nutrition_source_observations_user_run", "user_id", "ingestion_run_id"),
        Index(
            "uq_nutrition_observations_external_identity",
            "user_id",
            "source_instance_id",
            "provider_key",
            "source_namespace",
            "source_record_id",
            "source_revision",
            unique=True,
            postgresql_where=text("source_record_id IS NOT NULL"),
            sqlite_where=text("source_record_id IS NOT NULL"),
        ),
        Index(
            "uq_nutrition_observations_fingerprint_identity",
            "user_id",
            "source_instance_id",
            "provider_key",
            "source_namespace",
            "observation_fingerprint",
            unique=True,
            postgresql_where=text("source_record_id IS NULL"),
            sqlite_where=text("source_record_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column()
    provider_key: Mapped[str] = mapped_column(String(64))
    source_instance_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connector_variant: Mapped[str | None] = mapped_column(String(64))
    observation_kind: Mapped[str] = mapped_column(String(32))
    source_namespace: Mapped[str] = mapped_column(String(128))
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_civil_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    provider_timezone: Mapped[str | None] = mapped_column(String(64))
    canonical_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date | None] = mapped_column(Date)
    timezone_source: Mapped[str | None] = mapped_column(String(32))
    time_confidence: Mapped[str | None] = mapped_column(String(32))
    presence_state: Mapped[str] = mapped_column(String(16))
    coverage_state: Mapped[str] = mapped_column(String(16))
    resolution_state: Mapped[str] = mapped_column(String(32))
    lineage_state: Mapped[str] = mapped_column(String(16), default="unknown")
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionExternalIdentity(Base):
    __tablename__ = "nutrition_external_identities"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_external_identities_id_user"),
        UniqueConstraint(
            "user_id",
            "provider_key",
            "namespace",
            "identity_value",
            name="uq_nutrition_external_identity_value",
        ),
        Index("ix_nutrition_external_identities_user_namespace", "user_id", "provider_key", "namespace"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_key: Mapped[str] = mapped_column(String(64))
    namespace: Mapped[str] = mapped_column(String(128))
    identity_value: Mapped[str] = mapped_column(String(512))
    identity_kind: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionConsumptionEvent(Base):
    __tablename__ = "nutrition_consumption_events"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_events_id_user"),
        UniqueConstraint(
            "id",
            "user_id",
            "provider_key",
            "source_instance_id",
            "logical_event_key",
            "revision",
            name="uq_nutrition_events_revision_target",
        ),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_events_source_observation_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["food_snapshot_id", "user_id"],
            ["nutrition_food_snapshots.id", "nutrition_food_snapshots.user_id"],
            name="fk_nutrition_events_food_snapshot_user",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            [
                "supersedes_event_id",
                "user_id",
                "provider_key",
                "source_instance_id",
                "logical_event_key",
                "supersedes_revision",
            ],
            [
                "nutrition_consumption_events.id",
                "nutrition_consumption_events.user_id",
                "nutrition_consumption_events.provider_key",
                "nutrition_consumption_events.source_instance_id",
                "nutrition_consumption_events.logical_event_key",
                "nutrition_consumption_events.revision",
            ],
            name="fk_nutrition_events_supersedes",
        ),
        CheckConstraint("revision >= 1", name="ck_nutrition_events_revision"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_nutrition_events_amount"),
        CheckConstraint(
            "(supersedes_event_id IS NULL AND supersedes_revision IS NULL) "
            "OR (supersedes_event_id IS NOT NULL AND supersedes_revision IS NOT NULL "
            "AND logical_event_key IS NOT NULL)",
            name="ck_nutrition_events_supersedes_pair",
        ),
        CheckConstraint(
            "supersedes_event_id IS NULL OR revision = supersedes_revision + 1",
            name="ck_nutrition_events_revision_sequence",
        ),
        _in_check("event_kind", CONSUMPTION_EVENT_KIND_VALUES, "ck_nutrition_events_kind"),
        _in_check("presence_state", PRESENCE_VALUES, "ck_nutrition_events_presence"),
        _in_check("coverage_state", COVERAGE_VALUES, "ck_nutrition_events_coverage"),
        _in_check("resolution_state", RESOLUTION_VALUES, "ck_nutrition_events_resolution"),
        _in_check("lineage_state", LINEAGE_VALUES, "ck_nutrition_events_lineage"),
        Index("ix_nutrition_events_user_local_date", "user_id", "local_date"),
        Index("ix_nutrition_events_user_source", "user_id", "source_observation_id"),
        Index(
            "uq_nutrition_events_logical_revision",
            "user_id",
            "source_instance_id",
            "provider_key",
            "logical_event_key",
            "revision",
            unique=True,
            postgresql_where=text("logical_event_key IS NOT NULL"),
            sqlite_where=text("logical_event_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_observation_id: Mapped[uuid.UUID] = mapped_column()
    provider_key: Mapped[str] = mapped_column(String(64))
    source_instance_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32))
    logical_event_key: Mapped[str | None] = mapped_column(String(255))
    supersedes_event_id: Mapped[uuid.UUID | None] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_revision: Mapped[int | None] = mapped_column(Integer)
    food_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    provider_civil_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    provider_timezone: Mapped[str | None] = mapped_column(String(64))
    canonical_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_date: Mapped[date | None] = mapped_column(Date)
    daytime: Mapped[str | None] = mapped_column(String(32))
    amount: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    amount_unit: Mapped[str | None] = mapped_column(String(32))
    presence_state: Mapped[str] = mapped_column(String(16))
    coverage_state: Mapped[str] = mapped_column(String(16))
    resolution_state: Mapped[str] = mapped_column(String(32))
    lineage_state: Mapped[str] = mapped_column(String(16))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionFoodProfile(Base):
    __tablename__ = "nutrition_food_profiles"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_food_profiles_id_user"),
        ForeignKeyConstraint(
            ["current_snapshot_id", "user_id", "id"],
            [
                "nutrition_food_snapshots.id",
                "nutrition_food_snapshots.user_id",
                "nutrition_food_snapshots.food_profile_id",
            ],
            name="fk_nutrition_profiles_current_snapshot_scope",
            use_alter=True,
        ),
        Index("ix_nutrition_food_profiles_user_provider", "user_id", "provider_key"),
        Index("ix_nutrition_food_profiles_user_source_instance", "user_id", "source_instance_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_key: Mapped[str] = mapped_column(String(64))
    source_instance_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    current_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    profile_status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class NutritionFoodSnapshot(Base):
    __tablename__ = "nutrition_food_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_food_snapshots_id_user"),
        UniqueConstraint("id", "user_id", "food_profile_id", name="uq_nutrition_food_snapshots_scope"),
        UniqueConstraint(
            "user_id",
            "food_profile_id",
            "content_hash",
            name="uq_nutrition_food_snapshots_content",
        ),
        ForeignKeyConstraint(
            ["food_profile_id", "user_id"],
            ["nutrition_food_profiles.id", "nutrition_food_profiles.user_id"],
            name="fk_nutrition_food_snapshots_profile_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_food_snapshots_source_user",
            ondelete="CASCADE",
        ),
        Index("ix_nutrition_food_snapshots_user_profile", "user_id", "food_profile_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    food_profile_id: Mapped[uuid.UUID] = mapped_column()
    source_observation_id: Mapped[uuid.UUID] = mapped_column()
    provider_revision: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(512))
    producer: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(255))
    base_unit: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(32))
    is_verified: Mapped[bool | None] = mapped_column(Boolean)
    is_private: Mapped[bool | None] = mapped_column(Boolean)
    is_deleted: Mapped[bool | None] = mapped_column(Boolean)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionExternalIdentityLink(Base):
    __tablename__ = "nutrition_external_identity_links"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_identity_links_id_user"),
        UniqueConstraint(
            "id",
            "user_id",
            "external_identity_id",
            "link_role",
            "link_revision",
            name="uq_nutrition_identity_links_fk_target",
        ),
        UniqueConstraint(
            "user_id",
            "external_identity_id",
            "link_role",
            "link_revision",
            name="uq_nutrition_identity_links_revision",
        ),
        ForeignKeyConstraint(
            ["external_identity_id", "user_id"],
            ["nutrition_external_identities.id", "nutrition_external_identities.user_id"],
            name="fk_nutrition_identity_links_identity_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["consumption_event_id", "user_id"],
            ["nutrition_consumption_events.id", "nutrition_consumption_events.user_id"],
            name="fk_nutrition_identity_links_event_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["food_profile_id", "user_id"],
            ["nutrition_food_profiles.id", "nutrition_food_profiles.user_id"],
            name="fk_nutrition_identity_links_profile_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_identity_links_observation_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "supersedes_link_id",
                "user_id",
                "external_identity_id",
                "link_role",
                "supersedes_link_revision",
            ],
            [
                "nutrition_external_identity_links.id",
                "nutrition_external_identity_links.user_id",
                "nutrition_external_identity_links.external_identity_id",
                "nutrition_external_identity_links.link_role",
                "nutrition_external_identity_links.link_revision",
            ],
            name="fk_nutrition_identity_links_supersedes",
        ),
        CheckConstraint("link_revision >= 1", name="ck_nutrition_identity_links_revision"),
        CheckConstraint(
            "(supersedes_link_id IS NULL AND supersedes_link_revision IS NULL) "
            "OR (supersedes_link_id IS NOT NULL AND supersedes_link_revision IS NOT NULL)",
            name="ck_nutrition_identity_links_supersedes_pair",
        ),
        CheckConstraint(
            "supersedes_link_id IS NULL OR link_revision = supersedes_link_revision + 1",
            name="ck_nutrition_identity_links_revision_sequence",
        ),
        CheckConstraint(
            "(CASE WHEN consumption_event_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN food_profile_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN source_observation_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_nutrition_identity_links_one_target",
        ),
        Index(
            "ix_nutrition_identity_links_user_identity_role_revision",
            "user_id",
            "external_identity_id",
            "link_role",
            "link_revision",
        ),
        Index("ix_nutrition_identity_links_user_event", "user_id", "consumption_event_id"),
        Index("ix_nutrition_identity_links_user_profile", "user_id", "food_profile_id"),
        Index("ix_nutrition_identity_links_user_observation", "user_id", "source_observation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_identity_id: Mapped[uuid.UUID] = mapped_column()
    link_role: Mapped[str] = mapped_column(String(64))
    consumption_event_id: Mapped[uuid.UUID | None] = mapped_column()
    food_profile_id: Mapped[uuid.UUID | None] = mapped_column()
    source_observation_id: Mapped[uuid.UUID | None] = mapped_column()
    link_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_link_id: Mapped[uuid.UUID | None] = mapped_column()
    supersedes_link_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionServingObservation(Base):
    __tablename__ = "nutrition_serving_observations"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_servings_id_user"),
        UniqueConstraint(
            "id",
            "user_id",
            "serving_scope",
            name="uq_nutrition_servings_scope_target",
        ),
        ForeignKeyConstraint(
            ["consumption_event_id", "user_id"],
            ["nutrition_consumption_events.id", "nutrition_consumption_events.user_id"],
            name="fk_nutrition_servings_event_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["food_snapshot_id", "user_id"],
            ["nutrition_food_snapshots.id", "nutrition_food_snapshots.user_id"],
            name="fk_nutrition_servings_snapshot_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["profile_serving_id", "user_id", "profile_serving_scope"],
            [
                "nutrition_serving_observations.id",
                "nutrition_serving_observations.user_id",
                "nutrition_serving_observations.serving_scope",
            ],
            name="fk_nutrition_servings_profile_parent_user",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "(profile_serving_id IS NULL AND profile_serving_scope IS NULL) "
            "OR (profile_serving_id IS NOT NULL AND profile_serving_scope = 'profile')",
            name="ck_nutrition_servings_profile_parent",
        ),
        CheckConstraint(
            "(CASE WHEN consumption_event_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN food_snapshot_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_nutrition_servings_one_scope_parent",
        ),
        CheckConstraint(
            "(serving_scope = 'profile' AND food_snapshot_id IS NOT NULL "
            "AND amount IS NOT NULL AND unit IS NOT NULL AND consumption_event_id IS NULL) "
            "OR (serving_scope = 'event' AND consumption_event_id IS NOT NULL "
            "AND food_snapshot_id IS NULL)",
            name="ck_nutrition_servings_scope_fields",
        ),
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_nutrition_servings_amount"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_nutrition_servings_quantity"),
        _in_check("serving_scope", SERVING_SCOPE_VALUES, "ck_nutrition_servings_scope"),
        Index("ix_nutrition_servings_user_event", "user_id", "consumption_event_id"),
        Index("ix_nutrition_servings_user_snapshot", "user_id", "food_snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    consumption_event_id: Mapped[uuid.UUID | None] = mapped_column()
    food_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    serving_scope: Mapped[str] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(128))
    quantity: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    amount: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    unit: Mapped[str | None] = mapped_column(String(32))
    profile_serving_id: Mapped[uuid.UUID | None] = mapped_column()
    profile_serving_scope: Mapped[str | None] = mapped_column(String(16))
    provider_field_path: Mapped[str | None] = mapped_column(String(255))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionFieldObservation(Base):
    __tablename__ = "nutrition_field_observations"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_fields_id_user"),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_fields_source_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["derived_from_field_observation_id", "user_id"],
            ["nutrition_field_observations.id", "nutrition_field_observations.user_id"],
            name="fk_nutrition_fields_derived_parent_user",
            ondelete="SET NULL",
        ),
        _in_check("observation_role", OBSERVATION_ROLE_VALUES, "ck_nutrition_fields_role"),
        _in_check("presence_state", PRESENCE_VALUES, "ck_nutrition_fields_presence"),
        _in_check("coverage_state", COVERAGE_VALUES, "ck_nutrition_fields_coverage"),
        _in_check("resolution_state", RESOLUTION_VALUES, "ck_nutrition_fields_resolution"),
        _in_check("lineage_state", LINEAGE_VALUES, "ck_nutrition_fields_lineage"),
        Index("ix_nutrition_fields_user_source", "user_id", "source_observation_id"),
        Index("ix_nutrition_fields_user_metric", "user_id", "metric_key"),
        Index(
            "uq_nutrition_fields_source_path_role",
            "user_id",
            "source_observation_id",
            "provider_field_path",
            "observation_role",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_observation_id: Mapped[uuid.UUID] = mapped_column()
    provider_field_path: Mapped[str] = mapped_column(String(255))
    provider_raw_value_decimal: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    provider_raw_value_text: Mapped[str | None] = mapped_column(Text)
    provider_raw_unit: Mapped[str | None] = mapped_column(String(64))
    metric_key: Mapped[str | None] = mapped_column(String(128))
    canonical_value: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    canonical_unit: Mapped[str | None] = mapped_column(String(64))
    observation_role: Mapped[str] = mapped_column(String(16))
    derived_from_field_observation_id: Mapped[uuid.UUID | None] = mapped_column()
    presence_state: Mapped[str] = mapped_column(String(16))
    coverage_state: Mapped[str] = mapped_column(String(16))
    resolution_state: Mapped[str] = mapped_column(String(32))
    lineage_state: Mapped[str] = mapped_column(String(16), default="unknown")
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionSourceTombstone(Base):
    __tablename__ = "nutrition_source_tombstones"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_tombstones_id_user"),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_tombstones_source_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["external_identity_id", "user_id"],
            ["nutrition_external_identities.id", "nutrition_external_identities.user_id"],
            name="fk_nutrition_tombstones_identity_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source_observation_id IS NOT NULL OR external_identity_id IS NOT NULL "
            "OR source_record_id IS NOT NULL",
            name="ck_nutrition_tombstones_context",
        ),
        CheckConstraint(
            "source_record_id IS NULL OR length(source_record_id) > 0",
            name="ck_nutrition_tombstones_source_record",
        ),
        Index("ix_nutrition_tombstones_user_provider", "user_id", "provider_key"),
        Index(
            "ix_nutrition_tombstones_user_source",
            "user_id",
            "source_instance_id",
            "source_record_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_key: Mapped[str] = mapped_column(String(64))
    source_instance_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(128))
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    external_identity_id: Mapped[uuid.UUID | None] = mapped_column()
    source_observation_id: Mapped[uuid.UUID | None] = mapped_column()
    tombstone_kind: Mapped[str] = mapped_column(String(64))
    provider_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionProvenance(Base):
    __tablename__ = "nutrition_provenance"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_provenance_id_user"),
        Index(
            "uq_nutrition_provenance_event_identity",
            "user_id",
            "source_observation_id",
            "role",
            "consumption_event_id",
            unique=True,
            postgresql_where=text("consumption_event_id IS NOT NULL"),
            sqlite_where=text("consumption_event_id IS NOT NULL"),
        ),
        Index(
            "uq_nutrition_provenance_snapshot_identity",
            "user_id",
            "source_observation_id",
            "role",
            "food_snapshot_id",
            unique=True,
            postgresql_where=text("food_snapshot_id IS NOT NULL"),
            sqlite_where=text("food_snapshot_id IS NOT NULL"),
        ),
        Index(
            "uq_nutrition_provenance_serving_identity",
            "user_id",
            "source_observation_id",
            "role",
            "serving_observation_id",
            unique=True,
            postgresql_where=text("serving_observation_id IS NOT NULL"),
            sqlite_where=text("serving_observation_id IS NOT NULL"),
        ),
        Index(
            "uq_nutrition_provenance_field_identity",
            "user_id",
            "source_observation_id",
            "role",
            "field_observation_id",
            unique=True,
            postgresql_where=text("field_observation_id IS NOT NULL"),
            sqlite_where=text("field_observation_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_provenance_source_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["consumption_event_id", "user_id"],
            ["nutrition_consumption_events.id", "nutrition_consumption_events.user_id"],
            name="fk_nutrition_provenance_event_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["food_snapshot_id", "user_id"],
            ["nutrition_food_snapshots.id", "nutrition_food_snapshots.user_id"],
            name="fk_nutrition_provenance_snapshot_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["serving_observation_id", "user_id"],
            ["nutrition_serving_observations.id", "nutrition_serving_observations.user_id"],
            name="fk_nutrition_provenance_serving_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["field_observation_id", "user_id"],
            ["nutrition_field_observations.id", "nutrition_field_observations.user_id"],
            name="fk_nutrition_provenance_field_user",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(CASE WHEN consumption_event_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN food_snapshot_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN serving_observation_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN field_observation_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_nutrition_provenance_one_target",
        ),
        Index("ix_nutrition_provenance_user_source", "user_id", "source_observation_id"),
        Index("ix_nutrition_provenance_user_event", "user_id", "consumption_event_id"),
        Index("ix_nutrition_provenance_user_snapshot", "user_id", "food_snapshot_id"),
        Index("ix_nutrition_provenance_user_serving", "user_id", "serving_observation_id"),
        Index("ix_nutrition_provenance_user_field", "user_id", "field_observation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_observation_id: Mapped[uuid.UUID] = mapped_column()
    consumption_event_id: Mapped[uuid.UUID | None] = mapped_column()
    food_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    serving_observation_id: Mapped[uuid.UUID | None] = mapped_column()
    field_observation_id: Mapped[uuid.UUID | None] = mapped_column()
    role: Mapped[str] = mapped_column(String(32))
    lineage_state: Mapped[str] = mapped_column(String(16))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NutritionDailyProjection(Base):
    __tablename__ = "nutrition_daily_projections"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_projections_id_user"),
        UniqueConstraint("id", "user_id", "local_date", name="uq_nutrition_projections_id_scope"),
        UniqueConstraint(
            "user_id",
            "local_date",
            "projection_version",
            name="uq_nutrition_projections_user_date_version",
        ),
        ForeignKeyConstraint(
            ["priority_policy_id", "user_id"],
            ["source_priority_policies.id", "source_priority_policies.user_id"],
            name="fk_nutrition_projections_policy_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint("projection_version >= 1", name="ck_nutrition_projections_version"),
        CheckConstraint(
            "length(projection_algorithm_version) > 0",
            name="ck_nutrition_projections_algorithm_version",
        ),
        CheckConstraint("length(input_watermark) > 0", name="ck_nutrition_projections_input_watermark"),
        _in_check("projection_status", PROJECTION_STATUS_VALUES, "ck_nutrition_projections_status"),
        Index("ix_nutrition_projections_user_date", "user_id", "local_date"),
        Index("ix_nutrition_projections_user_policy", "user_id", "priority_policy_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    priority_policy_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    input_watermark: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    projection_status: Mapped[str] = mapped_column(String(16), nullable=False)


class NutritionDailyProjectionFact(Base):
    __tablename__ = "nutrition_daily_projection_facts"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_projection_facts_id_user"),
        UniqueConstraint("projection_id", "metric_key", name="uq_nutrition_projection_facts_metric"),
        ForeignKeyConstraint(
            ["projection_id", "user_id"],
            ["nutrition_daily_projections.id", "nutrition_daily_projections.user_id"],
            name="fk_nutrition_projection_facts_projection_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(metric_key) > 0", name="ck_nutrition_projection_facts_metric"),
        CheckConstraint(
            "selected_provider_key IS NULL OR length(selected_provider_key) > 0",
            name="ck_nutrition_projection_facts_provider",
        ),
        _in_check(
            "selected_granularity",
            PROJECTION_GRANULARITY_VALUES,
            "ck_nutrition_projection_facts_granularity",
        ),
        _in_check("presence_state", PRESENCE_VALUES, "ck_nutrition_projection_facts_presence"),
        _in_check("coverage_state", COVERAGE_VALUES, "ck_nutrition_projection_facts_coverage"),
        _in_check("resolution_state", RESOLUTION_VALUES, "ck_nutrition_projection_facts_resolution"),
        _in_check("lineage_state", LINEAGE_VALUES, "ck_nutrition_projection_facts_lineage"),
        Index("ix_nutrition_projection_facts_user_metric", "user_id", "metric_key"),
        Index("ix_nutrition_projection_facts_user_projection", "user_id", "projection_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    projection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    unit: Mapped[str | None] = mapped_column(String(64))
    selected_provider_key: Mapped[str | None] = mapped_column(String(64))
    selected_granularity: Mapped[str | None] = mapped_column(String(32))
    presence_state: Mapped[str] = mapped_column(String(16), nullable=False)
    coverage_state: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution_state: Mapped[str] = mapped_column(String(32), nullable=False)
    lineage_state: Mapped[str] = mapped_column(String(16), nullable=False)
    diagnostic_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class NutritionDailyProjectionLineage(Base):
    __tablename__ = "nutrition_daily_projection_lineage"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_nutrition_projection_lineage_id_user"),
        UniqueConstraint(
            "projection_fact_id",
            "source_observation_id",
            "role",
            name="uq_nutrition_projection_lineage_relation",
        ),
        ForeignKeyConstraint(
            ["projection_fact_id", "user_id"],
            ["nutrition_daily_projection_facts.id", "nutrition_daily_projection_facts.user_id"],
            name="fk_nutrition_projection_lineage_fact_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_observation_id", "user_id"],
            ["nutrition_source_observations.id", "nutrition_source_observations.user_id"],
            name="fk_nutrition_projection_lineage_source_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(provider_key) > 0", name="ck_nutrition_projection_lineage_provider"),
        _in_check("role", PROJECTION_LINEAGE_ROLE_VALUES, "ck_nutrition_projection_lineage_role"),
        _in_check(
            "granularity",
            PROJECTION_GRANULARITY_VALUES,
            "ck_nutrition_projection_lineage_granularity",
        ),
        _in_check("presence_state", PRESENCE_VALUES, "ck_nutrition_projection_lineage_presence"),
        _in_check("coverage_state", COVERAGE_VALUES, "ck_nutrition_projection_lineage_coverage"),
        Index("ix_nutrition_projection_lineage_user_fact", "user_id", "projection_fact_id"),
        Index("ix_nutrition_projection_lineage_user_source", "user_id", "source_observation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    projection_fact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_observation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    granularity: Mapped[str | None] = mapped_column(String(32))
    contribution_value: Mapped[Decimal | None] = mapped_column(_DECIMAL_TYPE)
    presence_state: Mapped[str | None] = mapped_column(String(16))
    coverage_state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    lineage_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class NutritionProjectionHead(Base):
    __tablename__ = "nutrition_projection_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_projection_id", "user_id", "local_date"],
            [
                "nutrition_daily_projections.id",
                "nutrition_daily_projections.user_id",
                "nutrition_daily_projections.local_date",
            ],
            name="fk_nutrition_projection_heads_projection_scope",
            ondelete="RESTRICT",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    current_projection_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
