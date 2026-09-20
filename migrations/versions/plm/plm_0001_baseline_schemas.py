"""baseline: create the nine phase schemas

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-20

One schema per phase (P0 §4), in dependency order for creation. Phase tables are
never created here: every phase owns its own later revisions, and a phase may
only alter its own schema (see migrations/README.md).

Downgrade drops the schemas (CASCADE) so ``alembic downgrade base`` round-trips.
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

PHASE_SCHEMAS = (
    "market",  # P1
    "ar",  # P2
    "experiments",  # P3
    "music",  # P4
    "qa",  # P5
    "media",  # P6
    "youtube",  # P7
    "distribution",  # P8
    "analytics",  # P9
)


def upgrade() -> None:
    for schema in PHASE_SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    for schema in reversed(PHASE_SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
