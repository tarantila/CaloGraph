"""Retain consumed Expert resource IDs after deletion.

Revision ID: 20261124_0059
Revises: 20261124_0058
"""

import sqlalchemy as sa
from alembic import op

revision = "20261124_0059"
down_revision = "20261124_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expert_conversation_identities",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "expert_run_identities",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(sa.text(
        "INSERT INTO expert_conversation_identities (user_id, conversation_id, created_at) "
        "SELECT user_id, id, created_at FROM expert_conversations"
    ))
    op.execute(sa.text(
        "INSERT INTO expert_run_identities (user_id, run_id, created_at) "
        "SELECT user_id, id, created_at FROM expert_runs"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM expert_conversation_identities) "
        "OR EXISTS (SELECT 1 FROM expert_run_identities)"
    )).scalar_one():
        raise RuntimeError("Cannot downgrade while Expert request identity receipts exist")
    op.drop_table("expert_run_identities")
    op.drop_table("expert_conversation_identities")
