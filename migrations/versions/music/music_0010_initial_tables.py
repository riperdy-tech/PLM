"""music schema initial tables (PHASE-P4 §65, Ruling R6).

Revision ID: 0010_music_initial
Revises: 0009_experiments_initial
Create Date: 2026-09-25 15:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_music_initial"
down_revision: str | None = "0009_experiments_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. music_providers
    op.create_table(
        "music_providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("configuration_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="music",
    )

    # 2. music_models
    op.create_table(
        "music_models",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider_id", sa.String(64), nullable=False, index=True),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("pricing_version", sa.String(64), nullable=False),
        sa.Column("rights_policy_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="music",
    )

    # 3. generation_plans
    op.create_table(
        "generation_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("track_spec_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("candidate_target", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("budget_limit", sa.Float(), nullable=False),
        sa.Column("credit_limit", sa.Float(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema="music",
    )

    # 4. creative_variants
    op.create_table(
        "creative_variants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("generation_plan_id", sa.String(64), nullable=False, index=True),
        sa.Column("variant_code", sa.String(32), nullable=False),
        sa.Column("parent_variant_id", sa.String(64), nullable=True),
        sa.Column("changed_dimensions_json", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.String(256), nullable=False),
        sa.Column("prompt_delta_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="music",
    )

    # 5. generation_attempts
    op.create_table(
        "generation_attempts",
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
        sa.Column("cost_estimate", sa.Float(), nullable=False),
        sa.Column("actual_cost", sa.Float(), nullable=False),
        sa.Column("credit_estimate", sa.Float(), nullable=False),
        sa.Column("actual_credits", sa.Float(), nullable=False),
        schema="music",
    )

    # 6. generated_candidates
    op.create_table(
        "generated_candidates",
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
        schema="music",
    )

    # 7. audio_artifacts
    op.create_table(
        "audio_artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("candidate_id", sa.String(64), nullable=False, index=True),
        sa.Column("artifact_type", sa.String(32), nullable=False, index=True),
        sa.Column("object_uri", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("codec", sa.String(32), nullable=False),
        sa.Column("probe_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="music",
    )

    # 8. artifact_lineage
    op.create_table(
        "artifact_lineage",
        sa.Column("parent_artifact_id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("child_artifact_id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("transformation_type", sa.String(32), nullable=False),
        sa.Column("transformation_manifest_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="music",
    )

    # 9. rights_snapshots (including R6 fields)
    op.create_table(
        "rights_snapshots",
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
        schema="music",
    )

    # 10. provider_cost_records
    op.create_table(
        "provider_cost_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("attempt_id", sa.String(64), nullable=False, index=True),
        sa.Column("cost_type", sa.String(32), nullable=False),
        sa.Column("estimated_amount", sa.Float(), nullable=False),
        sa.Column("actual_amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("credits_estimated", sa.Float(), nullable=False),
        sa.Column("credits_actual", sa.Float(), nullable=False),
        sa.Column("pricing_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="music",
    )


def downgrade() -> None:
    op.drop_table("provider_cost_records", schema="music")
    op.drop_table("rights_snapshots", schema="music")
    op.drop_table("artifact_lineage", schema="music")
    op.drop_table("audio_artifacts", schema="music")
    op.drop_table("generated_candidates", schema="music")
    op.drop_table("generation_attempts", schema="music")
    op.drop_table("creative_variants", schema="music")
    op.drop_table("generation_plans", schema="music")
    op.drop_table("music_models", schema="music")
    op.drop_table("music_providers", schema="music")
