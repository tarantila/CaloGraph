"""Add persistent security audit events.

Revision ID: 20260824_0017
Revises: 20260817_0016
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260824_0017"
down_revision: str | None = "20260817_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_security_audit_events_actor_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], name="fk_security_audit_events_target_user_id_users", ondelete="SET NULL"),
        sa.Column("actor_ref", sa.String(length=16), nullable=True),
        sa.Column("target_ref", sa.String(length=16), nullable=True),
        sa.Column("username_snapshot", sa.String(length=190), nullable=True),
        sa.Column("request_id", sa.String(length=32), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("client_ref", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_security_audit_events_occurred_at", "security_audit_events", ["occurred_at"])
    op.create_index("ix_security_audit_events_event_outcome", "security_audit_events", ["event", "outcome"])
    op.create_index("ix_security_audit_events_actor_user_id", "security_audit_events", ["actor_user_id"])
    op.create_index("ix_security_audit_events_target_user_id", "security_audit_events", ["target_user_id"])


def downgrade() -> None:
    op.drop_index("ix_security_audit_events_target_user_id", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_actor_user_id", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_event_outcome", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_occurred_at", table_name="security_audit_events")
    op.drop_table("security_audit_events")
