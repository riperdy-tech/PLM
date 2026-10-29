"""Keep unobserved provider credits NULL rather than inventing usage.

Revision ID: 0041_p4_credit_provenance
Revises: 0040_p4_unpriced_costs

CODE ONLY. Apply solely to a separately approved target after verified
retained backup and isolated restore. Old credit numbers are left unchanged
and explicitly labelled legacy_unverified.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_p4_credit_provenance"
down_revision = "0040_p4_unpriced_costs"
branch_labels = None
depends_on = None


_ATTEMPTS_CONSTRAINT = (
    "credit_basis IN ('unknown', 'mock_simulated', 'provider_reported', 'legacy_unverified') "
    "AND (credit_basis != 'unknown' OR "
    "(credit_estimate IS NULL AND actual_credits IS NULL AND credit_receipt_ref IS NULL)) "
    "AND (credit_basis != 'mock_simulated' OR "
    "(actual_credits IS NOT NULL AND credit_receipt_ref IS NULL)) "
    "AND (credit_basis != 'provider_reported' OR "
    "(actual_credits IS NOT NULL AND credit_receipt_ref IS NOT NULL "
    "AND credit_receipt_ref <> ''))"
)
_COSTS_CONSTRAINT = (
    "credit_basis IN ('unknown', 'mock_simulated', 'provider_reported', 'legacy_unverified') "
    "AND (credit_basis != 'unknown' OR "
    "(credits_estimated IS NULL AND credits_actual IS NULL AND credit_receipt_ref IS NULL)) "
    "AND (credit_basis != 'mock_simulated' OR "
    "(credits_actual IS NOT NULL AND credit_receipt_ref IS NULL)) "
    "AND (credit_basis != 'provider_reported' OR "
    "(credits_actual IS NOT NULL AND credit_receipt_ref IS NOT NULL "
    "AND credit_receipt_ref <> ''))"
)


def upgrade() -> None:
    for table, columns in (
        ("generation_attempts", ("credit_estimate", "actual_credits")),
        ("provider_cost_records", ("credits_estimated", "credits_actual")),
    ):
        for column in columns:
            op.alter_column(
                table, column, schema="music",
                existing_type=sa.Float(), nullable=True,
            )
        op.add_column(
            table, sa.Column(
                "credit_basis", sa.String(32), nullable=False,
                server_default="legacy_unverified",
            ), schema="music",
        )
        op.add_column(
            table, sa.Column("credit_receipt_ref", sa.String(256), nullable=True),
            schema="music",
        )
        # Default is only for historical rows, not permission to label new
        # unverified provider data as a historical cost.
        op.alter_column(
            table, "credit_basis", schema="music",
            existing_type=sa.String(32), server_default=None,
        )
    op.create_check_constraint(
        "ck_generation_attempts_credit_provenance",
        "generation_attempts", _ATTEMPTS_CONSTRAINT, schema="music",
    )
    op.create_check_constraint(
        "ck_provider_cost_records_credit_provenance",
        "provider_cost_records", _COSTS_CONSTRAINT, schema="music",
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table, columns in (
        ("generation_attempts", ("credit_estimate", "actual_credits")),
        ("provider_cost_records", ("credits_estimated", "credits_actual")),
    ):
        unsafe = connection.execute(sa.text(
            f"SELECT count(*) FROM music.{table} "
            f"WHERE credit_basis != 'legacy_unverified' "
            f"OR {columns[0]} IS NULL OR {columns[1]} IS NULL"
        )).scalar_one()
        if unsafe:
            raise RuntimeError(
                "cannot downgrade: unknown or evidenced provider credit usage "
                "cannot be converted into an invented numeric value"
            )
    for table, name, columns in (
        ("generation_attempts", "ck_generation_attempts_credit_provenance",
         ("actual_credits", "credit_estimate")),
        ("provider_cost_records", "ck_provider_cost_records_credit_provenance",
         ("credits_actual", "credits_estimated")),
    ):
        op.drop_constraint(name, table, schema="music", type_="check")
        op.drop_column(table, "credit_receipt_ref", schema="music")
        op.drop_column(table, "credit_basis", schema="music")
        for column in columns:
            op.alter_column(
                table, column, schema="music",
                existing_type=sa.Float(), nullable=False,
            )
