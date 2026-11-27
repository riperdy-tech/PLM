"""Channel-identity audio checks for Phase P5 (ADR-0044).

The genre's profile in ``quality_gate/audio/identity_profiles.json`` declares the tempo
band and the loudness-rise warning; the C4 event's first genre tag (P4 sends the spec's
genre first) selects it. A genre without a profile is not checked.

Reason codes: ``TEMPO_OUTSIDE_IDENTITY`` (blocking) when the measured tempo lies outside
the band at or above the profile's confidence and neither its half nor its double is an
accepted reading: a half or double inside the band counts only when the beat is about as
strong there (``half_time_min_strength``, :func:`~..audio.rhythm.pulse_strengths`);
``TEMPO_POSSIBLY_FAST`` (warning) when the accepted tempo's double is as strong and above
``fast_tempo_warn_bpm`` (a fast track felt half-time); ``SUDDEN_LOUDNESS_RISE``
(warning) when the short-term level rises by more than the declared amount inside the
rise window, away from the track's edges. An unmeasurable tempo (no beat, low
confidence, undecodable) is recorded on the check and judged by nothing: the technical
and musical evaluators already block an unreadable file.

For every genre: ``VOCAL_PRESENCE_SUSPECTED`` (a hold for a listening review) when an
instrumental track has a voice-like moving pitch in at least the policy's share of its
frames, and ``STUCK_LOOP_SUSPECTED`` (warning) when the track is one clip repeated
(:func:`~..audio.rhythm.loop_repetition`). These are signal-processing estimates, not a
listening review; sung vocals that arrive with lyrics are also caught by the lyric
evaluator (``UNEXPECTED_LYRICS_FOR_INSTRUMENTAL``).
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
from media_factory.audio.features import DEFAULT_FEATURE_POLICY, measure_features
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..audio.decode import DecodedAudio, decode_audio
from ..audio.policy import load_audio_qa_policy
from ..audio.rhythm import loop_repetition, pulse_strengths, vocal_presence
from ..detectors.reason_codes import VOCAL_PRESENCE_SUSPECTED
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
    half_time_min_strength: float = Field(gt=0, le=1)
    fast_tempo_warn_bpm: float = Field(gt=0)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self) -> IdentityProfile:
        if self.bpm_min >= self.bpm_max:
            raise ValueError("bpm_min must be below bpm_max")
        return self

    def in_band(self, bpm: float) -> bool:
        return self.bpm_min - self.tolerance_bpm <= bpm <= self.bpm_max + self.tolerance_bpm

    def tempo_reading(self, bpm: float, strength: float | None, half: float | None,
                      double: float | None) -> tuple[float | None, float | None]:
        """``(accepted tempo, fastest strong pulse)``; the accepted tempo is ``None``
        when no reading lies in the band. ``strength``, ``half`` and ``double`` are the
        beat's strengths at ``bpm``, ``bpm / 2`` and ``bpm * 2`` (``None``: unknown)."""
        def strong(other: float | None) -> bool:
            return (other is not None and strength is not None
                    and other >= self.half_time_min_strength * strength)

        if self.in_band(bpm):
            return bpm, (bpm * 2 if self.octave_equivalent and strong(double) else bpm)
        if self.octave_equivalent and self.in_band(bpm / 2) and strong(half):
            return bpm / 2, bpm  # a fast beat felt half-time
        if self.octave_equivalent and self.in_band(bpm * 2) and strong(double):
            return bpm * 2, bpm * 2
        return None, None


class IdentityAudioPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1)
    notes: tuple[str, ...]
    vocal_presence_hold_share: float = Field(gt=0, le=1)
    loop_repeated_share_warn: float = Field(gt=0, le=1)
    loop_steady_share_max: float = Field(gt=0, le=1)
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


def _max_level_rise(mono: Any, rate: int, profile: IdentityProfile) -> float | None:
    """Largest rise of the short-term RMS level (dB) inside the rise window."""
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


