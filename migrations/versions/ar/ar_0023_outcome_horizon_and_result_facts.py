"""ar schema: full policy config, declared outcome horizon, one row per result fact

Revision ID: 0023_ar_outcome_horizon
Revises: 0022_analytics_money
Create Date: 2026-10-14

ADR-0031:

* ``ar_allocation_policy.config_json`` stores the whole validated policy. Rebuilding a
  policy from its queryable columns filled every field without a column from code
  defaults. Rows written before this migration have no ``config_json`` and are refused on
  load; an operator creates and activates a new version.
* ``ar_production_directive.outcome_horizon`` serves the C2 1.1 field. Pre-existing rows
  have none and are refused when served.
* Result facts are keyed by (experiment, content, window, maturity), not (experiment,
  window): the old key dropped a mature result that followed a provisional one, and made
  two contents of one experiment collide.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_ar_outcome_horizon"
down_revision = "0022_analytics_money"
branch_labels = None
depends_on = None

SCHEMA = "ar"


def upgrade() -> None:
    op.add_column(
        "ar_allocation_policy", sa.Column("config_json", sa.JSON(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "ar_production_directive",
        sa.Column("outcome_horizon", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_ar_reward_observation_window", "ar_reward_observation", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_ar_reward_observation_fact",
        "ar_reward_observation",
        ["experiment_id", "content_id", "observation_window", "maturity"],
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_ar_experiment_result_window", "ar_experiment_result", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_ar_experiment_result_fact",
        "ar_experiment_result",
        ["experiment_id", "content_id", "observation_window", "status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ar_experiment_result_fact", "ar_experiment_result", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_ar_experiment_result_window",
        "ar_experiment_result",
        ["experiment_id", "observation_window"],
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_ar_reward_observation_fact", "ar_reward_observation", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_ar_reward_observation_window",
        "ar_reward_observation",
        ["experiment_id", "observation_window"],
        schema=SCHEMA,
    )
    op.drop_column("ar_production_directive", "outcome_horizon", schema=SCHEMA)
    op.drop_column("ar_allocation_policy", "config_json", schema=SCHEMA)
