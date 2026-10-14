"""platform: the cost ledger and budget ceiling

Revision ID: 0027_ops_cost_ledger
Revises: 0026_analytics_outbox
Create Date: 2026-10-14

R3, ADR-0033. The external-write guard's budget stub becomes a persisted reservation
against a ceiling (``plm_platform.ledger``):

* ``budget_ceilings``: a limit per scope (``global`` or ``capability:<name>``) over a
  half-open period, in an explicit currency.
* ``budget_rates``: the operator's rate card, what one write of a capability costs from
  an instant on. No rate means the cost is unknown, never zero.
* ``budget_entries``: one row per (capability, idempotency key), ``reserved`` ->
  ``committed`` | ``released``, bound to the ceiling(s) it was charged against.

Amounts are ``Numeric(20, 6)`` (ADR-0031 ledger precision) with a ``String(3)`` currency.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_ops_cost_ledger"
down_revision = "0026_analytics_outbox"
branch_labels = None
depends_on = None

SCHEMA = "ops"
AMOUNT = sa.Numeric(20, 6)


def upgrade() -> None:
    op.create_table(
        "budget_ceilings",
        sa.Column("ceiling_id", sa.String(200), primary_key=True),
        sa.Column("scope", sa.String(160), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("set_by", sa.String(128), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference", sa.String(256), nullable=True),
        sa.UniqueConstraint("scope", "period_start", name="uq_budget_ceilings_scope_period"),
        sa.CheckConstraint("amount >= 0", name="ck_budget_ceilings_amount_non_negative"),
        sa.CheckConstraint("period_end > period_start", name="ck_budget_ceilings_period"),
        schema=SCHEMA,
    )
    op.create_table(
        "budget_rates",
        sa.Column("capability", sa.String(128), primary_key=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("set_by", sa.String(128), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference", sa.String(256), nullable=True),
        sa.CheckConstraint("amount >= 0", name="ck_budget_rates_amount_non_negative"),
        schema=SCHEMA,
    )
    op.create_table(
        "budget_entries",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column("capability", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "ceiling_id", sa.String(200),
            sa.ForeignKey(f"{SCHEMA}.budget_ceilings.ceiling_id"), nullable=False,
        ),
        sa.Column(
            "capability_ceiling_id", sa.String(200),
            sa.ForeignKey(f"{SCHEMA}.budget_ceilings.ceiling_id"), nullable=True,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reserved_amount", AMOUNT, nullable=False),
        sa.Column("estimate_source", sa.String(16), nullable=False),
        sa.Column("original_amount", AMOUNT, nullable=False),
        sa.Column("original_currency", sa.String(3), nullable=False),
        sa.Column("fx_rate_id", sa.String(200), nullable=True),
        sa.Column("actual_amount", AMOUNT, nullable=True),
        sa.Column("actual_basis", sa.String(32), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settle_reason", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "capability", "idempotency_key", name="uq_budget_entries_capability_key"
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'committed', 'released')", name="ck_budget_entries_status"
        ),
        sa.CheckConstraint(
            "reserved_amount >= 0", name="ck_budget_entries_reserved_non_negative"
        ),
        sa.CheckConstraint(
            "actual_amount IS NULL OR actual_amount >= 0",
            name="ck_budget_entries_actual_non_negative",
        ),
        sa.CheckConstraint(
            "status <> 'committed' OR actual_amount IS NOT NULL",
            name="ck_budget_entries_committed_has_actual",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_budget_entries_ceiling_id", "budget_entries", ["ceiling_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_budget_entries_capability_ceiling_id", "budget_entries",
        ["capability_ceiling_id"], schema=SCHEMA,
    )
    op.create_index("ix_budget_entries_status", "budget_entries", ["status"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_budget_entries_status", table_name="budget_entries", schema=SCHEMA)
    op.drop_index(
        "ix_budget_entries_capability_ceiling_id", table_name="budget_entries", schema=SCHEMA
    )
    op.drop_index("ix_budget_entries_ceiling_id", table_name="budget_entries", schema=SCHEMA)
    op.drop_table("budget_entries", schema=SCHEMA)
    op.drop_table("budget_rates", schema=SCHEMA)
    op.drop_table("budget_ceilings", schema=SCHEMA)
