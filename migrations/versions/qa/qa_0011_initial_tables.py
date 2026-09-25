"""qa schema initial tables (PHASE-P5 §82-§92, Rulings R7, R8).

Revision ID: 0011_qa_initial
Revises: 0010_music_initial
Create Date: 2026-09-25 18:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_qa_initial"
down_revision: str | None = "0010_music_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. candidate_evaluations
    op.create_table(
        "candidate_evaluations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("evaluation_policy_version", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("technical_status", sa.String(32), nullable=False),
        sa.Column("quality_status", sa.String(32), nullable=False),
        sa.Column("provenance_status", sa.String(32), nullable=False),
        sa.Column("rights_status", sa.String(32), nullable=False),
        sa.Column("policy_status", sa.String(32), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=True),
        sa.Column("composite_quality_score", sa.Float(), nullable=True),
        sa.Column("composite_quality_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 2. evaluation_snapshots
    op.create_table(
        "evaluation_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("candidate_snapshot_json", sa.Text(), nullable=False),
        sa.Column("artifact_snapshot_json", sa.Text(), nullable=False),
        sa.Column("provider_snapshot_json", sa.Text(), nullable=False),
        sa.Column("license_snapshot_json", sa.Text(), nullable=False),
        sa.Column("p3_snapshot_ref", sa.String(128), nullable=False),
        sa.Column("p4_snapshot_ref", sa.String(128), nullable=False),
        sa.Column("policy_snapshot_ref", sa.String(128), nullable=False),
        sa.Column("catalog_snapshot_ref", sa.String(128), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 3. technical_checks
    op.create_table(
        "technical_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("check_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("measured_value", sa.String(256), nullable=False),
        sa.Column("expected_value", sa.String(256), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=True),
        sa.Column("end_seconds", sa.Float(), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("config_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 4. musical_checks
    op.create_table(
        "musical_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("check_type", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("segment_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 5. lyric_checks
    op.create_table(
        "lyric_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("check_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 6. provenance_checks
    op.create_table(
        "provenance_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("check_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 7. rights_checks
    op.create_table(
        "rights_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("check_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("matched_reference_id", sa.String(64), nullable=True),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 8. policy_checks
    op.create_table(
        "policy_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("policy_namespace", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 9. risk_findings
    op.create_table(
        "risk_findings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("finding_code", sa.String(64), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=True),
        sa.Column("end_seconds", sa.Float(), nullable=True),
        sa.Column("evidence_item_ids_json", sa.Text(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("repairability", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 10. evidence_items
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_reference", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("raw_pointer", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 11. gate_decisions
    op.create_table(
        "gate_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("gate_results_json", sa.Text(), nullable=False),
        sa.Column("blocking_finding_ids_json", sa.Text(), nullable=False),
        sa.Column("warning_finding_ids_json", sa.Text(), nullable=False),
        sa.Column("decision_version", sa.String(32), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )

    # 12. quarantine_cases
    op.create_table(
        "quarantine_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assigned_to", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(32), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        schema="qa",
    )

    # 13. release_manifests
    op.create_table(
        "release_manifests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("p5_manifest_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("release_manifest_version", sa.String(32), nullable=False),
        sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
        sa.Column("canonical_artifact_id", sa.String(64), nullable=False),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("track_spec_id", sa.String(64), nullable=False),
        sa.Column("content_arm_id", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("technical_status", sa.String(32), nullable=False),
        sa.Column("commercial_use_status", sa.String(32), nullable=False),
        sa.Column("provenance_status", sa.String(32), nullable=False),
        sa.Column("ai_disclosure_required", sa.Boolean(), nullable=False),
        sa.Column("rights_risk", sa.String(32), nullable=False),
        sa.Column("policy_status", sa.String(32), nullable=False),
        sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
        sa.Column("evaluation_policy_version", sa.String(64), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.Column("evidence_bundle_id", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="qa",
    )


def downgrade() -> None:
    op.drop_table("release_manifests", schema="qa")
    op.drop_table("quarantine_cases", schema="qa")
    op.drop_table("gate_decisions", schema="qa")
    op.drop_table("evidence_items", schema="qa")
    op.drop_table("risk_findings", schema="qa")
    op.drop_table("policy_checks", schema="qa")
    op.drop_table("rights_checks", schema="qa")
    op.drop_table("provenance_checks", schema="qa")
    op.drop_table("lyric_checks", schema="qa")
    op.drop_table("musical_checks", schema="qa")
    op.drop_table("technical_checks", schema="qa")
    op.drop_table("evaluation_snapshots", schema="qa")
    op.drop_table("candidate_evaluations", schema="qa")
