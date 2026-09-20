"""market schema: record the provider quota units each collection run is charged

Revision ID: 0005_market_quota_units
Revises: 0004_market_not_ready_metrics
Create Date: 2026-09-20

P1 §42 requires `api_calls` and `api_quota_units` to be exposed separately, and
§27's daily ceiling needs a durable per-day ledger: the connector's `QuotaBudget`
is per-run, so without a stored unit count a day of scheduled replays could
overspend the provider's allowance. `cost_api_quota_units` carries the units the
run's budget actually charged (search.list = 100, list calls = 1).
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_market_quota_units"
down_revision = "0004_market_not_ready_metrics"
branch_labels = None
depends_on = None

SCHEMA = "market"


def upgrade() -> None:
    op.add_column(
        "market_collection_runs",
        sa.Column("cost_api_quota_units", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("market_collection_runs", "cost_api_quota_units", schema=SCHEMA)
