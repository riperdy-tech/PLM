"""market schema: persist which observation metrics are not_ready

Revision ID: 0004_market_not_ready_metrics
Revises: 0003_market_norm_version
Create Date: 2026-09-20

`market_observations` maps Tri states onto numeric columns: NULL cannot tell
`unknown` (nothing was observed) from `not_ready` (expected later; the window has
not completed). The JSON list records the metrics whose NULL means `not_ready`,
so all three states survive the round trip.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_market_not_ready_metrics"
down_revision = "0003_market_norm_version"
branch_labels = None
depends_on = None

SCHEMA = "market"


def upgrade() -> None:
    op.add_column(
        "market_observations",
        sa.Column("not_ready_metrics", sa.JSON(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("market_observations", "not_ready_metrics", schema=SCHEMA)
