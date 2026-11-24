"""Persist account-scoped Expert conversations and idempotent runs.

Revision ID: 20261114_0048
Revises: 20261114_0047
"""

import sqlalchemy as sa

from alembic import op

revision = "20261114_0048"
down_revision = "20261114_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expert_conversations",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('chat', 'analysis')", name="ck_expert_conversations_mode"),
    )
    op.create_index(
        "ix_expert_conversations_owner_created",
        "expert_conversations",
        ["user_id", "created_at", "id"],
    )
    op.create_table(
        "expert_runs",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("connection_version", sa.Integer(), nullable=False),
        sa.Column("preferences_version", sa.Integer(), nullable=False),
        sa.Column("consent_id", sa.Uuid(), nullable=False),
        sa.Column("provider_kind", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("context", sa.JSON()),
        sa.Column("answer", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("coverage_start", sa.Date()),
        sa.Column("coverage_end", sa.Date()),
        sa.ForeignKeyConstraint(
            ["user_id", "conversation_id"],
            ["expert_conversations.user_id", "expert_conversations.id"],
            ondelete="CASCADE",
            name="fk_expert_runs_conversation",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_expert_runs_state",
        ),
        sa.CheckConstraint("kind IN ('chat', 'analysis')", name="ck_expert_runs_kind"),
        sa.CheckConstraint(
            "connection_version >= 1 AND preferences_version >= 1", name="ck_expert_runs_versions"
        ),
    )
    op.create_index(
        "ix_expert_runs_conversation_created",
        "expert_runs",
        ["user_id", "conversation_id", "created_at", "id"],
    )
    op.create_index(
        "uq_expert_runs_active_owner",
        "expert_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
        sqlite_where=sa.text("state IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("expert_runs")
    op.drop_table("expert_conversations")
