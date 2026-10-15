"""platform: the R5 dashboard views need no schema change (documented no-op)

Revision ID: 0037_dashboard_views
Revises: 0036_pipeline_publish_steps
Create Date: 2026-10-15

The live dashboard (``plm_platform.dashboard``) implements the six architecture PART XVII
views by reading what the phases already store: read-only queries over ``ops``
(``pipeline_runs``, ``pipeline_transitions``, ``exception_cases``, ``exception_events``),
``ar``, ``analytics``, ``youtube``, ``distribution`` and ``market``, plus the phase APIs.
Its only writes are exception decisions through ``ExceptionQueue`` into the existing
``ops.exception_cases`` / ``ops.exception_events`` (migration ``0028``). No table, view,
index or grant is added, so this revision changes nothing; it records in the chain that
the dashboard stage was reviewed against the schema at this point.
"""

revision = "0037_dashboard_views"
down_revision = "0036_pipeline_publish_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change: the dashboard reads existing tables."""


def downgrade() -> None:
    """Nothing to undo."""