def _repetition_checks(evaluation_id: str, mono: Any, rate: int, vocal_mode: str | None,
                       policy: IdentityAudioPolicy, *,
                       now: datetime) -> tuple[list[MusicalCheck], list[RiskFinding]]:
    """The vocal-presence hold (instrumental tracks) and the stuck-loop warning."""
    checks: list[MusicalCheck] = []
    findings: list[RiskFinding] = []
    if vocal_mode == "instrumental":
        share = vocal_presence(mono, rate)
        suspected = share is not None and share >= policy.vocal_presence_hold_share
        checks.append(MusicalCheck.create(
            evaluation_id_str=evaluation_id, check_type="identity_vocal_presence",
            score=0.0 if suspected else 1.0, confidence=0.0 if share is None else 0.5,
            severity=CheckSeverity.HIGH if suspected else CheckSeverity.INFO,
            method="moving_pitch_share", model_version=policy.policy_version,
            segment_data={"voice_like_share": None if share is None else round(share, 3),
                          "hold_at_share": policy.vocal_presence_hold_share},
            now=now))
        if suspected and share is not None:
            findings.append(RiskFinding.create(
                evaluation_id_str=evaluation_id, finding_code=VOCAL_PRESENCE_SUSPECTED,
                category=GateCategory.QUALITY_GATE, severity=CheckSeverity.HIGH,
                status="WARN", confidence=0.5,
                message=(f"An instrumental track has a voice-like moving pitch in {share:.0%} "
                         f"of its frames (hold at {policy.vocal_presence_hold_share:.0%}): "
                         "listen for sung vocals (a forgotten Instrumental switch) "
                         f"[identity policy {policy.policy_version}]"),
                blocking=False, now=now))
    loop = loop_repetition(mono, rate)
    stuck = (loop is not None and loop.repeated_share >= policy.loop_repeated_share_warn
             and loop.steady_share <= policy.loop_steady_share_max)
    checks.append(MusicalCheck.create(
        evaluation_id_str=evaluation_id, check_type="identity_loop_repetition",
        score=0.0 if stuck else 1.0, confidence=0.0 if loop is None else 0.5,
        severity=CheckSeverity.MEDIUM if stuck else CheckSeverity.INFO,
        method="waveform_self_similarity", model_version=policy.policy_version,
        segment_data=({"period_seconds": None, "repeated_share": None, "steady_share": None}
                      if loop is None else {
                          "period_seconds": round(loop.period_seconds, 2),
                          "repeated_share": round(loop.repeated_share, 3),
                          "steady_share": round(loop.steady_share, 3)}),
        now=now))
    if stuck and loop is not None:
        findings.append(RiskFinding.create(
            evaluation_id_str=evaluation_id, finding_code="STUCK_LOOP_SUSPECTED",
            category=GateCategory.QUALITY_GATE, severity=CheckSeverity.MEDIUM,
            status="WARN", confidence=0.5,
            message=(f"{loop.repeated_share:.0%} of the track repeats itself exactly every "
                     f"{loop.period_seconds:.1f} s: possibly one clip looped end to end "
                     f"[identity policy {policy.policy_version}]"),
            blocking=False, now=now))
    return checks, findings


def evaluate_identity_audio(
    evaluation_id: str,
    audio_path: str | Path,
    genre_tags: list[str],
    *,
    now: datetime,
    policy: IdentityAudioPolicy | None = None,
    vocal_mode: str | None = None,
) -> IdentityEvaluationResult:
    """Check one track's tempo and loudness rises against its genre's identity profile,
    and (any genre) sung vocals in an instrumental and a stuck loop."""
    policy = policy or load_identity_audio_policy()
    genre = genre_tags[0] if genre_tags else ""
    profile = policy.profiles.get(genre)
    path = Path(audio_path)
    if not path.is_file():
        return IdentityEvaluationResult([], [], policy.policy_version)
    decoded = decode_audio(
        path, timeout_seconds=load_audio_qa_policy().decode.ffmpeg_timeout_seconds)
    if not isinstance(decoded, DecodedAudio):
        return IdentityEvaluationResult([], [], policy.policy_version)
    mono = decoded.samples.mean(axis=1)
    rate = decoded.sample_rate
    checks, findings = _repetition_checks(evaluation_id, mono, rate, vocal_mode, policy,
                                          now=now)
    if profile is None:
        return IdentityEvaluationResult(checks, findings, policy.policy_version)

    feature_policy = dataclasses.replace(
        DEFAULT_FEATURE_POLICY, bpm_min=50.0, bpm_max=200.0,
        bpm_prior_center=(profile.bpm_min + profile.bpm_max) / 2)
    features = measure_features(evaluation_id, b"", feature_policy,
                                pcm=(decoded.samples, rate))
    bpm, confidence = features.bpm, features.bpm_confidence
    judged = bpm is not None and confidence is not None and confidence >= profile.min_confidence
    accepted: float | None = None
    fastest: float | None = None
    strengths: list[float | None] = [None, None, None]
    if judged and bpm is not None:
        strengths = pulse_strengths(mono, rate, [bpm, bpm / 2, bpm * 2])
        accepted, fastest = profile.tempo_reading(bpm, *strengths)
    inside = not judged or accepted is not None
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
            "accepted_bpm": None if accepted is None else round(accepted, 1),
            "fastest_pulse_bpm": None if fastest is None else round(fastest, 1),
            "pulse_strength": {name: None if value is None else round(value, 3)
                               for name, value in zip(("measured", "half", "double"),
                                                      strengths, strict=True)},
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
                f"the {genre} band {profile.bpm_min:g}-{profile.bpm_max:g} BPM, and no "
                f"equally strong half or double lies inside (ADR-0044) "
                f"[identity policy {policy.policy_version}]"
            ),
            blocking=True,
            now=now,
        ))
    if accepted is not None and fastest is not None and fastest > profile.fast_tempo_warn_bpm:
        findings.append(RiskFinding.create(
            evaluation_id_str=evaluation_id,
            finding_code="TEMPO_POSSIBLY_FAST",
            category=GateCategory.QUALITY_GATE,
            severity=CheckSeverity.MEDIUM,
            status="WARN",
            confidence=0.5,
            message=(
                f"The beat is as strong at {fastest:.0f} BPM as at {accepted:.0f} BPM: "
                f"possibly a fast track (above {profile.fast_tempo_warn_bpm:g} BPM) felt "
                f"half-time [identity policy {policy.policy_version}]"
            ),
            blocking=False,
            now=now,
        ))

    rise = _max_level_rise(mono, rate, profile)
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
