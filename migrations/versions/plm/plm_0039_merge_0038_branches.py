"""Merge the two independently-applied 0038 revisions (code only).

Revision ID: 0039_merge_0038_branches
Revises: 0038_release_reviews, 0038_youtube_candidate_selection

This graph merge contains no SQL. Each historical parent owns its own upgrade
and downgrade operations. Installing this file is NOT approval to migrate a
shared database; see docs/runbooks/reconcile-alembic-0038.md.
"""

revision = "0039_merge_0038_branches"
down_revision = ("0038_release_reviews", "0038_youtube_candidate_selection")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No SQL; both parent migrations own their separate schema changes."""


def downgrade() -> None:
    """No SQL; individual parent revisions own their schema reversals."""