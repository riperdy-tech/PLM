"""analytics schema: C7 release links; unknown stays unknown

Revision ID: 0020_analytics_c7_links
Revises: 0019_distribution_isrc
Create Date: 2026-10-14

ADR-0030:

* ``release_links``: one row per (release, ISRC) from P8's C7 leg-1 record, with the
  lineage it was released under, so statement revenue is attributable to the arm.
* ``track_metrics.track_id`` / ``release_id`` become nullable: a statement row that names
  neither is stored as unknown instead of under an invented id.
* ``revenue_statements`` records revenue on rows with no ISRC as unattributed.
* ``experiment_outcomes.quality_status`` becomes nullable (not assessed), and neither it
  nor ``maturity`` has a server default any more.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_analytics_c7_links"
down_revision = "0019_distribution_isrc"
branch_labels = None
depends_on = None

SCHEMA = "analytics"


def upgrade() -> None:
    op.create_table(
        "release_links",
        sa.Column("release_id", sa.String(64), primary_key=True),
        sa.Column("isrc", sa.String(32), primary_key=True),
        sa.Column("media_package_id", sa.String(64), nullable=False),
        sa.Column("lineage_json", sa.JSON(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_release_links_isrc", "release_links", ["isrc"], schema=SCHEMA)
    op.alter_column("track_metrics", "track_id", nullable=True, schema=SCHEMA)
    op.alter_column("track_metrics", "release_id", nullable=True, schema=SCHEMA)
    op.add_column(
        "revenue_statements",
        sa.Column(
            "unattributed_revenue_usd", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "revenue_statements",
        sa.Column(
            "unattributed_row_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        schema=SCHEMA,
    )
    op.alter_column(
        "experiment_outcomes", "quality_status", nullable=True, server_default=None, schema=SCHEMA
    )
    op.alter_column("experiment_outcomes", "maturity", server_default=None, schema=SCHEMA)


def downgrade() -> None:
    op.execute(
        sa.text(f"UPDATE {SCHEMA}.experiment_outcomes SET quality_status = 'ELIGIBLE' "
                "WHERE quality_status IS NULL")
    )
    op.alter_column("experiment_outcomes", "quality_status", nullable=False, schema=SCHEMA)
    op.drop_column("revenue_statements", "unattributed_row_count", schema=SCHEMA)
    op.drop_column("revenue_statements", "unattributed_revenue_usd", schema=SCHEMA)
    op.alter_column("track_metrics", "release_id", nullable=False, schema=SCHEMA)
    op.alter_column("track_metrics", "track_id", nullable=False, schema=SCHEMA)
    op.drop_index("ix_release_links_isrc", table_name="release_links", schema=SCHEMA)
    op.drop_table("release_links", schema=SCHEMA)
