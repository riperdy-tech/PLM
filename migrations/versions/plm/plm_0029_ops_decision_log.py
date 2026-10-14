"""platform: the decision log (architecture PART XXI)

Revision ID: 0029_ops_decision_log
Revises: 0028_ops_exception_queue
Create Date: 2026-10-14

One row per major decision (P2 allocations first): date, decision, input data, model,
confidence and action, with the realized result attached later. Lives in ``ops``
(ADR-0033) because the decision is P2's and the result arrives from P9.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_ops_decision_log"
down_revision = "0028_ops_exception_queue"
branch_labels = None
depends_on = None

SCHEMA = "ops"


def upgrade() -> None:
    op.create_table(
        "decision_log",
        sa.Column("decision_key", sa.String(128), primary_key=True),
        sa.Column("decision_kind", sa.String(64), nullable=False),
        sa.Column("origin_phase", sa.String(32), nullable=False),
        sa.Column("subject_ref", sa.String(128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("action_json", sa.JSON(), nullable=False),
        sa.Column("input_data_json", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Numeric(10, 6), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("result_recorded_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_decision_log_subject", "decision_log", ["subject_ref"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_decision_log_subject", table_name="decision_log", schema=SCHEMA)
    op.drop_table("decision_log", schema=SCHEMA)
