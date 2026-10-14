"""music schema: attempts record the provider's model version and returned lyrics

Revision ID: 0032_music_attempt_provenance
Revises: 0031_qa_detector_catalog
Create Date: 2026-10-15

ADR-0035: C4 3.0 carries the P28 facts of the producing attempt. The model version and
the lyrics are what the provider returned for that attempt, so they are stored with it.
Earlier attempts have neither (NULL): their candidates export as unknown provenance and
P5 quarantines them, which is the truth about them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_music_attempt_provenance"
down_revision = "0031_qa_detector_catalog"
branch_labels = None
depends_on = None

SCHEMA = "music"


def upgrade() -> None:
    op.add_column("generation_attempts", sa.Column("model_version", sa.String(128), nullable=True),
                  schema=SCHEMA)
    op.add_column("generation_attempts", sa.Column("lyrics_text", sa.Text(), nullable=True),
                  schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("generation_attempts", "lyrics_text", schema=SCHEMA)
    op.drop_column("generation_attempts", "model_version", schema=SCHEMA)
