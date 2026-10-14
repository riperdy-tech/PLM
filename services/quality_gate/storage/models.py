"""SQLAlchemy metadata and table declarations for schema `qa` (PHASE-P5 §82-§92, R7, R8)."""

from __future__ import annotations

import sqlalchemy as sa

QA_METADATA = sa.MetaData(schema="qa")

candidate_evaluations_table = sa.Table(
    "candidate_evaluations",
    QA_METADATA,
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
    sa.Column("composite_quality_score", sa.Float, nullable=True),
    sa.Column("composite_quality_confidence", sa.Float, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

evaluation_snapshots_table = sa.Table(
    "evaluation_snapshots",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("candidate_id", sa.String(64), nullable=False),
    sa.Column("artifact_id", sa.String(64), nullable=False),
    sa.Column("candidate_snapshot_json", sa.Text, nullable=False),
    sa.Column("artifact_snapshot_json", sa.Text, nullable=False),
    sa.Column("provider_snapshot_json", sa.Text, nullable=False),
    sa.Column("license_snapshot_json", sa.Text, nullable=False),
    sa.Column("p3_snapshot_ref", sa.String(128), nullable=False),
    sa.Column("p4_snapshot_ref", sa.String(128), nullable=False),
    sa.Column("policy_snapshot_ref", sa.String(128), nullable=False),
    sa.Column("catalog_snapshot_ref", sa.String(128), nullable=False),
    sa.Column("snapshot_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

technical_checks_table = sa.Table(
    "technical_checks",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("check_type", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("severity", sa.String(32), nullable=False),
    sa.Column("measured_value", sa.String(256), nullable=False),
    sa.Column("expected_value", sa.String(256), nullable=False),
    sa.Column("unit", sa.String(32), nullable=False),
    sa.Column("start_seconds", sa.Float, nullable=True),
    sa.Column("end_seconds", sa.Float, nullable=True),
    sa.Column("tool_name", sa.String(64), nullable=False),
    sa.Column("tool_version", sa.String(32), nullable=False),
    sa.Column("config_version", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

musical_checks_table = sa.Table(
    "musical_checks",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("check_type", sa.String(64), nullable=False),
    sa.Column("score", sa.Float, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("severity", sa.String(32), nullable=False),
    sa.Column("method", sa.String(64), nullable=False),
    sa.Column("model_version", sa.String(32), nullable=False),
    sa.Column("segment_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

lyric_checks_table = sa.Table(
    "lyric_checks",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("check_type", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("severity", sa.String(32), nullable=False),
    sa.Column("details_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

provenance_checks_table = sa.Table(
    "provenance_checks",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("check_type", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("severity", sa.String(32), nullable=False),
    sa.Column("details_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

rights_checks_table = sa.Table(
    "rights_checks",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("check_type", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("risk_level", sa.String(32), nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("source_type", sa.String(64), nullable=False),
    sa.Column("source_ref", sa.String(128), nullable=False),
    sa.Column("matched_reference_id", sa.String(64), nullable=True),
    sa.Column("similarity_score", sa.Float, nullable=False),
    sa.Column("evidence_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

policy_checks_table = sa.Table(
    "policy_checks",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("policy_namespace", sa.String(64), nullable=False),
    sa.Column("rule_id", sa.String(64), nullable=False),
    sa.Column("rule_version", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("severity", sa.String(32), nullable=False),
    sa.Column("evidence_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

risk_findings_table = sa.Table(
    "risk_findings",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("finding_code", sa.String(64), nullable=False),
    sa.Column("category", sa.String(64), nullable=False),
    sa.Column("severity", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("start_seconds", sa.Float, nullable=True),
    sa.Column("end_seconds", sa.Float, nullable=True),
    sa.Column("evidence_item_ids_json", sa.Text, nullable=False),
    sa.Column("blocking", sa.Boolean, nullable=False),
    sa.Column("repairability", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

evidence_items_table = sa.Table(
    "evidence_items",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("evidence_type", sa.String(64), nullable=False),
    sa.Column("source", sa.String(64), nullable=False),
    sa.Column("source_reference", sa.String(128), nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("raw_pointer", sa.String(256), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

gate_decisions_table = sa.Table(
    "gate_decisions",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("disposition", sa.String(32), nullable=False),
    sa.Column("gate_results_json", sa.Text, nullable=False),
    sa.Column("blocking_finding_ids_json", sa.Text, nullable=False),
    sa.Column("warning_finding_ids_json", sa.Text, nullable=False),
    sa.Column("decision_version", sa.String(32), nullable=False),
    sa.Column("decision_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

quarantine_cases_table = sa.Table(
    "quarantine_cases",
    QA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("reason_codes_json", sa.Text, nullable=False),
    sa.Column("priority", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("assigned_to", sa.String(64), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("resolution", sa.String(32), nullable=True),
    sa.Column("resolution_notes", sa.Text, nullable=True),
)

release_manifests_table = sa.Table(
    "release_manifests",
    QA_METADATA,
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
    sa.Column("ai_disclosure_required", sa.Boolean, nullable=False),
    sa.Column("rights_risk", sa.String(32), nullable=False),
    sa.Column("policy_status", sa.String(32), nullable=False),
    sa.Column("evaluation_id", sa.String(64), nullable=False, index=True),
    sa.Column("evaluation_policy_version", sa.String(64), nullable=False),
    sa.Column("warning_codes_json", sa.Text, nullable=False),
    sa.Column("evidence_bundle_id", sa.String(64), nullable=False),
    sa.Column("manifest_hash", sa.String(64), nullable=False),
    # ADR-0028; NULL only on manifests stored before it (never served on C5).
    sa.Column("lineage_json", sa.JSON, nullable=True),
    sa.Column("artifact_uri", sa.String(512), nullable=True),
    sa.Column("artifact_sha256", sa.String(64), nullable=True),
    sa.Column("measured_duration_seconds", sa.Float, nullable=True),
    sa.Column("override_json", sa.JSON, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
