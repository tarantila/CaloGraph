"""Retain consumed owner-scoped Expert attachment IDs.

Revision ID: 20261124_0061
Revises: 20261124_0060
"""

import sqlalchemy as sa
from alembic import op

revision = "20261124_0061"
down_revision = "20261124_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expert_attachment_identities",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("attachment_id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(sa.text(
        "INSERT INTO expert_attachment_identities (user_id, attachment_id, created_at) "
        "SELECT user_id, id, created_at FROM expert_attachments"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM expert_attachment_identities)"
    )).scalar_one():
        raise RuntimeError("Cannot downgrade while Expert attachment identity receipts exist")
    op.drop_table("expert_attachment_identities")
