"""Ingestion and adaptation of P3 TrackSpec specifications (Seam C3, Ruling R5)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = ["AdaptedTrackSpec", "TrackSpecValidationError", "adapt_track_spec"]


class TrackSpecValidationError(Exception):
    """Raised when an incoming P3 TrackSpec fails validation."""


class AdaptedTrackSpec(BaseModel):
    """P4 internal representation of an ingested P3 TrackSpec (Seam C3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_spec_id: str
    playlist_spec_id: str
    experiment_id: str
    content_arm_id: str
    brand_profile_id: str
    genre: str
    vocal_mode: str
    language: str
    bpm: int
    moods: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    requested_duration: int = Field(ge=1)
    candidate_target: int = Field(default=2, ge=1)
    priority: int = Field(default=1, ge=1)
    budget_limit: float = Field(ge=0.0)
    allowed_providers: tuple[str, ...] = ()
    raw_spec: dict[str, Any]


def adapt_track_spec(wire_payload: dict[str, Any]) -> AdaptedTrackSpec:
    """Validate and adapt a frozen C3 TrackSpec payload into P4's internal domain model."""
    for req in ("track_spec_id", "experiment_id", "content_arm_id"):
        if not wire_payload.get(req):
            raise TrackSpecValidationError(f"TrackSpec missing required identity field: {req}")

    duration = wire_payload.get("requested_duration")
    if duration is None or int(duration) <= 0:
        raise TrackSpecValidationError(f"requested_duration must be positive; got {duration}")

    budget = wire_payload.get("budget_limit")
    if budget is None or float(budget) < 0.0:
        raise TrackSpecValidationError(f"budget_limit must be >= 0.0; got {budget}")

    hard = wire_payload.get("hard_constraints", {})
    bounded = wire_payload.get("bounded_preferences", {})
    vocal = wire_payload.get("vocal_spec", {})
    policy = wire_payload.get("provider_policy", {})

    genre = hard.get("genre") or bounded.get("genre") or "rnb"
    vocal_mode = vocal.get("vocal_mode") or hard.get("vocal_mode") or "vocal"
    language = hard.get("language") or "english"
    bpm = int(bounded.get("bpm") or 80)
    moods = tuple(bounded.get("mood", []))
    use_cases = tuple(bounded.get("use_case", []))
    allowed_providers = tuple(policy.get("allowed_providers", []))

    try:
        return AdaptedTrackSpec(
            track_spec_id=wire_payload["track_spec_id"],
            playlist_spec_id=wire_payload.get("playlist_spec_id", ""),
            experiment_id=wire_payload["experiment_id"],
            content_arm_id=wire_payload["content_arm_id"],
            brand_profile_id=wire_payload.get("brand_profile_id", "neon_seoul"),
            genre=genre,
            vocal_mode=vocal_mode,
            language=language,
            bpm=bpm,
            moods=moods,
            use_cases=use_cases,
            requested_duration=int(duration),
            candidate_target=int(wire_payload.get("candidate_target", 2)),
            priority=int(wire_payload.get("priority", 1)),
            budget_limit=float(budget),
            allowed_providers=allowed_providers,
            raw_spec=wire_payload,
        )
    except (ValidationError, TypeError, ValueError) as err:
        raise TrackSpecValidationError(f"Invalid C3 TrackSpec payload: {err}") from err
