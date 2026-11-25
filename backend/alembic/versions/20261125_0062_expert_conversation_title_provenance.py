"""Track provenance for generated Expert conversation titles.

Revision ID: 20261125_0062
Revises: 20261124_0061
"""

import sqlalchemy as sa
from alembic import op

revision = "20261125_0062"
down_revision = "20261124_0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expert_conversations",
        sa.Column("title_source", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "ck_expert_conversations_title_source",
        "expert_conversations",
        "title_source IS NULL OR title_source = 'generated'",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM expert_conversations "
            "WHERE title_source IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError(
            "Cannot downgrade while generated Expert conversation title provenance exists"
        )
    op.drop_constraint(
        "ck_expert_conversations_title_source",
        "expert_conversations",
        type_="check",
    )
    op.drop_column("expert_conversations", "title_source")
