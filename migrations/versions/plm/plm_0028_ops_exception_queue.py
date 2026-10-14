"""platform: the exception queue with hold classes in schema ops

Revision ID: 0028_ops_exception_queue
Revises: 0027_ops_cost_ledger
Create Date: 2026-10-14

R3 / ADR-0033. ``ops.exception_cases`` holds one row per (origin phase, subject, reason
code) with the hold class, override permission, required evidence and reviewer role
copied from the policy at raise time, plus status, actor and timestamps.
``ops.exception_events`` is its append-only decision history; on Postgres a trigger
refuses UPDATE and DELETE on it. ``plm_platform.exceptions`` is the only writer.
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_ops_exception_queue"
down_revision = "0027_ops_cost_ledger"
branch_labels = None
depends_on = None

SCHEMA = "ops"


def upgrade() -> None:
    op.create_table(
        "exception_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("origin_phase", sa.String(16), nullable=False),
        sa.Column("subject_ref", sa.String(256), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("hold_class", sa.String(32), nullable=False),
        sa.Column("override_permitted", sa.Boolean(), nullable=False),
        sa.Column("required_evidence_json", sa.JSON(), nullable=False),
        sa.Column("reviewer_role", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("policy_mapped", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("resolution", sa.String(48), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("raised_by", sa.String(128), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by", sa.String(128), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "origin_phase", "subject_ref", "reason_code", name="uq_exception_cases_subject_reason"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_exception_cases_status", "exception_cases", ["status"], schema=SCHEMA)
    op.create_table(
        "exception_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.exception_cases.id"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("decision_code", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "seq", name="uq_exception_events_case_seq"),
        schema=SCHEMA,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION ops.exception_events_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'ops.exception_events is append-only';
            END
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER exception_events_append_only
            BEFORE UPDATE OR DELETE ON ops.exception_events
            FOR EACH ROW EXECUTE FUNCTION ops.exception_events_append_only()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS exception_events_append_only ON ops.exception_events")
        op.execute("DROP FUNCTION IF EXISTS ops.exception_events_append_only()")
    op.drop_table("exception_events", schema=SCHEMA)
    op.drop_index("ix_exception_cases_status", table_name="exception_cases", schema=SCHEMA)
    op.drop_table("exception_cases", schema=SCHEMA)
