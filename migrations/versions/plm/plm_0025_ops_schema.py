"""platform: the ops schema for cross-phase runtime state

Revision ID: 0025_ops_schema
Revises: 0024_experiments_measurement
Create Date: 2026-10-14

ADR-0033. The cost ledger, the exception queue and the decision log are cross-phase:
the budget ceiling is one number for the whole program, and a hold or a decision can
originate in any phase. They live in a schema owned by ``plm_platform`` instead of being
copied into nine phase schemas. Phase tables never move here.
"""

from alembic import op

revision = "0025_ops_schema"
down_revision = "0024_experiments_measurement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS ops CASCADE")
