"""Materializer Builder: compiles experiments and treatments into downstream specifications (PHASE-P3 §19–§25, §44, Ruling R5)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c3 import (
    BoundedPreferences,
    DurationRangeSeconds,
    HardConstraints,
    ProviderPolicy,
)

from ..domain.enums import ExperimentStatus, PackageStatus, TrackRole, VariableClass
from ..domain.errors import ExperimentStateError
from ..domain.fingerprint import compute_package_hash
from ..domain.genome import BpmRange, ContentGenome, DurationRange
from ..domain.package import (
    ContentPackage,
    PackageExtension,
    PlaylistSpec,
    ThumbnailSpec,
    TitleSpec,
    TrackSpec,
    VisualSpec,
)
from ..storage.ids import deterministic_id, package_id, spec_id
from .arrangements import arrangement_direction
from .supply import SupplyPolicy, default_supply_policy, plan_supply

if TYPE_CHECKING:
    from ..domain.experiment import Experiment, Treatment


class BudgetEnvelopeExceededError(Exception):
    """Raised when a package's budget allocation is invalid or exceeds ceilings."""


class PackageExtensionError(ExperimentStateError):
    """A top-up was refused: over the declared bound, or a key reused for another count."""


MATERIALIZABLE_STATUSES = frozenset({ExperimentStatus.VALIDATED, ExperimentStatus.MATERIALIZED})
"""Only a validated experiment (or one with treatments already materialized) is built."""


def effective_budget(experiment: Experiment, requested: float | None) -> float:
    """The package budget: the directive's ceiling, or a positive request below it (§34)."""
    ceiling = experiment.budget_limit
    if requested is None:
        budget = ceiling
    elif requested > ceiling:
        raise BudgetEnvelopeExceededError(
            f"Requested budget envelope {requested} exceeds the directive's budget limit "
            f"{ceiling} for experiment {experiment.experiment_id}"
        )
    else:
        budget = requested
    if budget <= 0.0:
        raise BudgetEnvelopeExceededError(f"Budget envelope must be positive; got {budget}")
    return budget


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


def duration_range_seconds(duration: DurationRange) -> DurationRangeSeconds:
    """ADR-0032: the genome's track-duration range, carried as declared on C3."""
    return DurationRangeSeconds(min_seconds=duration.min_seconds, max_seconds=duration.max_seconds)


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
    budget_envelope: float | None = None,
    supply_policy: SupplyPolicy | None = None,
    candidate_target: int = 2,
    track_duration_range: DurationRangeSeconds | None = None,
    created_at: datetime,
) -> ContentPackage:
    """Compile a validated experiment's treatment into an immutable ContentPackage.

    ``budget_envelope`` may lower the directive's ``budget_limit`` but never raise it;
    ``None`` means the ceiling itself. ``candidate_target`` is how many candidates P4
    produces per track (1 under manual supply). ``track_duration_range`` replaces the
    genome's per-track range P5 enforces (manual supply: the owner's tool sets each song's
    length); the planned length and the track count still come from the genome, the
    length brought inside that range when the genome's target lies outside it. The track count is not a parameter: it is planned
    from the genome's declared playlist range and the planned track length
    (:func:`.supply.plan_supply`), and the budget is split over every spec the package
    may ever hold, extensions included.
    """
    if experiment.status not in MATERIALIZABLE_STATUSES:
        raise ExperimentStateError(
            "experiment_not_validated",
            f"Experiment {experiment.experiment_id!r} is {experiment.status.value!r}; only a "
            "validated experiment is materialized",
            field_paths=["status"],
        )
    if all(t.treatment_id != treatment.treatment_id for t in experiment.treatments):
        raise ExperimentStateError(
            "treatment_not_in_experiment",
            f"Treatment {treatment.treatment_id!r} is not part of {experiment.experiment_id!r}",
            field_paths=["treatment_id"],
        )
    budget = effective_budget(experiment, budget_envelope)

    now = created_at
    effective_genome = _apply_genome_delta(base_genome, treatment.genome_delta)
    duration = derived_duration_seconds(effective_genome.track_duration)
    if track_duration_range is not None:  # e.g. an ambient 330 s target under manual 90-300 s
        duration = min(max(duration, track_duration_range.min_seconds),
                       track_duration_range.max_seconds)
    supply = plan_supply(
        effective_genome.playlist_duration, duration, supply_policy or default_supply_policy()
    )
    track_count = supply.track_count

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
    track_budget = round(budget / supply.budget_slots, 2)
    # What this experiment tests stays as the treatment set it; the palette never varies it.
    held = sorted(v.name for v in experiment.variables
                  if v.variable_class == VariableClass.EXPERIMENTAL)
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
        direction = arrangement_direction(
            pkg_id, i, effective_genome.instrumentation_profile, genre=effective_genome.genre,
            subgenre=effective_genome.subgenre, held=held,
        )
        direction["held"] = held
        bounded_preferences = BoundedPreferences(
            bpm=derived_bpm(effective_genome.bpm),
            key_profile=direction.get("key_profile", effective_genome.key_profile),
            mood=tuple(effective_genome.mood),
            use_case=tuple(effective_genome.use_case),
        )
        creative_latitude = {
            "energy": direction.get("energy", effective_genome.energy.value),
            "visual_motion": effective_genome.visual_motion_profile,
            "track_arrangement": direction,
        }
        lyric_spec = {
            "lyric_density": effective_genome.lyric_density,
            "song_structure": direction.get("song_structure", effective_genome.song_structure),
        }
        vocal_spec = {
            "vocal_mode": effective_genome.vocal_mode.value,
            "vocal_gender": effective_genome.vocal_gender.value,
        }
        instrumentation_spec = {
            "instrumentation_profile": direction.get(
                "instrumentation", list(effective_genome.instrumentation_profile)),
            "hook_profile": direction.get("hook_profile", effective_genome.hook_profile),
        }
        provider_policy = ProviderPolicy(
            allowed_providers=("elevenlabs", "suno", "stable_audio", "lyria"),
        )

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
                production_style=direction.get(
                    "production_style", effective_genome.production_style),
                track_role=role,
                requested_duration=duration,
                duration_range=(track_duration_range if track_duration_range is not None
                                else duration_range_seconds(effective_genome.track_duration)),
                candidate_target=candidate_target,
                priority=1,
                budget_limit=track_budget,
                provider_policy=provider_policy,
            )
        )

    playlist_spec = PlaylistSpec(
        playlist_spec_id=ply_id,
        experiment_id=experiment.experiment_id,
        brand_id=brand_id,
        duration_range_seconds=supply.duration_range_seconds,
        target_duration_seconds=supply.target_duration_seconds,
        planned_track_duration_seconds=supply.planned_track_duration_seconds,
        base_track_count=supply.base_track_count,
        spare_track_count=supply.spare_track_count,
        track_count_target=supply.track_count,
        max_extension_track_count=supply.max_extension_track_count,
        spare_ratio=supply.spare_ratio,
        supply_policy_version=supply.supply_policy_version,
        sequencing_profile="progressive_arc",
    )

    visual_spec = VisualSpec(
        visual_spec_id=vis_id,
        experiment_id=experiment.experiment_id,
        visual_world=effective_genome.visual_world,
        motion_profile=effective_genome.visual_motion_profile,
        # No palette_guide: it was the same indigo/cyan/rain triple for every album and
        # nothing read it; P6 draws each visual_world itself (media_factory.visuals.worlds).
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
        budget_envelope=budget,
        created_at=now,
    )


