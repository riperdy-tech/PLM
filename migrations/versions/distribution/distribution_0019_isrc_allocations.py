"""distribution schema: persisted per-recording ISRC allocation

Revision ID: 0019_distribution_isrc_allocations
Revises: 0018_c5_manifest_facts
Create Date: 2026-10-14

ADR-0029: an ISRC is allocated once per recording (the P4 candidate id) from a persisted
sequence per (country, registrant, year), and is returned unchanged on every later
request. P8 used to derive the ISRC from the track's position, so every release's first
track got the same code and the second release failed on the tracks' unique ISRC.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_distribution_isrc_allocations"
down_revision = "0018_c5_manifest_facts"
branch_labels = None
depends_on = None

SCHEMA = "distribution"


def upgrade() -> None:
    op.create_table(
        "isrc_allocations",
        sa.Column("recording_key", sa.String(128), primary_key=True),
        sa.Column("isrc", sa.String(32), nullable=False, unique=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("registrant", sa.String(3), nullable=False),
        sa.Column("year", sa.String(2), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("isrc_allocations", schema=SCHEMA)
