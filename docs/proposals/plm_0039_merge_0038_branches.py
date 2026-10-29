"""REVIEW-ONLY DRAFT — NOT INSTALLED IN THE ACTIVE MIGRATION CHAIN.

Illustrates a genuine Alembic merge point preserving both independently
applied 0038 revisions. Do not copy into migrations/versions or run it
without owner approval, an ADR and isolated PostgreSQL recovery testing.

Revision ID: 0039_merge_0038_branches
Revises: 0038_release_reviews, 0038_youtube_candidate_selection

This is a graph merge, not a schema patch. The respective parent upgrades
must have been applied to reach this node; their histories are not rewritten.
"""

revision = "0039_merge_0038_branches"
down_revision = ("0038_release_reviews", "0038_youtube_candidate_selection")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No SQL; both parent migrations own their separate schema changes."""


def downgrade() -> None:
    """No SQL; individual parent revisions own their schema reversals."""
