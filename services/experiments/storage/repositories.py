"""Repository access functions for the `experiments` schema (PHASE-P3 §50–§63)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..domain.enums import (
    ExperimentStatus,
    ExperimentType,
    PackageStatus,
    VariableClass,
)
from ..domain.experiment import (
    Experiment,
    ExperimentVariable,
    Hypothesis,
    Treatment,
)
from ..domain.genome import ConstraintEnvelope, ContentGenome
from ..domain.package import (
    ContentPackage,
    PlaylistSpec,
    ThumbnailSpec,
    TitleSpec,
    TrackSpec,
    VisualSpec,
)
from .models import (
    content_genome_versions_table,
    content_genomes_table,
    content_packages_table,
    experiment_events_table,
    experiment_treatments_table,
    experiment_variables_table,
    experiment_versions_table,
    experiments_table,
    playlist_specs_table,
    thumbnail_specs_table,
    title_specs_table,
    track_specs_table,
    visual_specs_table,
)


class ExperimentNotFoundError(Exception):
    """Raised when an experiment ID cannot be resolved."""


class PackageNotFoundError(Exception):
    """Raised when a content package ID cannot be resolved."""


def insert_genome(
    session: Session,
    genome: ContentGenome,
    *,
    genome_id: str,
    envelope: ConstraintEnvelope | None = None,
    created_at: datetime,
) -> str:
    """Store a ContentGenome and its first version immutably."""
    from ..domain.fingerprint import compute_genome_fingerprint

    now = created_at
    fp = compute_genome_fingerprint(genome)
    env = envelope or ConstraintEnvelope()

    # Check for existing identical genome (idempotent)
    existing = session.execute(
        sa.select(content_genomes_table.c.genome_id).where(
            content_genomes_table.c.fingerprint == fp
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing)

    dumped = genome.model_dump(mode="json")
    session.execute(
        sa.insert(content_genomes_table).values(
            genome_id=genome_id,
            fingerprint=fp,
            genre=genome.genre,
            subgenre=genome.subgenre,
            dimensions_json=json.dumps(dumped, sort_keys=True),
            constraints_json=json.dumps(env.model_dump(mode="json"), sort_keys=True),
            created_at=now,
        )
    )

    session.execute(
        sa.insert(content_genome_versions_table).values(
            genome_version_id=f"{genome_id}_v1",
            genome_id=genome_id,
            version_number=1,
            genome_json=json.dumps(dumped, sort_keys=True),
            created_at=now,
        )
    )
    return genome_id


def get_genome_by_fingerprint(session: Session, fingerprint: str) -> ContentGenome | None:
    """Retrieve a ContentGenome by its SHA256 fingerprint."""
    row = session.execute(
        sa.select(content_genomes_table.c.dimensions_json).where(
            content_genomes_table.c.fingerprint == fingerprint
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    data = json.loads(row)
    return ContentGenome.model_validate(data)


def insert_experiment(session: Session, experiment: Experiment) -> None:
    """Insert an experiment and all associated variables and treatments."""
    now = experiment.created_at
    session.execute(
        sa.insert(experiments_table).values(
            experiment_id=experiment.experiment_id,
            content_arm_id=experiment.content_arm_id,
            production_directive_id=experiment.production_directive_id,
            experiment_type=experiment.experiment_type.value,
            status=experiment.status.value,
            hypothesis_statement=experiment.hypothesis.statement,
            target_metric=experiment.hypothesis.target_metric,
            expected_direction=experiment.hypothesis.expected_direction,
            rationale=experiment.hypothesis.rationale,
            base_genome_fingerprint=experiment.base_genome_fingerprint,
            experiment_version=experiment.experiment_version,
            created_at=now,
            updated_at=now,
        )
    )

    # Initial definition snapshot
    exp_dump = experiment.model_dump(mode="json")
    session.execute(
        sa.insert(experiment_versions_table).values(
            version_id=f"{experiment.experiment_id}_v1",
            experiment_id=experiment.experiment_id,
            version_number=1,
            definition_json=json.dumps(exp_dump, sort_keys=True),
            created_at=now,
        )
    )

    # Variables
    for idx, var in enumerate(experiment.variables):
        session.execute(
            sa.insert(experiment_variables_table).values(
                variable_id=f"{experiment.experiment_id}_var_{idx}",
                experiment_id=experiment.experiment_id,
                name=var.name,
                variable_class=var.variable_class.value,
                baseline_value_json=json.dumps(var.baseline_value, sort_keys=True),
                treatment_values_json=json.dumps(var.treatment_values, sort_keys=True),
            )
        )

    # Treatments
    for trt in experiment.treatments:
        session.execute(
            sa.insert(experiment_treatments_table).values(
                treatment_id=trt.treatment_id,
                experiment_id=experiment.experiment_id,
                treatment_index=trt.treatment_index,
                name=trt.name,
                variable_assignments_json=json.dumps(trt.variable_assignments, sort_keys=True),
                genome_delta_json=json.dumps(trt.genome_delta, sort_keys=True),
            )
        )


def get_experiment(session: Session, experiment_id: str) -> Experiment | None:
    """Load an Experiment definition from the database."""
    row = session.execute(
        sa.select(experiments_table).where(experiments_table.c.experiment_id == experiment_id)
    ).mappings().one_or_none()
    if row is None:
        return None

    # Load variables
    var_rows = session.execute(
        sa.select(experiment_variables_table).where(
            experiment_variables_table.c.experiment_id == experiment_id
        )
    ).mappings().all()
    variables = tuple(
        ExperimentVariable(
            name=v["name"],
            variable_class=VariableClass(v["variable_class"]),
            baseline_value=json.loads(v["baseline_value_json"]),
            treatment_values=tuple(json.loads(v["treatment_values_json"])),
        )
        for v in var_rows
    )

    # Load treatments
    trt_rows = session.execute(
        sa.select(experiment_treatments_table)
        .where(experiment_treatments_table.c.experiment_id == experiment_id)
        .order_by(experiment_treatments_table.c.treatment_index)
    ).mappings().all()
    treatments = tuple(
        Treatment(
            treatment_id=t["treatment_id"],
            treatment_index=t["treatment_index"],
            name=t["name"],
            variable_assignments=json.loads(t["variable_assignments_json"]),
            genome_delta=json.loads(t["genome_delta_json"]),
        )
        for t in trt_rows
    )

    hyp = Hypothesis(
        statement=row["hypothesis_statement"],
        target_metric=row["target_metric"],
        expected_direction=row["expected_direction"],
        rationale=row["rationale"],
    )

    return Experiment(
        experiment_id=row["experiment_id"],
        content_arm_id=row["content_arm_id"],
        production_directive_id=row["production_directive_id"],
        experiment_type=ExperimentType(row["experiment_type"]),
        status=ExperimentStatus(row["status"]),
        hypothesis=hyp,
        variables=variables,
        treatments=treatments,
        base_genome_fingerprint=row["base_genome_fingerprint"],
        experiment_version=row["experiment_version"],
        created_at=row["created_at"],
    )


def list_experiments(
    session: Session, limit: int = 50, offset: int = 0
) -> list[Experiment]:
    """List experiments ordered by creation time descending."""
    exp_ids = session.execute(
        sa.select(experiments_table.c.experiment_id)
        .order_by(experiments_table.c.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()
    results: list[Experiment] = []
    for eid in exp_ids:
        exp = get_experiment(session, str(eid))
        if exp is not None:
            results.append(exp)
    return results


def update_experiment_status(
    session: Session, experiment_id: str, status: ExperimentStatus, *, now: datetime
) -> None:
    """Update status of an experiment."""
    from sqlalchemy.engine import CursorResult

    result = session.execute(
        sa.update(experiments_table)
        .where(experiments_table.c.experiment_id == experiment_id)
        .values(status=status.value, updated_at=now)
    )
    if isinstance(result, CursorResult) and result.rowcount == 0:
        raise ExperimentNotFoundError(f"Experiment {experiment_id} not found")


def insert_content_package(session: Session, package: ContentPackage) -> None:
    """Insert a ContentPackage and all its associated component specs."""
    now = package.created_at
    session.execute(
        sa.insert(content_packages_table).values(
            package_id=package.package_id,
            experiment_id=package.experiment_id,
            treatment_id=package.treatment_id,
            brand_id=package.brand_id,
            status=package.status.value,
            package_hash=package.package_hash,
            budget_envelope=package.budget_envelope,
            created_at=now,
        )
    )

    # Track specs
    for trk in package.track_specs:
        spec_content = trk.to_wire()
        session.execute(
            sa.insert(track_specs_table).values(
                track_spec_id=trk.track_spec_id,
                package_id=package.package_id,
                playlist_spec_id=trk.playlist_spec_id,
                experiment_id=trk.lineage.experiment_id,
                content_arm_id=trk.lineage.content_arm_id,
                brand_profile_id=trk.brand_profile_id,
                track_role=trk.track_role.value,
                requested_duration=trk.requested_duration,
                candidate_target=trk.candidate_target,
                priority=trk.priority,
                budget_limit=trk.budget_limit,
                spec_json=json.dumps(spec_content, sort_keys=True),
            )
        )

    # Playlist spec
    session.execute(
        sa.insert(playlist_specs_table).values(
            playlist_spec_id=package.playlist_spec.playlist_spec_id,
            package_id=package.package_id,
            experiment_id=package.playlist_spec.experiment_id,
            brand_id=package.playlist_spec.brand_id,
            track_count_target=package.playlist_spec.track_count_target,
            total_duration_minutes=package.playlist_spec.total_duration_minutes,
            sequencing_profile=package.playlist_spec.sequencing_profile,
        )
    )

    # Visual spec
    session.execute(
        sa.insert(visual_specs_table).values(
            visual_spec_id=package.visual_spec.visual_spec_id,
            package_id=package.package_id,
            experiment_id=package.visual_spec.experiment_id,
            visual_world=package.visual_spec.visual_world,
            motion_profile=package.visual_spec.motion_profile,
            palette_guide_json=json.dumps(package.visual_spec.palette_guide, sort_keys=True),
            aspect_ratio=package.visual_spec.aspect_ratio,
        )
    )

    # Thumbnail spec
    session.execute(
        sa.insert(thumbnail_specs_table).values(
            thumbnail_spec_id=package.thumbnail_spec.thumbnail_spec_id,
            package_id=package.package_id,
            experiment_id=package.thumbnail_spec.experiment_id,
            thumbnail_family=package.thumbnail_spec.thumbnail_family,
            composition_archetype=package.thumbnail_spec.composition_archetype,
            text_rules_json=json.dumps(package.thumbnail_spec.text_rules, sort_keys=True),
        )
    )

    # Title spec
    session.execute(
        sa.insert(title_specs_table).values(
            title_spec_id=package.title_spec.title_spec_id,
            package_id=package.package_id,
            experiment_id=package.title_spec.experiment_id,
            title_archetype=package.title_spec.title_archetype,
            formatting_pattern=package.title_spec.formatting_pattern,
            character_limit=package.title_spec.character_limit,
        )
    )


def get_content_package(session: Session, package_id: str) -> ContentPackage | None:
    """Load a ContentPackage and its full component specs from database."""
    pkg_row = session.execute(
        sa.select(content_packages_table).where(content_packages_table.c.package_id == package_id)
    ).mappings().one_or_none()
    if pkg_row is None:
        return None

    # Track specs
    trk_rows = session.execute(
        sa.select(track_specs_table).where(track_specs_table.c.package_id == package_id)
    ).mappings().all()
    # spec_json holds the full C3 wire the producer emitted; read it back verbatim.
    track_specs = tuple(TrackSpec.model_validate(json.loads(t["spec_json"])) for t in trk_rows)

    # Playlist spec
    ply_row = session.execute(
        sa.select(playlist_specs_table).where(playlist_specs_table.c.package_id == package_id)
    ).mappings().one()
    ply = PlaylistSpec(
        playlist_spec_id=ply_row["playlist_spec_id"],
        experiment_id=ply_row["experiment_id"],
        brand_id=ply_row["brand_id"],
        track_count_target=ply_row["track_count_target"],
        total_duration_minutes=ply_row["total_duration_minutes"],
        sequencing_profile=ply_row["sequencing_profile"],
    )

    # Visual spec
    vis_row = session.execute(
        sa.select(visual_specs_table).where(visual_specs_table.c.package_id == package_id)
    ).mappings().one()
    vis = VisualSpec(
        visual_spec_id=vis_row["visual_spec_id"],
        experiment_id=vis_row["experiment_id"],
        visual_world=vis_row["visual_world"],
        motion_profile=vis_row["motion_profile"],
        palette_guide=tuple(json.loads(vis_row["palette_guide_json"])),
        aspect_ratio=vis_row["aspect_ratio"],
    )

    # Thumbnail spec
    thb_row = session.execute(
        sa.select(thumbnail_specs_table).where(thumbnail_specs_table.c.package_id == package_id)
    ).mappings().one()
    thb = ThumbnailSpec(
        thumbnail_spec_id=thb_row["thumbnail_spec_id"],
        experiment_id=thb_row["experiment_id"],
        thumbnail_family=thb_row["thumbnail_family"],
        composition_archetype=thb_row["composition_archetype"],
        text_rules=json.loads(thb_row["text_rules_json"]),
    )

    # Title spec
    ttl_row = session.execute(
        sa.select(title_specs_table).where(title_specs_table.c.package_id == package_id)
    ).mappings().one()
    ttl = TitleSpec(
        title_spec_id=ttl_row["title_spec_id"],
        experiment_id=ttl_row["experiment_id"],
        title_archetype=ttl_row["title_archetype"],
        formatting_pattern=ttl_row["formatting_pattern"],
        character_limit=ttl_row["character_limit"],
    )

    return ContentPackage(
        package_id=pkg_row["package_id"],
        experiment_id=pkg_row["experiment_id"],
        treatment_id=pkg_row["treatment_id"],
        brand_id=pkg_row["brand_id"],
        status=PackageStatus(pkg_row["status"]),
        track_specs=track_specs,
        playlist_spec=ply,
        visual_spec=vis,
        thumbnail_spec=thb,
        title_spec=ttl,
        package_hash=pkg_row["package_hash"],
        budget_envelope=pkg_row["budget_envelope"],
        created_at=pkg_row["created_at"],
    )


def record_event(
    session: Session,
    *,
    event_id: str,
    experiment_id: str,
    event_type: str,
    payload: dict[str, Any],
    created_at: datetime,
) -> None:
    """Record an immutable experiment event into the audit trail (§63, §77)."""
    now = created_at
    session.execute(
        sa.insert(experiment_events_table).values(
            event_id=event_id,
            experiment_id=experiment_id,
            event_type=event_type,
            payload_json=json.dumps(payload, sort_keys=True, default=str),
            created_at=now,
        )
    )
