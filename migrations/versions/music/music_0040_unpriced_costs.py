"""P4 monetary provenance: credits are not actual currency.

Revision ID: 0040_p4_unpriced_costs
Revises: 0039_merge_0038_branches

CODE ONLY. The 0039 development database must not be changed absent a
separately approved migration, backup/restore proof and rollout.
Old numeric records remain intact but are labelled legacy_unverified; the
migration does not rewrite or invent their monetary meaning.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_p4_unpriced_costs"
down_revision = "0039_merge_0038_branches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "generation_attempts", "cost_estimate", schema="music",
        existing_type=sa.Float(), nullable=True,
    )
    op.alter_column(
        "generation_attempts", "actual_cost", schema="music",
        existing_type=sa.Float(), nullable=True,
    )
    op.add_column(
        "generation_attempts",
        sa.Column("money_basis", sa.String(32), nullable=False,
                  server_default="legacy_unverified"),
        schema="music",
    )
    op.add_column(
        "generation_attempts",
        sa.Column("cost_currency", sa.String(8), nullable=True),
        schema="music",
    )
    op.add_column(
        "generation_attempts",
        sa.Column("cost_receipt_ref", sa.String(256), nullable=True),
        schema="music",
    )
    for column in ("estimated_amount", "actual_amount"):
        op.alter_column(
            "provider_cost_records", column, schema="music",
            existing_type=sa.Float(), nullable=True,
        )
    op.alter_column(
        "provider_cost_records", "currency", schema="music",
        existing_type=sa.String(length=8), nullable=True,
    )
    op.add_column(
        "provider_cost_records",
        sa.Column("money_basis", sa.String(32), nullable=False,
                  server_default="legacy_unverified"),
        schema="music",
    )
    op.add_column(
        "provider_cost_records",
        sa.Column("cost_receipt_ref", sa.String(256), nullable=True),
        schema="music",
    )
    # Existing rows keep their legacy_unverified marker. New insertions MUST
    # explicitly supply a basis instead of inheriting that historical label.
    for table in ("generation_attempts", "provider_cost_records"):
        op.alter_column(
            table, "money_basis", schema="music",
            existing_type=sa.String(length=32), server_default=None,
        )
    op.create_check_constraint(
        "ck_generation_attempts_money_provenance",
        "generation_attempts",
        "money_basis IN ('unpriced', 'provider_reported', 'legacy_unverified') "
        "AND (money_basis != 'unpriced' OR "
        "(cost_estimate IS NULL AND actual_cost IS NULL "
        "AND cost_currency IS NULL AND cost_receipt_ref IS NULL)) "
        "AND (money_basis != 'provider_reported' OR "
        "(actual_cost IS NOT NULL AND cost_currency IS NOT NULL "
        "AND cost_receipt_ref IS NOT NULL AND cost_receipt_ref <> ''))",
        schema="music",
    )
    op.create_check_constraint(
        "ck_provider_cost_records_money_provenance",
        "provider_cost_records",
        "money_basis IN ('unpriced', 'provider_reported', 'legacy_unverified') "
        "AND (money_basis != 'unpriced' OR "
        "(estimated_amount IS NULL AND actual_amount IS NULL "
        "AND currency IS NULL AND cost_receipt_ref IS NULL)) "
        "AND (money_basis != 'provider_reported' OR "
        "(actual_amount IS NOT NULL AND currency IS NOT NULL "
        "AND cost_receipt_ref IS NOT NULL AND cost_receipt_ref <> '' "
        "AND pricing_version != 'unpriced'))",
        schema="music",
    )


def downgrade() -> None:
    # A downgrade must not turn unknown monetary amounts into zero, or drop
    # their provenance. On a database with post-0040 records it is unsafe.
    connection = op.get_bind()
    for table, amounts in (
        ("generation_attempts", "cost_estimate IS NULL OR actual_cost IS NULL"),
        (
            "provider_cost_records",
            "estimated_amount IS NULL OR actual_amount IS NULL OR currency IS NULL",
        ),
    ):
        remaining = connection.execute(sa.text(
            f"SELECT COUNT(*) FROM music.{table} "
            f"WHERE money_basis != 'legacy_unverified' OR {amounts}"
        )).scalar_one()
        if remaining:
            raise RuntimeError(
                "cannot downgrade P4 unpriced cost metadata without losing "
                "unknown monetary values or provenance"
            )
    op.drop_constraint(
        "ck_provider_cost_records_money_provenance",
        "provider_cost_records", schema="music", type_="check",
    )
    op.drop_constraint(
        "ck_generation_attempts_money_provenance",
        "generation_attempts", schema="music", type_="check",
    )
    op.drop_column("provider_cost_records", "cost_receipt_ref", schema="music")
    op.drop_column("provider_cost_records", "money_basis", schema="music")
    op.alter_column(
        "provider_cost_records", "currency", schema="music",
        existing_type=sa.String(length=8), nullable=False,
    )
    for column in ("actual_amount", "estimated_amount"):
        op.alter_column(
            "provider_cost_records", column, schema="music",
            existing_type=sa.Float(), nullable=False,
        )
    op.drop_column("generation_attempts", "cost_receipt_ref", schema="music")
    op.drop_column("generation_attempts", "cost_currency", schema="music")
    op.drop_column("generation_attempts", "money_basis", schema="music")
    op.alter_column(
        "generation_attempts", "actual_cost", schema="music",
        existing_type=sa.Float(), nullable=False,
    )
    op.alter_column(
        "generation_attempts", "cost_estimate", schema="music",
        existing_type=sa.Float(), nullable=False,
    )
