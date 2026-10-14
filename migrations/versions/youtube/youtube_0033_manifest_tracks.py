"""youtube schema: publish manifests keep the package's tracklist

Revision ID: 0033_youtube_manifest_tracks
Revises: 0032_music_attempt_provenance
Create Date: 2026-10-15

R4: P7 re-verifies every recording with P5 and the exception queue right before the
upload, so the manifest must keep which recordings (candidate id, artifact id) the video
carries. Manifests stored earlier have no tracklist (NULL) and are never cleared.
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_youtube_manifest_tracks"
down_revision = "0032_music_attempt_provenance"
branch_labels = None
depends_on = None

SCHEMA = "youtube"


def upgrade() -> None:
    op.add_column(
        "publish_manifests", sa.Column("tracks_json", sa.JSON(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("publish_manifests", "tracks_json", schema=SCHEMA)
