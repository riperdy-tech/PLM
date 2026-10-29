"""youtube: persist immutable publish candidate assignment and eligible candidate snapshot.

Revision ID: 0038_youtube_candidate_selection
Revises: 0037_dashboard_views
Create Date: 2026-10-24

NULL indicates historical manifests whose exposure choice cannot be reconstructed.
No existing choice is retroactively attributed or labeled as an A/B result.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_youtube_candidate_selection"
down_revision = "0037_dashboard_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publish_manifests",
        sa.Column("selection_json", sa.JSON(), nullable=True),
        schema="youtube",
    )


def downgrade() -> None:
    op.drop_column("publish_manifests", "selection_json", schema="youtube")
