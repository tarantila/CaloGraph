"""Initial CaloGraph schema.

Revision ID: 20260719_0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(190), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("week_starts_on", sa.Integer(), nullable=False),
        sa.Column("preferred_weight_unit", sa.String(8), nullable=False),
        sa.Column("raw_payload_retention_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    op.create_index("ix_api_tokens_token_prefix", "api_tokens", ["token_prefix"])
    op.create_table(
        "nutrition_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("calories_kcal", sa.Numeric(12, 3), nullable=False),
        sa.Column("protein_g", sa.Numeric(12, 3), nullable=False),
        sa.Column("carbs_g", sa.Numeric(12, 3)),
        sa.Column("fat_g", sa.Numeric(12, 3)),
        sa.Column("fiber_g", sa.Numeric(12, 3)),
        sa.Column("water_ml", sa.Numeric(12, 3)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_target_date_range"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "valid_from", name="uq_target_user_valid_from"),
    )
    op.create_index("ix_nutrition_targets_user_id", "nutrition_targets", ["user_id"])
    op.create_table(
        "tracking_quality_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("calories_full_ratio", sa.Numeric(5, 3), nullable=False),
        sa.Column("calories_partial_ratio", sa.Numeric(5, 3), nullable=False),
        sa.Column("median_full_ratio", sa.Numeric(5, 3), nullable=False),
        sa.Column("median_partial_ratio", sa.Numeric(5, 3), nullable=False),
        sa.Column("complete_score", sa.Integer(), nullable=False),
        sa.Column("probably_complete_score", sa.Integer(), nullable=False),
        sa.Column("probably_incomplete_score", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("client_identifier", sa.String(190)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("received", sa.Integer(), nullable=False),
        sa.Column("inserted", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("unknown_types", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_batches_user_id", "import_batches", ["user_id"])
    op.create_index("ix_import_batches_source_type", "import_batches", ["source_type"])
    op.create_index("ix_import_batches_status", "import_batches", ["status"])
    op.create_index("ix_import_batches_payload_hash", "import_batches", ["payload_hash"])
    op.create_table(
        "raw_import_payloads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("content_encoding", sa.String(32), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("compressed_payload", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id"),
    )
    op.create_index("ix_raw_import_payloads_expires_at", "raw_import_payloads", ["expires_at"])
    op.create_table(
        "import_errors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("item_index", sa.Integer()),
        sa.Column("metric_type", sa.String(128)),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("safe_detail", sa.String(500), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_errors_batch_id", "import_errors", ["batch_id"])
    op.create_table(
        "health_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("external_sample_id", sa.String(255)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(190)),
        sa.Column("source_identifier", sa.String(255), nullable=False),
        sa.Column("metric_type", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("original_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("original_unit", sa.String(64), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value >= 0", name="ck_sample_non_negative"),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "source_type", "source_identifier", "external_sample_id",
            name="uq_sample_external_identity",
        ),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_sample_user_fingerprint"),
    )
    op.create_index("ix_health_samples_user_id", "health_samples", ["user_id"])
    op.create_index("ix_health_samples_import_batch_id", "health_samples", ["import_batch_id"])
    op.create_index("ix_health_samples_metric_type", "health_samples", ["metric_type"])
    op.create_index("ix_health_samples_start_at", "health_samples", ["start_at"])
    op.create_index("ix_health_samples_local_date", "health_samples", ["local_date"])
    op.create_index(
        "ix_samples_user_local_metric", "health_samples", ["user_id", "local_date", "metric_type"]
    )
    op.create_table(
        "tracking_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "local_date", name="uq_tracking_override_day"),
    )
    op.create_index("ix_tracking_overrides_user_id", "tracking_overrides", ["user_id"])
    op.create_table(
        "rate_limit_buckets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", "action", "window_start", name="uq_rate_bucket"),
    )
    op.create_index("ix_rate_limit_buckets_window_start", "rate_limit_buckets", ["window_start"])


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
    op.drop_table("tracking_overrides")
    op.drop_table("health_samples")
    op.drop_table("import_errors")
    op.drop_table("raw_import_payloads")
    op.drop_table("import_batches")
    op.drop_table("tracking_quality_settings")
    op.drop_table("nutrition_targets")
    op.drop_table("api_tokens")
    op.drop_table("user_sessions")
    op.drop_table("users")
