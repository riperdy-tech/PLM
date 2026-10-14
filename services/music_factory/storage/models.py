"""SQLAlchemy metadata and table declarations for schema `music` (PHASE-P4 §65, R6)."""

from __future__ import annotations

import sqlalchemy as sa

MUSIC_METADATA = sa.MetaData(schema="music")

music_providers_table = sa.Table(
    "music_providers",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("provider_id", sa.String(64), nullable=False, unique=True),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("configuration_version", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

music_models_table = sa.Table(
    "music_models",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("provider_id", sa.String(64), nullable=False, index=True),
    sa.Column("model_id", sa.String(64), nullable=False),
    sa.Column("model_version", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("capabilities_json", sa.Text, nullable=False),
    sa.Column("pricing_version", sa.String(64), nullable=False),
    sa.Column("rights_policy_version", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

generation_plans_table = sa.Table(
    "generation_plans",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("track_spec_id", sa.String(64), nullable=False, index=True),
    sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
    # ExperimentLineage as JSON; NULL only for plans stored before ADR-0026.
    sa.Column("lineage_json", sa.JSON, nullable=True),
    sa.Column("candidate_target", sa.Integer, nullable=False),
    sa.Column("max_attempts", sa.Integer, nullable=False),
    sa.Column("budget_limit", sa.Float, nullable=False),
    sa.Column("credit_limit", sa.Float, nullable=False),
    sa.Column("policy_version", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
)

creative_variants_table = sa.Table(
    "creative_variants",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("generation_plan_id", sa.String(64), nullable=False, index=True),
    sa.Column("variant_code", sa.String(32), nullable=False),
    sa.Column("parent_variant_id", sa.String(64), nullable=True),
    sa.Column("changed_dimensions_json", sa.Text, nullable=False),
    sa.Column("change_reason", sa.String(256), nullable=False),
    sa.Column("prompt_delta_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

generation_attempts_table = sa.Table(
    "generation_attempts",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("generation_plan_id", sa.String(64), nullable=False, index=True),
    sa.Column("creative_variant_id", sa.String(64), nullable=False, index=True),
    sa.Column("provider_id", sa.String(64), nullable=False, index=True),
    sa.Column("model_id", sa.String(64), nullable=False),
    sa.Column("provider_job_id", sa.String(128), nullable=False, index=True),
    sa.Column("attempt_type", sa.String(32), nullable=False),
    sa.Column("transport_retry_of", sa.String(64), nullable=True),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("request_payload_hash", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("failure_code", sa.String(64), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cost_estimate", sa.Float, nullable=False),
    sa.Column("actual_cost", sa.Float, nullable=False),
    sa.Column("credit_estimate", sa.Float, nullable=False),
    sa.Column("actual_credits", sa.Float, nullable=False),
)

generated_candidates_table = sa.Table(
    "generated_candidates",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("track_spec_id", sa.String(64), nullable=False, index=True),
    sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
    sa.Column("generation_plan_id", sa.String(64), nullable=False, index=True),
    sa.Column("parent_candidate_id", sa.String(64), nullable=True),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("provider_id", sa.String(64), nullable=False),
    sa.Column("model_id", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

audio_artifacts_table = sa.Table(
    "audio_artifacts",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
    sa.Column("artifact_type", sa.String(32), nullable=False, index=True),
    sa.Column("object_uri", sa.String(512), nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False, index=True),
    sa.Column("mime_type", sa.String(64), nullable=False),
    sa.Column("byte_size", sa.BigInteger, nullable=False),
    sa.Column("duration_seconds", sa.Float, nullable=False),
    sa.Column("sample_rate", sa.Integer, nullable=False),
    sa.Column("channels", sa.Integer, nullable=False),
    sa.Column("codec", sa.String(32), nullable=False),
    sa.Column("probe_version", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

artifact_lineage_table = sa.Table(
    "artifact_lineage",
    MUSIC_METADATA,
    sa.Column("parent_artifact_id", sa.String(64), nullable=False, primary_key=True),
    sa.Column("child_artifact_id", sa.String(64), nullable=False, primary_key=True),
    sa.Column("transformation_type", sa.String(32), nullable=False),
    sa.Column("transformation_manifest_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

rights_snapshots_table = sa.Table(
    "rights_snapshots",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("attempt_id", sa.String(64), nullable=False, index=True),
    sa.Column("provider_id", sa.String(64), nullable=False, index=True),
    sa.Column("commercial_use_status", sa.String(32), nullable=False, index=True),
    sa.Column("basis", sa.String(128), nullable=False),
    sa.Column("license_reference", sa.String(256), nullable=False),
    sa.Column("policy_version", sa.String(64), nullable=False),
    sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("plan_or_tier", sa.String(64), nullable=False),
    sa.Column("account_identity_ref", sa.String(128), nullable=False),
    sa.Column("terms_version", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False),
)

provider_cost_records_table = sa.Table(
    "provider_cost_records",
    MUSIC_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("attempt_id", sa.String(64), nullable=False, index=True),
    sa.Column("cost_type", sa.String(32), nullable=False),
    sa.Column("estimated_amount", sa.Float, nullable=False),
    sa.Column("actual_amount", sa.Float, nullable=False),
    sa.Column("currency", sa.String(8), nullable=False),
    sa.Column("credits_estimated", sa.Float, nullable=False),
    sa.Column("credits_actual", sa.Float, nullable=False),
    sa.Column("pricing_version", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
