"""market schema: record how complete each score's basis was

Revision ID: 0006_market_score_basis_weight
Revises: 0005_market_quota_units
Create Date: 2026-09-20

Review ruling (P1 fix round): renormalising over measured components keeps §17.3's
"unknown is not zero" rule, but it is coverage-blind — an arm scored from two
components and one scored from six both produce smooth, comparable 0-100 numbers,
and nothing persisted said how complete the basis was. `basis_weight` is the sum of
the component weights that were actually measured (1.0 = all seven). Rows that
predate this column default to 0, which reads as "no basis recorded": the ranking
floor treats them as under-covered rather than silently ranking them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_market_score_basis_weight"
down_revision = "0005_market_quota_units"
branch_labels = None
depends_on = None

SCHEMA = "market"


def upgrade() -> None:
    op.add_column(
        "market_opportunity_scores",
        sa.Column("basis_weight", sa.Float(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("market_opportunity_scores", "basis_weight", schema=SCHEMA)
