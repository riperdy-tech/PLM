"""platform: durable pipeline runs and their append-only transitions in schema ops

Revision ID: 0030_ops_pipeline_runs
Revises: 0029_ops_decision_log
Create Date: 2026-10-14

R3 / ADR-0033, architecture PART XIV P27. ``ops.pipeline_runs`` holds one row per
pipeline workflow (current state, the state a held run returns to, last sequence, last
error, completion). ``ops.pipeline_transitions`` is the append-only history: per
transition the step, attempt and retry count, error code and detail, timestamps,
provider ids, cost (exact amount with its currency and basis) and input/output refs,
unique per (workflow_id, sequence). On Postgres a trigger refuses UPDATE and DELETE on
it. ``plm_platform.pipeline.store`` is the only writer.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_ops_pipeline_runs"
down_revision = "0029_ops_decision_log"
branch_labels = None
depends_on = None

SCHEMA = "ops"


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("workflow_id", sa.String(200), primary_key=True),
        sa.Column("directive_id", sa.String(128), nullable=False),
        sa.Column("brand_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("held_from", sa.String(32), nullable=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_pipeline_runs_state", "pipeline_runs", ["state"], schema=SCHEMA)
    op.create_index("ix_pipeline_runs_directive", "pipeline_runs", ["directive_id"],
                    schema=SCHEMA)
    op.create_table(
        "pipeline_transitions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(200),
            sa.ForeignKey(f"{SCHEMA}.pipeline_runs.workflow_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=True),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("held_from", sa.String(32), nullable=True),
        sa.Column("step", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_ids_json", sa.JSON(), nullable=False),
        sa.Column("cost_amount", sa.Numeric(20, 6), nullable=True),
        sa.Column("cost_currency", sa.String(3), nullable=True),
        sa.Column("cost_basis", sa.String(32), nullable=True),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("outputs_json", sa.JSON(), nullable=False),
        sa.Column("hold_case_ids_json", sa.JSON(), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("workflow_id", "sequence", name="uq_pipeline_transitions_run_seq"),
        sa.CheckConstraint(
            "(cost_amount IS NULL) = (cost_currency IS NULL)",
            name="ck_pipeline_transitions_cost_has_currency",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_pipeline_transitions_workflow", "pipeline_transitions",
                    ["workflow_id"], schema=SCHEMA)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION ops.pipeline_transitions_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'ops.pipeline_transitions is append-only';
            END
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER pipeline_transitions_append_only
            BEFORE UPDATE OR DELETE ON ops.pipeline_transitions
            FOR EACH ROW EXECUTE FUNCTION ops.pipeline_transitions_append_only()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS pipeline_transitions_append_only ON ops.pipeline_transitions"
        )
        op.execute("DROP FUNCTION IF EXISTS ops.pipeline_transitions_append_only()")
    op.drop_index("ix_pipeline_transitions_workflow", table_name="pipeline_transitions",
                  schema=SCHEMA)
    op.drop_table("pipeline_transitions", schema=SCHEMA)
    op.drop_index("ix_pipeline_runs_directive", table_name="pipeline_runs", schema=SCHEMA)
    op.drop_index("ix_pipeline_runs_state", table_name="pipeline_runs", schema=SCHEMA)
    op.drop_table("pipeline_runs", schema=SCHEMA)
