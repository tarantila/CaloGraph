"""Correct record presence for amount-less YAZIO simple products.

Revision ID: 20261026_0041
Revises: 20261021_0040
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261026_0041"
down_revision: str | None = "20261021_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    runs = sa.table(
        "nutrition_ingestion_runs",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("provider_key", sa.String(64)),
        sa.column("source_instance_id", sa.Uuid()),
        sa.column("connector_variant", sa.String(64)),
    )
    sources = sa.table(
        "nutrition_source_observations",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("ingestion_run_id", sa.Uuid()),
        sa.column("source_instance_id", sa.Uuid()),
        sa.column("provider_key", sa.String(64)),
        sa.column("connector_variant", sa.String(64)),
        sa.column("source_namespace", sa.String(128)),
        sa.column("observation_kind", sa.String(32)),
        sa.column("presence_state", sa.String(16)),
    )
    events = sa.table(
        "nutrition_consumption_events",
        sa.column("user_id", sa.Uuid()),
        sa.column("source_observation_id", sa.Uuid()),
        sa.column("source_instance_id", sa.Uuid()),
        sa.column("provider_key", sa.String(64)),
        sa.column("event_kind", sa.String(32)),
        sa.column("presence_state", sa.String(16)),
    )
    # SDK provenance is stored on the owning run; the source-level variant is
    # normally NULL, but an explicit legacy/unknown marker must remain untouched.
    sdk_ingestion_run = sa.exists(
        sa.select(sa.literal(1))
        .select_from(runs)
        .where(
            runs.c.id == sources.c.ingestion_run_id,
            runs.c.user_id == sources.c.user_id,
            runs.c.provider_key == "yazio",
            runs.c.source_instance_id == sources.c.source_instance_id,
            runs.c.connector_variant == "sdk-v22",
        )
    )
    source_scope = sa.and_(
        sources.c.provider_key == "yazio",
        sources.c.source_namespace == "yazio.simple_product",
        sources.c.observation_kind == "simple_product",
        sa.or_(
            sources.c.connector_variant.is_(None),
            sources.c.connector_variant == "sdk-v22",
        ),
        sdk_ingestion_run,
    )

    # In the SDK DTO, membership in consumed_simple_products proves that the
    # record exists. Amount has its own field-level presence and is independent.
    bind.execute(
        sources.update()
        .where(source_scope, sources.c.presence_state == "missing")
        .values(presence_state="supplied")
    )

    matching_source = sa.exists(
        sa.select(sa.literal(1))
        .select_from(sources)
        .where(
            sources.c.id == events.c.source_observation_id,
            sources.c.user_id == events.c.user_id,
            events.c.source_instance_id == sources.c.source_instance_id,
            source_scope,
        )
    )
    bind.execute(
        events.update()
        .where(
            events.c.provider_key == "yazio",
            events.c.event_kind == "simple_product",
            events.c.presence_state == "missing",
            matching_source,
        )
        .values(presence_state="supplied")
    )


def downgrade() -> None:
    # Irreversible data correction: the old missing state conflated absent
    # amount with absent record, so the original intent cannot be reconstructed.
    pass
