"""market schema: the publishing channel on each observation

Revision ID: 0021_market_channel_id
Revises: 0020_analytics_c7_links
Create Date: 2026-10-14

§13 concentration (HHI) is a share of activity *per channel*. The observation rows
carried no channel, so the feature layer keyed concentration on the video id and every
arm looked maximally fragmented (review R2, `features/arm_features.py`).

``market_observations.channel_id`` records the publishing channel named by the video or
search snippet. It is nullable: rows stored before this revision have no channel, and
the feature layer treats a NULL as unknown attribution (concentration unknown), never
as "each video is its own channel". Nothing is backfilled or invented here.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_market_channel_id"
down_revision = "0020_analytics_c7_links"
branch_labels = None
depends_on = None

SCHEMA = "market"


def upgrade() -> None:
    op.add_column(
        "market_observations",
        sa.Column("channel_id", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("market_observations", "channel_id", schema=SCHEMA)
