"""Add conversation attachments and explicit document permissions.

Revision ID: 20261114_0049
Revises: 20261114_0048
"""

import sqlalchemy as sa

from alembic import op

revision = "20261114_0049"
down_revision = "20261114_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_consents", sa.Column("include_attachments", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("expert_conversations", sa.Column("title", sa.String(120), nullable=True))
    op.add_column("expert_conversations", sa.Column("uses_attachments", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("expert_runs", sa.Column("attachment_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.execute(sa.text(
        "UPDATE expert_conversations SET title = (SELECT substr(message, 1, 120) FROM expert_runs "
        "WHERE expert_runs.user_id = expert_conversations.user_id AND "
        "expert_runs.conversation_id = expert_conversations.id ORDER BY created_at, id LIMIT 1) "
        "WHERE mode = 'chat'"
    ))
    op.create_table(
        "expert_attachments",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(120), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id", "conversation_id"], ["expert_conversations.user_id", "expert_conversations.id"], ondelete="CASCADE", name="fk_expert_attachments_conversation"),
        sa.CheckConstraint("byte_size BETWEEN 1 AND 4194304", name="ck_expert_attachments_size"),
        sa.CheckConstraint("media_type IN ('application/pdf', 'image/png', 'image/jpeg', 'image/webp')", name="ck_expert_attachments_media"),
    )
    op.create_index("ix_expert_attachments_conversation", "expert_attachments", ["user_id", "conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_table("expert_attachments")
    op.drop_column("expert_runs", "attachment_ids")
    op.drop_column("expert_conversations", "uses_attachments")
    op.drop_column("expert_conversations", "title")
    op.drop_column("ai_consents", "include_attachments")
