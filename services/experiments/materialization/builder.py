"""Materializer Builder: compiles experiments and treatments into downstream specifications (PHASE-P3 §19–§25, §44, Ruling R5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..domain.enums import PackageStatus, TrackRole
from ..domain.fingerprint import compute_package_hash
from ..domain.genome import BpmRange, ContentGenome
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

    track_budget = round(budget_envelope / track_count, 2)
    track_specs: list[TrackSpec] = []
    track_spec_ids: list[str] = []

    for i in range(track_count):
        trk_id = spec_id("trk", pkg_id, str(i))
        track_spec_ids.append(trk_id)
        role = TrackRole.FOCUS if i == 0 else TrackRole.SECONDARY

        hard_constraints = {
            "genre": effective_genome.genre,
            "language": effective_genome.language,
            "vocal_mode": effective_genome.vocal_mode.value,
        }
        bounded_preferences = {
            "bpm": effective_genome.bpm.target_bpm or 80,
            "key_profile": effective_genome.key_profile or "C_minor",
            "mood": list(effective_genome.mood),
            "use_case": list(effective_genome.use_case),
        }
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
        provider_policy = {
            "allowed_providers": ["elevenlabs", "suno", "stable_audio"],
        }

        duration = effective_genome.track_duration.target_seconds or 180

        track_specs.append(
            TrackSpec(
                track_spec_id=trk_id,
                playlist_spec_id=ply_id,
                experiment_id=experiment.experiment_id,
                content_arm_id=experiment.content_arm_id,
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
