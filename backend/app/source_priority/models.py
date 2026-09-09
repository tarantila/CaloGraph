from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SourcePriorityPolicy(Base):
    __tablename__ = "source_priority_policies"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_source_priority_policies_id_user"),
        UniqueConstraint("user_id", "version", name="uq_source_priority_policies_user_version"),
        UniqueConstraint(
            "user_id",
            "effective_from",
            name="uq_source_priority_policies_user_effective_from",
        ),
        CheckConstraint("version >= 1", name="ck_source_priority_policies_version"),
        Index(
            "ix_source_priority_policies_user_effective_from",
            "user_id",
            "effective_from",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SourcePriorityRule(Base):
    __tablename__ = "source_priority_rules"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_source_priority_rules_id_user"),
        ForeignKeyConstraint(
            ["policy_id", "user_id"],
            ["source_priority_policies.id", "source_priority_policies.user_id"],
            name="fk_source_priority_rules_policy_user",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(data_area) > 0", name="ck_source_priority_rules_data_area"),
        CheckConstraint(
            "metric_key IS NULL OR length(metric_key) > 0",
            name="ck_source_priority_rules_metric_key",
        ),
        CheckConstraint("length(provider_key) > 0", name="ck_source_priority_rules_provider_key"),
        CheckConstraint("priority_rank >= 1", name="ck_source_priority_rules_priority_rank"),
        Index(
            "uq_source_priority_rules_wildcard_provider",
            "policy_id",
            "data_area",
            "provider_key",
            unique=True,
            postgresql_where=text("metric_key IS NULL"),
            sqlite_where=text("metric_key IS NULL"),
        ),
        Index(
            "uq_source_priority_rules_wildcard_rank",
            "policy_id",
            "data_area",
            "priority_rank",
            unique=True,
            postgresql_where=text("metric_key IS NULL"),
            sqlite_where=text("metric_key IS NULL"),
        ),
        Index(
            "uq_source_priority_rules_metric_provider",
            "policy_id",
            "data_area",
            "metric_key",
            "provider_key",
            unique=True,
            postgresql_where=text("metric_key IS NOT NULL"),
            sqlite_where=text("metric_key IS NOT NULL"),
        ),
        Index(
            "uq_source_priority_rules_metric_rank",
            "policy_id",
            "data_area",
            "metric_key",
            "priority_rank",
            unique=True,
            postgresql_where=text("metric_key IS NOT NULL"),
            sqlite_where=text("metric_key IS NOT NULL"),
        ),
        Index(
            "ix_source_priority_rules_user_scope",
            "user_id",
            "data_area",
            "metric_key",
        ),
        Index(
            "ix_source_priority_rules_policy_scope_rank",
            "policy_id",
            "data_area",
            "metric_key",
            "priority_rank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    data_area: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_key: Mapped[str | None] = mapped_column(String(128))
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
