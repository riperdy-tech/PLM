"""Materializer Builder: compiles experiments and treatments into downstream specifications (PHASE-P3 §19–§25, §44, Ruling R5)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c3 import (
    BoundedPreferences,
    HardConstraints,
    ProviderPolicy,
)

from ..domain.enums import PackageStatus, TrackRole
from ..domain.fingerprint import compute_package_hash
from ..domain.genome import BpmRange, ContentGenome, DurationRange
from ..domain.package import (
    ContentPackage,
    PlaylistSpec,
    ThumbnailSpec,
    TitleSpec,
    TrackSpec,
    VisualSpec,
)
from ..storage.ids import package_id, spec_id

if TYPE_CHECKING:
    from ..domain.experiment import Experiment, Treatment


class BudgetEnvelopeExceededError(Exception):
    """Raised when a package's budget allocation is invalid or exceeds ceilings."""


def derived_bpm(bpm: BpmRange) -> int:
    """R5 rule: the genome's target BPM, else the rounded midpoint of its range."""
    if bpm.target_bpm is not None:
        return bpm.target_bpm
    return round((bpm.min_bpm + bpm.max_bpm) / 2)


def derived_duration_seconds(duration: DurationRange) -> int:
    """R5 rule: the genome's target duration, else the rounded midpoint of its range."""
    if duration.target_seconds is not None:
        return duration.target_seconds
    return round((duration.min_seconds + duration.max_seconds) / 2)


def content_genome_id(genome: ContentGenome) -> str:
    """Identity of the effective genome a package was built from (content-addressed)."""
    digest = hashlib.sha256(genome.model_dump_json().encode("utf-8")).hexdigest()
    return f"gen_{digest[:16]}"


def _apply_genome_delta(base: ContentGenome, delta: dict[str, Any]) -> ContentGenome:
    """Apply treatment mutations onto a baseline genome."""
    if not delta:
        return base
    dumped = base.model_dump()
    for k, v in delta.items():
        if k == "bpm" and isinstance(v, dict):
            dumped["bpm"] = BpmRange(**v)
        else:
            dumped[k] = v
    return ContentGenome.model_validate(dumped)


def materialize_package(
    experiment: Experiment,
    treatment: Treatment,
    base_genome: ContentGenome,
    *,
    brand_id: str = "neon_seoul",
    budget_envelope: float = 100.0,
    track_count: int = 5,
    total_duration_minutes: int = 60,
    created_at: datetime | None = None,
) -> ContentPackage:
    """Compile an experiment treatment and genome into an immutable ContentPackage."""
    if budget_envelope < 0.0:
        raise BudgetEnvelopeExceededError(
            f"Budget envelope must be non-negative; got {budget_envelope}"
        )
    if track_count < 1:
        raise ValueError(f"track_count must be at least 1; got {track_count}")

    now = created_at or datetime.now(UTC)
    effective_genome = _apply_genome_delta(base_genome, treatment.genome_delta)

    pkg_id = package_id(experiment.experiment_id, treatment.treatment_id)
    ply_id = spec_id("ply", pkg_id)
    vis_id = spec_id("vis", pkg_id)
    thb_id = spec_id("thb", pkg_id)
    ttl_id = spec_id("ttl", pkg_id)

    lineage = ExperimentLineage(
        experiment_id=experiment.experiment_id,
        experiment_family_id=experiment.experiment_family_id,
        content_arm_id=experiment.content_arm_id,
        content_genome_id=content_genome_id(effective_genome),
        production_directive_id=experiment.production_directive_id,
    )
    track_budget = round(budget_envelope / track_count, 2)
    track_specs: list[TrackSpec] = []
    track_spec_ids: list[str] = []

    for i in range(track_count):
        trk_id = spec_id("trk", pkg_id, str(i))
        track_spec_ids.append(trk_id)
        role = TrackRole.FOCUS if i == 0 else TrackRole.SECONDARY

        hard_constraints = HardConstraints(
            genre=effective_genome.genre,
            language=effective_genome.language,
            vocal_mode=effective_genome.vocal_mode.value,
        )
        bounded_preferences = BoundedPreferences(
            bpm=derived_bpm(effective_genome.bpm),
            key_profile=effective_genome.key_profile,
            mood=tuple(effective_genome.mood),
            use_case=tuple(effective_genome.use_case),
        )
        creative_latitude = {
            "energy": effective_genome.energy.value,
            "visual_motion": effective_genome.visual_motion_profile,
        }
        lyric_spec = {
            "lyric_density": effective_genome.lyric_density,
            "song_structure": effective_genome.song_structure,
        }
        vocal_spec = {
            "vocal_mode": effective_genome.vocal_mode.value,
            "vocal_gender": effective_genome.vocal_gender.value,
        }
        instrumentation_spec = {
            "instrumentation_profile": list(effective_genome.instrumentation_profile),
            "hook_profile": effective_genome.hook_profile,
        }
        provider_policy = ProviderPolicy(
            allowed_providers=("elevenlabs", "suno", "stable_audio"),
        )

        duration = derived_duration_seconds(effective_genome.track_duration)

        track_specs.append(
            TrackSpec(
                track_spec_id=trk_id,
                playlist_spec_id=ply_id,
                lineage=lineage,
                brand_profile_id=f"{brand_id}_profile",
                hard_constraints=hard_constraints,
                bounded_preferences=bounded_preferences,
                creative_latitude=creative_latitude,
                lyric_spec=lyric_spec,
                vocal_spec=vocal_spec,
                instrumentation_spec=instrumentation_spec,
                production_style=effective_genome.production_style,
                track_role=role,
                requested_duration=duration,
                candidate_target=2,
                priority=1,
                budget_limit=track_budget,
                provider_policy=provider_policy,
            )
        )

    playlist_spec = PlaylistSpec(
        playlist_spec_id=ply_id,
        experiment_id=experiment.experiment_id,
        brand_id=brand_id,
        track_count_target=track_count,
        total_duration_minutes=total_duration_minutes,
        sequencing_profile="progressive_arc",
    )

    visual_spec = VisualSpec(
        visual_spec_id=vis_id,
        experiment_id=experiment.experiment_id,
        visual_world=effective_genome.visual_world,
        motion_profile=effective_genome.visual_motion_profile,
        palette_guide=("deep_indigo", "neon_cyan", "rain_sheen"),
        aspect_ratio="16:9",
    )

    thumbnail_spec = ThumbnailSpec(
        thumbnail_spec_id=thb_id,
        experiment_id=experiment.experiment_id,
        thumbnail_family=effective_genome.thumbnail_family,
        composition_archetype="cinematic_establishing_shot",
        text_rules={"max_words": 3, "font": "sans_bold"},
    )

    title_spec = TitleSpec(
        title_spec_id=ttl_id,
        experiment_id=experiment.experiment_id,
        title_archetype=effective_genome.title_archetype,
        formatting_pattern="{mood} {genre} for {use_case}",
        character_limit=70,
    )

    pkg_hash = compute_package_hash(experiment.experiment_id, treatment.treatment_id, track_spec_ids)

    return ContentPackage(
        package_id=pkg_id,
        experiment_id=experiment.experiment_id,
        treatment_id=treatment.treatment_id,
        brand_id=brand_id,
        status=PackageStatus.MATERIALIZED,
        track_specs=tuple(track_specs),
        playlist_spec=playlist_spec,
        visual_spec=visual_spec,
        thumbnail_spec=thumbnail_spec,
        title_spec=title_spec,
        package_hash=pkg_hash,
        budget_envelope=budget_envelope,
        created_at=now,
    )
