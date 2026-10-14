"""music schema: generation plans carry their experiment lineage

Revision ID: 0016_music_plan_lineage
Revises: 0015_analytics_initial
Create Date: 2026-10-14

ADR-0026: the C3 track spec now carries the full ExperimentLineage (experiment, family,
arm, genome, directive). P4 stores it on the plan so every candidate the plan produces
can be exported to P5 with its real identity instead of a placeholder. The column is
nullable only for plans stored before this revision; such plans cannot export.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_music_plan_lineage"
down_revision = "0015_analytics_initial"
branch_labels = None
depends_on = None

SCHEMA = "music"


def upgrade() -> None:
    op.add_column(
        "generation_plans",
        sa.Column("lineage_json", sa.JSON(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("generation_plans", "lineage_json", schema=SCHEMA)
