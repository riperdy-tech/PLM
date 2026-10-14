"""SQLAlchemy metadata and table schemas for Phase P3 experiments (PHASE-P3 §50–§63)."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

SCHEMA = "experiments"
EXPERIMENTS_METADATA = MetaData(schema=SCHEMA)

# 1. Core Experiments Table (§51)
experiments_table = Table(
    "experiments",
    EXPERIMENTS_METADATA,
    Column("experiment_id", String(64), primary_key=True),
    Column("content_arm_id", String(128), nullable=False, index=True),
    Column("production_directive_id", String(64), nullable=False, index=True),
    Column("experiment_type", String(32), nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("hypothesis_statement", Text, nullable=False),
    Column("target_metric", String(64), nullable=False),
    Column("expected_direction", String(32), nullable=False),
    Column("rationale", Text, nullable=False, default=""),
    Column("base_genome_fingerprint", String(64), nullable=False),
    Column("experiment_version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # 0024 (ADR-0031, R2): nullable only because legacy rows predate them; such rows are
    # refused on load. Every insert writes all three.
    Column("measurement_plan_json", Text, nullable=True),
    Column("budget_limit", Float, nullable=True),
    Column("design_json", Text, nullable=True),
)

# 2. Experiment Versions Table (§52)
experiment_versions_table = Table(
    "experiment_versions",
    EXPERIMENTS_METADATA,
    Column("version_id", String(64), primary_key=True),
    Column(
        "experiment_id",
        String(64),
        ForeignKey("experiments.experiments.experiment_id"),
        nullable=False,
        index=True,
    ),
    Column("version_number", Integer, nullable=False),
    Column("definition_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 3. Experiment Variables Table (§53)
experiment_variables_table = Table(
    "experiment_variables",
    EXPERIMENTS_METADATA,
    Column("variable_id", String(64), primary_key=True),
    Column(
        "experiment_id",
        String(64),
        ForeignKey("experiments.experiments.experiment_id"),
        nullable=False,
        index=True,
    ),
    Column("name", String(64), nullable=False),
    Column("variable_class", String(32), nullable=False),
    Column("baseline_value_json", Text, nullable=False),
    Column("treatment_values_json", Text, nullable=False),
)

# 4. Experiment Treatments Table (§54)
experiment_treatments_table = Table(
    "experiment_treatments",
    EXPERIMENTS_METADATA,
    Column("treatment_id", String(64), primary_key=True),
    Column(
        "experiment_id",
        String(64),
        ForeignKey("experiments.experiments.experiment_id"),
        nullable=False,
        index=True,
    ),
    Column("treatment_index", Integer, nullable=False),
    Column("name", String(64), nullable=False),
    Column("variable_assignments_json", Text, nullable=False),
    Column("genome_delta_json", Text, nullable=False, default="{}"),
)

# 5. Content Genomes Table (§55)
content_genomes_table = Table(
    "content_genomes",
    EXPERIMENTS_METADATA,
    Column("genome_id", String(64), primary_key=True),
    Column("fingerprint", String(64), nullable=False, unique=True, index=True),
    Column("genre", String(64), nullable=False),
    Column("subgenre", String(64), nullable=True),
    Column("dimensions_json", Text, nullable=False),
    Column("constraints_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 6. Content Genome Versions Table (§56)
content_genome_versions_table = Table(
    "content_genome_versions",
    EXPERIMENTS_METADATA,
    Column("genome_version_id", String(64), primary_key=True),
    Column(
        "genome_id",
        String(64),
        ForeignKey("experiments.content_genomes.genome_id"),
        nullable=False,
        index=True,
    ),
    Column("version_number", Integer, nullable=False),
    Column("genome_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 7. Content Packages Table (§57)
content_packages_table = Table(
    "content_packages",
    EXPERIMENTS_METADATA,
    Column("package_id", String(64), primary_key=True),
    Column(
        "experiment_id",
        String(64),
        ForeignKey("experiments.experiments.experiment_id"),
        nullable=False,
        index=True,
    ),
    Column("treatment_id", String(64), nullable=False, index=True),
    Column("brand_id", String(64), nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("package_hash", String(64), nullable=False, index=True),
    Column("budget_envelope", Float, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 8. Track Specs Table (§58)
track_specs_table = Table(
    "track_specs",
    EXPERIMENTS_METADATA,
    Column("track_spec_id", String(64), primary_key=True),
    Column(
        "package_id",
        String(64),
        ForeignKey("experiments.content_packages.package_id"),
        nullable=False,
        index=True,
    ),
    Column("playlist_spec_id", String(64), nullable=False),
    Column("experiment_id", String(64), nullable=False),
    Column("content_arm_id", String(128), nullable=False),
    Column("brand_profile_id", String(64), nullable=False),
    Column("track_role", String(32), nullable=False),
    Column("requested_duration", Integer, nullable=False),
    Column("candidate_target", Integer, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("budget_limit", Float, nullable=False),
    Column("spec_json", Text, nullable=False),
)

# 9. Playlist Specs Table (§59)
playlist_specs_table = Table(
    "playlist_specs",
    EXPERIMENTS_METADATA,
    Column("playlist_spec_id", String(64), primary_key=True),
    Column(
        "package_id",
        String(64),
        ForeignKey("experiments.content_packages.package_id"),
        nullable=False,
        index=True,
    ),
    Column("experiment_id", String(64), nullable=False),
    Column("brand_id", String(64), nullable=False),
    Column("track_count_target", Integer, nullable=False),
    Column("total_duration_minutes", Integer, nullable=False),
    Column("sequencing_profile", String(64), nullable=False),
)

# 10. Visual Specs Table (§60)
visual_specs_table = Table(
    "visual_specs",
    EXPERIMENTS_METADATA,
    Column("visual_spec_id", String(64), primary_key=True),
    Column(
        "package_id",
        String(64),
        ForeignKey("experiments.content_packages.package_id"),
        nullable=False,
        index=True,
    ),
    Column("experiment_id", String(64), nullable=False),
    Column("visual_world", String(64), nullable=False),
    Column("motion_profile", String(64), nullable=False),
    Column("palette_guide_json", Text, nullable=False),
    Column("aspect_ratio", String(16), nullable=False),
)

# 11. Thumbnail Specs Table (§61)
thumbnail_specs_table = Table(
    "thumbnail_specs",
    EXPERIMENTS_METADATA,
    Column("thumbnail_spec_id", String(64), primary_key=True),
    Column(
        "package_id",
        String(64),
        ForeignKey("experiments.content_packages.package_id"),
        nullable=False,
        index=True,
    ),
    Column("experiment_id", String(64), nullable=False),
    Column("thumbnail_family", String(64), nullable=False),
    Column("composition_archetype", String(64), nullable=False),
    Column("text_rules_json", Text, nullable=False),
)

# 12. Title Specs Table (§62)
title_specs_table = Table(
    "title_specs",
    EXPERIMENTS_METADATA,
    Column("title_spec_id", String(64), primary_key=True),
    Column(
        "package_id",
        String(64),
        ForeignKey("experiments.content_packages.package_id"),
        nullable=False,
        index=True,
    ),
    Column("experiment_id", String(64), nullable=False),
    Column("title_archetype", String(64), nullable=False),
    Column("formatting_pattern", String(128), nullable=False),
    Column("character_limit", Integer, nullable=False),
)

# 13. Experiment Events Table (§63)
experiment_events_table = Table(
    "experiment_events",
    EXPERIMENTS_METADATA,
    Column("event_id", String(64), primary_key=True),
    Column("experiment_id", String(64), nullable=False, index=True),
    Column("event_type", String(64), nullable=False, index=True),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

EXPERIMENTS_TABLES = (
    experiments_table,
    experiment_versions_table,
    experiment_variables_table,
    experiment_treatments_table,
    content_genomes_table,
    content_genome_versions_table,
    content_packages_table,
    track_specs_table,
    playlist_specs_table,
    visual_specs_table,
    thumbnail_specs_table,
    title_specs_table,
    experiment_events_table,
)
