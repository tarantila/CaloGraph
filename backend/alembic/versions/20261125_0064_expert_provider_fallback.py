"""Persist opt-in Expert route snapshots and one-time provider attempts.

Revision ID: 20261125_0064
Revises: 20261125_0063
"""

import sqlalchemy as sa

from alembic import op

revision = "20261125_0064"
down_revision = "20261125_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expert_runs", sa.Column("routing_version", sa.Integer(), nullable=True))
    op.add_column("expert_runs", sa.Column("routing_snapshot", sa.JSON(), nullable=True))
    op.create_table(
        "expert_run_provider_attempts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("connection_version", sa.Integer(), nullable=False),
        sa.Column("provider_kind", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("consent_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "run_id", "position", name="pk_expert_run_provider_attempts"),
        sa.ForeignKeyConstraint(
            ["user_id", "run_id"], ["expert_runs.user_id", "expert_runs.id"],
            ondelete="CASCADE", name="fk_expert_run_provider_attempts_run",
        ),
        sa.CheckConstraint("position >= 0 AND position < 20", name="ck_expert_run_provider_attempts_position"),
        sa.CheckConstraint("connection_version >= 1", name="ck_expert_run_provider_attempts_version"),
        sa.CheckConstraint(
            "state IN ('pending', 'dispatching', 'succeeded', 'failed', 'skipped', 'interrupted')",
            name="ck_expert_run_provider_attempts_state",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT 1 FROM expert_runs WHERE routing_version IS NOT NULL LIMIT 1"
    )) is not None:
        raise RuntimeError("Refusing to remove persisted Expert routing snapshots.")
    if bind.scalar(sa.text(
        "SELECT 1 FROM expert_run_provider_attempts LIMIT 1"
    )) is not None:
        raise RuntimeError("Refusing to remove persisted Expert provider attempts.")
    op.drop_table("expert_run_provider_attempts")
    op.drop_column("expert_runs", "routing_snapshot")
    op.drop_column("expert_runs", "routing_version")
