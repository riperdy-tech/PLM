"""analytics schema: the transactional outbox for events P9 announces

Revision ID: 0026_analytics_outbox
Revises: 0025_ops_schema
Create Date: 2026-10-14

Stage R3 (transactional outbox) and ADR-0033: a producer keeps its outbox in its own
schema, and the outbox row commits in the same transaction as the fact it announces. P9's
first event is the C7 leg-2 reward to P2's ``POST /ar/results``; before this revision it
was posted in-request, a failed POST was swallowed and the reward was recorded anyway.

The DDL mirrors ``plm_platform.outbox.outbox_table``; the unit drift test compares them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_analytics_outbox"
down_revision = "0025_ops_schema"
branch_labels = None
depends_on = None

SCHEMA = "analytics"
TABLE = "outbox_events"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("reconcile_count", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(32), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_detail", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("topic", "idempotency_key", name="uq_outbox_events_topic_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_analytics_outbox_events_due", TABLE, ["status", "next_attempt_at"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_analytics_outbox_events_due", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
