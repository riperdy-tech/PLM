"""Channel-identity audio checks for Phase P5 (ADR-0044).

The genre's profile in ``quality_gate/audio/identity_profiles.json`` declares the tempo
band and the loudness-rise warning; the C4 event's first genre tag (P4 sends the spec's
genre first) selects it. A genre without a profile is not checked.

Reason codes: ``TEMPO_OUTSIDE_IDENTITY`` (blocking) when the measured tempo, and its half
and double, all lie outside the band at or above the profile's confidence;
``SUDDEN_LOUDNESS_RISE`` (warning) when the short-term level rises by more than the
declared amount inside the rise window, away from the track's edges. An unmeasurable
tempo (no beat, low confidence, undecodable) is recorded on the check and judged by
nothing: the technical and musical evaluators already block an unreadable file.

These are signal-processing estimates, not a listening review. P5 has no vocal
detector; an instrumental that arrives with lyrics is caught by the lyric evaluator
(``UNEXPECTED_LYRICS_FOR_INSTRUMENTAL``), sung vocals without lyrics are not.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]
from media_factory.audio.features import DEFAULT_FEATURE_POLICY, measure_features
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.enums import CheckSeverity, GateCategory
from ..domain.models import MusicalCheck, RiskFinding

__all__ = [
    "IdentityAudioPolicy",
    "IdentityEvaluationResult",
    "IdentityProfile",
    "evaluate_identity_audio",
    "load_identity_audio_policy",
]


class IdentityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bpm_min: float = Field(gt=0)
    bpm_max: float = Field(gt=0)
    octave_equivalent: bool
    tolerance_bpm: float = Field(ge=0)
    min_confidence: float = Field(ge=0, le=1)
    level_window_seconds: float = Field(gt=0)
    rise_window_seconds: float = Field(gt=0)
    edge_skip_seconds: float = Field(ge=0)
    loudness_rise_warn_db: float = Field(gt=0)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> IdentityProfile:
        if self.bpm_min >= self.bpm_max:
            raise ValueError("bpm_min must be below bpm_max")
        return self

    def tempo_inside(self, bpm: float) -> bool:
        candidates = (bpm, bpm / 2, bpm * 2) if self.octave_equivalent else (bpm,)
        low, high = self.bpm_min - self.tolerance_bpm, self.bpm_max + self.tolerance_bpm
        return any(low <= value <= high for value in candidates)


class IdentityAudioPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    notes: tuple[str, ...]
    profiles: dict[str, IdentityProfile]


@lru_cache(maxsize=1)
def load_identity_audio_policy() -> IdentityAudioPolicy:
    package = f"{__package__.rsplit('.', 1)[0]}.audio"
    raw = resources.files(package).joinpath("identity_profiles.json").read_text(encoding="utf-8")
    return IdentityAudioPolicy.model_validate(json.loads(raw))


@dataclasses.dataclass(frozen=True)
class IdentityEvaluationResult:
    checks: list[MusicalCheck]
    findings: list[RiskFinding]
    policy_version: str


def _max_level_rise(path: Path, profile: IdentityProfile) -> float | None:
    """Largest rise of the short-term RMS level (dB) inside the rise window."""
    try:
        samples, rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (RuntimeError, ValueError, TypeError, sf.LibsndfileError):
        return None
    mono = samples.mean(axis=1)
    window = int(profile.level_window_seconds * rate)
    hop = int(rate)
    if window <= 0 or len(mono) < window + hop:
        return None
    levels = np.array([10.0 * np.log10(np.mean(mono[i:i + window] ** 2) + 1e-12)
                       for i in range(0, len(mono) - window, hop)])
    skip = int(profile.edge_skip_seconds)
    core = levels[skip:len(levels) - skip] if len(levels) > 2 * skip + 2 else levels
    span = max(1, int(profile.rise_window_seconds))
    rises = [core[i] - core[max(0, i - span):i].min() for i in range(1, len(core))]
    return float(max(rises)) if rises else None


def evaluate_identity_audio(
    evaluation_id: str,
    audio_path: str | Path,
    genre_tags: list[str],
    *,
    now: datetime,
    policy: IdentityAudioPolicy | None = None,
) -> IdentityEvaluationResult:
    """Check one track's tempo and loudness rises against its genre's identity profile."""
    policy = policy or load_identity_audio_policy()
    genre = genre_tags[0] if genre_tags else ""
    profile = policy.profiles.get(genre)
    path = Path(audio_path)
    if profile is None or not path.is_file():
        return IdentityEvaluationResult([], [], policy.policy_version)

    checks: list[MusicalCheck] = []
    findings: list[RiskFinding] = []
    feature_policy = dataclasses.replace(
        DEFAULT_FEATURE_POLICY, bpm_min=50.0, bpm_max=200.0,
        bpm_prior_center=(profile.bpm_min + profile.bpm_max) / 2)
    features = measure_features(evaluation_id, path.read_bytes(), feature_policy)
    bpm, confidence = features.bpm, features.bpm_confidence
    judged = bpm is not None and confidence is not None and confidence >= profile.min_confidence
    inside = bpm is None or not judged or profile.tempo_inside(bpm)
    checks.append(MusicalCheck.create(
        evaluation_id_str=evaluation_id,
        check_type="identity_tempo",
        score=1.0 if inside else 0.0,
        confidence=round(confidence, 3) if judged and confidence is not None else 0.0,
        severity=CheckSeverity.INFO if inside else CheckSeverity.HIGH,
        method=f"onset_autocorrelation:{features.extractor_version}",
        model_version=policy.policy_version,
        segment_data={
            "genre": genre, "bpm": None if bpm is None else round(bpm, 1),
            "bpm_confidence": None if confidence is None else round(confidence, 3),
            "unknown_reason": features.unknown_reasons.get("bpm"),
            "band_bpm": [profile.bpm_min, profile.bpm_max],
            "octave_equivalent": profile.octave_equivalent,
        },
        now=now,
    ))
    if not inside and bpm is not None and confidence is not None:
        findings.append(RiskFinding.create(
            evaluation_id_str=evaluation_id,
            finding_code="TEMPO_OUTSIDE_IDENTITY",
            category=GateCategory.QUALITY_GATE,
            severity=CheckSeverity.HIGH,
            status="FAIL",
            confidence=round(confidence, 3),
            message=(
                f"Measured tempo {bpm:.1f} BPM (confidence {confidence:.2f}) lies outside "
                f"the {genre} band {profile.bpm_min:g}-{profile.bpm_max:g} BPM, half and "
                f"double included (ADR-0044) [identity policy {policy.policy_version}]"
            ),
            blocking=True,
            now=now,
        ))

    rise = _max_level_rise(path, profile)
    detail: dict[str, Any] = {
        "max_rise_db": None if rise is None else round(rise, 1),
        "warn_above_db": profile.loudness_rise_warn_db,
        "level_window_seconds": profile.level_window_seconds,
        "rise_window_seconds": profile.rise_window_seconds,
    }
    sudden = rise is not None and rise > profile.loudness_rise_warn_db
    checks.append(MusicalCheck.create(
        evaluation_id_str=evaluation_id,
        check_type="identity_loudness_rise",
        score=0.0 if sudden else 1.0,
        confidence=0.0 if rise is None else 0.5,
        severity=CheckSeverity.MEDIUM if sudden else CheckSeverity.INFO,
        method="short_term_rms_rise",
        model_version=policy.policy_version,
        segment_data=detail,
        now=now,
    ))
    if sudden and rise is not None:
        findings.append(RiskFinding.create(
            evaluation_id_str=evaluation_id,
            finding_code="SUDDEN_LOUDNESS_RISE",
            category=GateCategory.QUALITY_GATE,
            severity=CheckSeverity.MEDIUM,
            status="WARN",
            confidence=0.5,
            message=(
                f"Short-term level rises {rise:.1f} dB within {profile.rise_window_seconds:g} s "
                f"(warning above {profile.loudness_rise_warn_db:g} dB): possible drop, which "
                f"focus music avoids (ADR-0044) [identity policy {policy.policy_version}]"
            ),
            blocking=False,
            now=now,
        ))
    return IdentityEvaluationResult(checks, findings, policy.policy_version)