def extension_id(package_id_: str, idempotency_key: str) -> str:
    """Identity of one top-up: the package and the caller's idempotency key."""
    return deterministic_id("ext", package_id_, idempotency_key)


def extend_package(
    package: ContentPackage,
    *,
    count: int,
    idempotency_key: str,
    created_at: datetime,
) -> tuple[PackageExtension, tuple[TrackSpec, ...]]:
    """Add ``count`` more distinct track specs to ``package``, deterministically.

    Each new spec is the package's own spec (same lineage, constraints, duration range,
    budget slot) under a new id derived from the package, the key and its position in
    the extension, as a ``secondary`` track. The caller looks the key up first: a stored
    extension for the key is the answer, and the same key with another count is refused
    (``idempotency_key_reused``). The total added over the package's life is bounded by
    ``playlist_spec.max_extension_track_count`` (``package_extension_limit_exceeded``).
    """
    if count < 1:
        raise ValueError(f"count must be at least 1; got {count}")
    bound = package.playlist_spec.max_extension_track_count
    used = package.extension_track_count
    if used + count > bound:
        raise PackageExtensionError(
            "package_extension_limit_exceeded",
            f"package {package.package_id} may add at most {bound} extension specs; "
            f"{used} are already added and {count} more were requested",
            field_paths=["count"],
        )
    if not package.track_specs:
        raise PackageExtensionError(
            "package_has_no_track_specs",
            f"package {package.package_id} has no track spec to extend from",
            field_paths=["package_id"],
        )
    template = package.track_specs[0]
    ext_id = extension_id(package.package_id, idempotency_key)
    instruments = template.instrumentation_spec.get("instrumentation_profile", [])
    if not isinstance(instruments, list):
        instruments = []
    first = template.creative_latitude.get("track_arrangement", {})
    # The album's own instruments and held dimensions, as the first track recorded them.
    base = list(first.get("album_instruments", instruments))
    held = list(first.get("held", []))
    genre = template.hard_constraints.genre
    subgenre = first.get("subgenre")

    def extension_spec(j: int) -> TrackSpec:
        direction = arrangement_direction(
            package.package_id, len(package.track_specs) + used + j, base, genre=genre,
            subgenre=subgenre, held=held)
        direction["held"] = held
        return template.model_copy(update={
            "track_spec_id": spec_id("trk", package.package_id, f"ext:{idempotency_key}:{j}"),
            "track_role": TrackRole.SECONDARY,
            "bounded_preferences": template.bounded_preferences.model_copy(update={
                "key_profile": direction.get(
                    "key_profile", template.bounded_preferences.key_profile)}),
            "creative_latitude": {
                **template.creative_latitude, "track_arrangement": direction,
                "energy": direction.get("energy", template.creative_latitude.get("energy")),
            },
            "lyric_spec": {**template.lyric_spec, "song_structure": direction.get(
                "song_structure", template.lyric_spec.get("song_structure"))},
            "instrumentation_spec": {
                **template.instrumentation_spec,
                "instrumentation_profile": direction.get("instrumentation", base),
                "hook_profile": direction.get(
                    "hook_profile", template.instrumentation_spec.get("hook_profile")),
            },
            "production_style": direction.get("production_style", template.production_style),
        })

    specs = tuple(extension_spec(j) for j in range(count))
    extension = PackageExtension(
        extension_id=ext_id,
        package_id=package.package_id,
        idempotency_key=idempotency_key,
        track_count=count,
        track_spec_ids=tuple(spec.track_spec_id for spec in specs),
        created_at=created_at,
    )
    return extension, specs
