"""Persist render recovery evidence instead of trusting caller booleans (B02).

Revision ID: 0042_media_render_evidence
Revises: 0041_p4_credit_provenance

CODE ONLY (ADR-0043). Apply solely to a separately approved target. The new
table is append-only; downgrade refuses while any evidence row exists, because
dropping it would silently remove the record a quarantine or retry relied on.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_media_render_evidence"
down_revision = "0041_p4_credit_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "render_recovery_evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("render_job_id", sa.String(64), nullable=False, index=True),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_manifest_hash", sa.String(64), nullable=False),
        sa.Column("evidence_ref", sa.Text, nullable=True),
        sa.Column("detail_json", sa.JSON, nullable=False),
        sa.Column("recorded_by", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "render_job_id", "evidence_kind",
            name="uq_render_recovery_evidence_job_kind",
        ),
        schema="media",
    )


def downgrade() -> None:
    remaining = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM media.render_recovery_evidence"
    )).scalar_one()
    if remaining:
        raise RuntimeError(
            "cannot downgrade: render recovery evidence would be destroyed"
        )
    op.drop_table("render_recovery_evidence", schema="media")
