"""platform: Idempotency-Key records for the P3 and P4 mutating endpoints

Revision ID: 0035_idempotency_records
Revises: 0034_experiments_playlist_range
Create Date: 2026-10-15

R5: the pipeline retries P3 (design / validate / materialize / extend) and P4 (plan /
execute / export) with an ``Idempotency-Key``. A retry after a lost response used to
re-execute and fail closed on its own duplicate insert. Each phase now stores, per key,
the request fingerprint and the response it returned (``plm_platform.idempotency``), in
its own schema (ADR-0033) so the record commits in the same transaction as the domain
rows. One table per phase, the same DDL as ``idempotency_table``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_idempotency_records"
down_revision = "0034_experiments_playlist_range"
branch_labels = None
depends_on = None

SCHEMAS = ("experiments", "music")
TABLE = "idempotency_records"


def upgrade() -> None:
    for schema in SCHEMAS:
        op.create_table(
            TABLE,
            sa.Column("idempotency_key", sa.String(255), primary_key=True),
            sa.Column("request_method", sa.String(8), nullable=False),
            sa.Column("request_path", sa.String(512), nullable=False),
            sa.Column("request_fingerprint", sa.String(64), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            schema=schema,
        )


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.drop_table(TABLE, schema=schema)
