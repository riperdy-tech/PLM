"""experiments schema: predeclared measurement plan, directive budget ceiling, design record

Revision ID: 0024_experiments_measurement
Revises: 0023_ar_outcome_horizon
Create Date: 2026-10-14

ADR-0031 and stage R2 (P3):

* ``measurement_plan_json`` holds the experiment's ``MeasurementPlan``: primary metric,
  the outcome horizon P2 declared on the C2 directive, and the maturity rule
  (publication time + horizon). It is fixed at design time, before any content exists.
* ``budget_limit`` is the directive's budget ceiling. Materialisation may spend less,
  never more.
* ``design_json`` records the factor design, the randomisation seed/algorithm/version and
  any design notes (for example, fewer distinct treatments than requested).

Rows written before this migration have none of these. They are refused when loaded;
no horizon or budget is invented for them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_experiments_measurement"
down_revision = "0023_ar_outcome_horizon"
branch_labels = None
depends_on = None

SCHEMA = "experiments"


def upgrade() -> None:
    op.add_column(
        "experiments", sa.Column("measurement_plan_json", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.add_column("experiments", sa.Column("budget_limit", sa.Float(), nullable=True), schema=SCHEMA)
    op.add_column("experiments", sa.Column("design_json", sa.Text(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("experiments", "design_json", schema=SCHEMA)
    op.drop_column("experiments", "budget_limit", schema=SCHEMA)
    op.drop_column("experiments", "measurement_plan_json", schema=SCHEMA)
