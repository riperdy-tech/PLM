"""market schema: snapshot report bodies and the llm_calls cost counter

Revision ID: 0007_market_snapshot_bodies
Revises: 0006_market_score_basis_weight
Create Date: 2026-09-20

Two additions P1.12's report needs:

* `market_snapshots` stores the ten §31 section bodies (`sections_json`) and the
  evidence class of each (`evidence_classes_json`) so §32's change detection can
  compare the current report against the stored previous one without re-reading
  the artifacts — the artifact URI stays the provenance of record.
* §42 requires a per-run `llm_calls` counter alongside `api_calls`; nothing was
  persisting the call count (`llm_tokens` alone cannot say how many calls ran).

The plan told P1.12 to extend the P1.2 migration in place; the repository's
migration discipline (one chain, add-only revisions, a live database already at
head) makes a new revision the only safe form — see the task ledger.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_market_snapshot_bodies"
down_revision = "0006_market_score_basis_weight"
branch_labels = None
depends_on = None

SCHEMA = "market"


def upgrade() -> None:
    op.add_column(
        "market_snapshots",
        sa.Column("sections_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        schema=SCHEMA,
    )
    op.add_column(
        "market_snapshots",
        sa.Column("evidence_classes_json", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'")),
        schema=SCHEMA,
    )
    op.add_column(
        "market_collection_runs",
        sa.Column("cost_llm_calls", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("market_collection_runs", "cost_llm_calls", schema=SCHEMA)
    op.drop_column("market_snapshots", "evidence_classes_json", schema=SCHEMA)
    op.drop_column("market_snapshots", "sections_json", schema=SCHEMA)
