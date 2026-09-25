# migrations/versions/experiments/experiments_0009_initial_tables.py
"""P3 initial tables — the `experiments` schema.

Revision ID: 0009_experiments_initial
Revises: 0008_ar_initial
Create Date: 2026-09-25

Creates all 13 tables for the experiment framework in schema `experiments` (§50–§63).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_experiments_initial"
down_revision = "0008_ar_initial"
branch_labels = None
depends_on = None

SCHEMA = "experiments"
_ts = sa.DateTime(timezone=True)


def upgrade() -> None:
    # 1. experiments
    op.create_table(
        "experiments",
        sa.Column("experiment_id", sa.String(64), primary_key=True),
        sa.Column("content_arm_id", sa.String(128), nullable=False, index=True),
        sa.Column("production_directive_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("hypothesis_statement", sa.Text, nullable=False),
        sa.Column("target_metric", sa.String(64), nullable=False),
        sa.Column("expected_direction", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False, server_default=""),
        sa.Column("base_genome_fingerprint", sa.String(64), nullable=False),
        sa.Column("experiment_version", sa.String(32), nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("updated_at", _ts, nullable=False),
        schema=SCHEMA,
    )

    # 2. experiment_versions
    op.create_table(
        "experiment_versions",
        sa.Column("version_id", sa.String(64), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.experiments.experiment_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("definition_json", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        schema=SCHEMA,
    )

    # 3. experiment_variables
    op.create_table(
        "experiment_variables",
        sa.Column("variable_id", sa.String(64), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.experiments.experiment_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("variable_class", sa.String(32), nullable=False),
        sa.Column("baseline_value_json", sa.Text, nullable=False),
        sa.Column("treatment_values_json", sa.Text, nullable=False),
        schema=SCHEMA,
    )

    # 4. experiment_treatments
    op.create_table(
        "experiment_treatments",
        sa.Column("treatment_id", sa.String(64), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.experiments.experiment_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("treatment_index", sa.Integer, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("variable_assignments_json", sa.Text, nullable=False),
        sa.Column("genome_delta_json", sa.Text, nullable=False, server_default="{}"),
        schema=SCHEMA,
    )

    # 5. content_genomes
    op.create_table(
        "content_genomes",
        sa.Column("genome_id", sa.String(64), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("genre", sa.String(64), nullable=False),
        sa.Column("subgenre", sa.String(64), nullable=True),
        sa.Column("dimensions_json", sa.Text, nullable=False),
        sa.Column("constraints_json", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        schema=SCHEMA,
    )

    # 6. content_genome_versions
    op.create_table(
        "content_genome_versions",
        sa.Column("genome_version_id", sa.String(64), primary_key=True),
        sa.Column(
            "genome_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.content_genomes.genome_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("genome_json", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        schema=SCHEMA,
    )

    # 7. content_packages
    op.create_table(
        "content_packages",
        sa.Column("package_id", sa.String(64), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.experiments.experiment_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("treatment_id", sa.String(64), nullable=False, index=True),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("package_hash", sa.String(64), nullable=False, index=True),
        sa.Column("budget_envelope", sa.Float, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        schema=SCHEMA,
    )

    # 8. track_specs
    op.create_table(
        "track_specs",
        sa.Column("track_spec_id", sa.String(64), primary_key=True),
        sa.Column(
            "package_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.content_packages.package_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("playlist_spec_id", sa.String(64), nullable=False),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("content_arm_id", sa.String(128), nullable=False),
        sa.Column("brand_profile_id", sa.String(64), nullable=False),
        sa.Column("track_role", sa.String(32), nullable=False),
        sa.Column("requested_duration", sa.Integer, nullable=False),
        sa.Column("candidate_target", sa.Integer, nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("budget_limit", sa.Float, nullable=False),
        sa.Column("spec_json", sa.Text, nullable=False),
        schema=SCHEMA,
    )

    # 9. playlist_specs
    op.create_table(
        "playlist_specs",
        sa.Column("playlist_spec_id", sa.String(64), primary_key=True),
        sa.Column(
            "package_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.content_packages.package_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("track_count_target", sa.Integer, nullable=False),
        sa.Column("total_duration_minutes", sa.Integer, nullable=False),
        sa.Column("sequencing_profile", sa.String(64), nullable=False),
        schema=SCHEMA,
    )

    # 10. visual_specs
    op.create_table(
        "visual_specs",
        sa.Column("visual_spec_id", sa.String(64), primary_key=True),
        sa.Column(
            "package_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.content_packages.package_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("visual_world", sa.String(64), nullable=False),
        sa.Column("motion_profile", sa.String(64), nullable=False),
        sa.Column("palette_guide_json", sa.Text, nullable=False),
        sa.Column("aspect_ratio", sa.String(16), nullable=False),
        schema=SCHEMA,
    )

    # 11. thumbnail_specs
    op.create_table(
        "thumbnail_specs",
        sa.Column("thumbnail_spec_id", sa.String(64), primary_key=True),
        sa.Column(
            "package_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.content_packages.package_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("thumbnail_family", sa.String(64), nullable=False),
        sa.Column("composition_archetype", sa.String(64), nullable=False),
        sa.Column("text_rules_json", sa.Text, nullable=False),
        schema=SCHEMA,
    )

    # 12. title_specs
    op.create_table(
        "title_specs",
        sa.Column("title_spec_id", sa.String(64), primary_key=True),
        sa.Column(
            "package_id",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.content_packages.package_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("title_archetype", sa.String(64), nullable=False),
        sa.Column("formatting_pattern", sa.String(128), nullable=False),
        sa.Column("character_limit", sa.Integer, nullable=False),
        schema=SCHEMA,
    )

    # 13. experiment_events
    op.create_table(
        "experiment_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("experiment_events", schema=SCHEMA)
    op.drop_table("title_specs", schema=SCHEMA)
    op.drop_table("thumbnail_specs", schema=SCHEMA)
    op.drop_table("visual_specs", schema=SCHEMA)
    op.drop_table("playlist_specs", schema=SCHEMA)
    op.drop_table("track_specs", schema=SCHEMA)
    op.drop_table("content_packages", schema=SCHEMA)
    op.drop_table("content_genome_versions", schema=SCHEMA)
    op.drop_table("content_genomes", schema=SCHEMA)
    op.drop_table("experiment_treatments", schema=SCHEMA)
    op.drop_table("experiment_variables", schema=SCHEMA)
    op.drop_table("experiment_versions", schema=SCHEMA)
    op.drop_table("experiments", schema=SCHEMA)
