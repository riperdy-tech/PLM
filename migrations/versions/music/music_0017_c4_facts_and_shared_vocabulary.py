"""music schema: facts the C4 event is built from, and the shared commercial-use vocabulary

Revision ID: 0017_music_c4_facts
Revises: 0016_music_plan_lineage
Create Date: 2026-10-14

ADR-0027:

* ``generation_plans.track_spec_json`` keeps the C3 spec a plan was made from, so the C4
  event's vocal mode, genre tags, prompt and requested duration come from stored facts.
* ``generated_candidates.source_attempt_id`` records the attempt that produced each
  candidate, so the exported rights snapshot is that attempt's (not the plan's first).
* ``rights_snapshots.commercial_use_status`` moves to the shared P5 §34 vocabulary:
  permitted -> eligible, restricted -> conditional, prohibited -> ineligible.

Rows stored before this revision keep NULL in the new columns and cannot be exported.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_music_c4_facts"
down_revision = "0016_music_plan_lineage"
branch_labels = None
depends_on = None

SCHEMA = "music"

_FORWARD = {"permitted": "eligible", "restricted": "conditional", "prohibited": "ineligible"}


def upgrade() -> None:
    op.add_column(
        "generation_plans",
        sa.Column("track_spec_json", sa.JSON(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "generated_candidates",
        sa.Column("source_attempt_id", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    for old, new in _FORWARD.items():
        op.execute(
            sa.text(
                f"UPDATE {SCHEMA}.rights_snapshots SET commercial_use_status = :new "
                "WHERE commercial_use_status = :old"
            ).bindparams(old=old, new=new)
        )


def downgrade() -> None:
    for old, new in _FORWARD.items():
        op.execute(
            sa.text(
                f"UPDATE {SCHEMA}.rights_snapshots SET commercial_use_status = :old "
                "WHERE commercial_use_status = :new"
            ).bindparams(old=old, new=new)
        )
    op.drop_column("generated_candidates", "source_attempt_id", schema=SCHEMA)
    op.drop_column("generation_plans", "track_spec_json", schema=SCHEMA)
