"""market schema: record the transformation version on market observations

Revision ID: 0003_market_norm_version
Revises: 0002_market_initial
Create Date: 2026-09-20

Every derived value must trace to its transformation version: the scores table
carries `normalization_version`, and an observation — the output of the
normalization step — now carries the version of the rules that derived it too.

The revision id stays within alembic's `version_num varchar(32)` column; the
file slug carries the full description.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_market_norm_version"
down_revision = "0002_market_initial"
branch_labels = None
depends_on = None

SCHEMA = "market"


def upgrade() -> None:
    op.add_column(
        "market_observations",
        sa.Column("normalization_version", sa.Text(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("market_observations", "normalization_version", schema=SCHEMA)
