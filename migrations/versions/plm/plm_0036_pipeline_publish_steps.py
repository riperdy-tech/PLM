"""platform: the pipeline's publishing, measurement and learning steps (R5) - no schema change

Revision ID: 0036_pipeline_publish_steps
Revises: 0035_idempotency_records
Create Date: 2026-10-15

A documented no-op that marks the R5 pipeline steps in the chain. They need no new
table or column:

* the steps' inputs, outputs, guard receipts and costs are recorded in the existing
  ``ops.pipeline_transitions`` JSON columns (migration ``0030``);
* the run's spend is read from the existing ``ops.budget_entries`` (``0027``), where the
  phases' write guards reserve and commit; the pipeline itself no longer reserves;
* P9 stores mock-sourced video metrics in the existing ``analytics.video_metrics.source``
  as ``synthetic`` and statement lines under the provider ``mock_distributor``;
* the new hold codes (``PLAYLIST_SUPPLY_SHORT``, ``EXTERNAL_WRITE_NOT_AUTHORIZED``) are
  policy data (``hold_classes.json`` v5), read when a case is raised.
"""

revision = "0036_pipeline_publish_steps"
down_revision = "0035_idempotency_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema change (see the module docstring)."""


def downgrade() -> None:
    """No schema change to revert."""
