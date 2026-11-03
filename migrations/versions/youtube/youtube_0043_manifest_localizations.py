"""youtube: store the localized titles and descriptions a publish sends (ADR-0045).

Revision ID: 0043_youtube_localizations
Revises: 0042_media_render_evidence
Create Date: 2026-11-03

NULL on manifests stored before this revision: they had no localizations.
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_youtube_localizations"
down_revision = "0042_media_render_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publish_manifests",
        sa.Column("localizations_json", sa.JSON(), nullable=True),
        schema="youtube",
    )


def downgrade() -> None:
    op.drop_column("publish_manifests", "localizations_json", schema="youtube")
