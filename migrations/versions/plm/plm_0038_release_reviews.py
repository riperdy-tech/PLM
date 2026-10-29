"""platform: Go / No-Go release review registry

Revision ID: 0038_release_reviews
Revises: 0037_dashboard_views
Create Date: 2026-10-21

``ops.release_reviews`` holds the current review status of each reviewable package;
``ops.release_review_events`` is the append-only history (registered, approved, rejected
with its reason, reopened, marked published) so a rejected package can be reconsidered.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_release_reviews"
down_revision = "0037_dashboard_views"
branch_labels = None
depends_on = None

SCHEMA = "ops"


def upgrade() -> None:
    op.create_table(
        "release_reviews",
        sa.Column("package_id", sa.String(64), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("video_ref", sa.Text(), nullable=True),
        sa.Column("thumbnail_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("video_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "release_review_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_release_review_events_package", "release_review_events",
                    ["package_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_release_review_events_package", table_name="release_review_events",
                  schema=SCHEMA)
    op.drop_table("release_review_events", schema=SCHEMA)
    op.drop_table("release_reviews", schema=SCHEMA)
