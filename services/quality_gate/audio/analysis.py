"""One pass of every P5 audio measurement over decoded PCM (R4).

:func:`measure_audio` runs the methods in :mod:`.measure` with the thresholds of an
:class:`~.policy.AudioQaPolicy` and returns plain values. A measurement that cannot be
made is recorded as UNKNOWN with its reason, never as a number; judging the values is the
evaluators' job.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .decode import DecodedAudio
from .measure import (
    ClipStats,
    EdgeSilence,
    MeasurementUnknownError,
    clip_stats,
    db_from_linear,
    edge_silence,
    integrated_loudness_lufs,
    linear_from_db,
    rms_linear,
    sample_peak_linear,
    stereo_correlation,
    true_peak_linear,
)
from .policy import AudioQaPolicy

__all__ = ["AudioMeasurements", "Unknown", "json_number", "measure_audio", "measure_loudness"]


@dataclass(frozen=True)
class Unknown:
    """A measurement that could not be made."""

    reason: str
    detail: str


@dataclass(frozen=True)
class AudioMeasurements:
    policy_version: str
    container: str
    codec: str
    decoder: str
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    integrated_lufs: float | Unknown
    sample_peak_dbfs: float
    true_peak_dbtp: float
    rms_dbfs: float
    clipping: ClipStats
    silence: EdgeSilence
    # None when the measurement does not apply (not two channels).
    stereo_correlation: float | Unknown | None

    def as_evidence(self) -> dict[str, Any]:
        """JSON-safe, key-sorted record of every value (``-inf`` as a string)."""

        def value(v: float | Unknown | None) -> Any:
            if v is None:
                return "not_applicable"
            if isinstance(v, Unknown):
                return {"unknown": v.reason, "detail": v.detail}
            return json_number(v)

        return {
            "policy_version": self.policy_version,
            "container": self.container,
            "codec": self.codec,
            "decoder": self.decoder,
            "sample_rate_hz": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "duration_seconds": json_number(self.duration_seconds),
            "integrated_lufs": value(self.integrated_lufs),
            "sample_peak_dbfs": json_number(self.sample_peak_dbfs),
            "true_peak_dbtp": json_number(self.true_peak_dbtp),
            "rms_dbfs": json_number(self.rms_dbfs),
            "clipping": {
                "events": self.clipping.events,
                "clipped_samples": self.clipping.clipped_samples,
                "longest_run_samples": self.clipping.longest_run_samples,
                "first_event_seconds": None
                if self.clipping.first_event_seconds is None
                else json_number(self.clipping.first_event_seconds),
            },
            "silence": {
                "leading_seconds": json_number(self.silence.leading_seconds),
                "trailing_seconds": json_number(self.silence.trailing_seconds),
                "entirely_silent": self.silence.entirely_silent,
            },
            "stereo_correlation": value(self.stereo_correlation),
        }

    def evidence_json(self) -> str:
        return json.dumps(self.as_evidence(), sort_keys=True, separators=(",", ":"))

    def evidence_sha256(self) -> str:
        return hashlib.sha256(self.evidence_json().encode("utf-8")).hexdigest()


def json_number(value: float, digits: int = 4) -> float | str:
    """``value`` rounded for records; non-finite values as ``"-inf"``/``"inf"``/``"nan"``."""
    if math.isfinite(value):
        return round(value, digits)
    return str(value)


def measure_loudness(audio: DecodedAudio) -> float | Unknown:
    try:
        return integrated_loudness_lufs(audio.samples, audio.sample_rate)
    except MeasurementUnknownError as exc:
        return Unknown(exc.reason, exc.detail)


def measure_audio(audio: DecodedAudio, policy: AudioQaPolicy) -> AudioMeasurements:
    samples = audio.samples
    rate = audio.sample_rate

    correlation: float | Unknown | None
    if audio.channels == 2:
        try:
            correlation = stereo_correlation(samples)
        except MeasurementUnknownError as exc:
            correlation = Unknown(exc.reason, exc.detail)
    else:
        correlation = None

    return AudioMeasurements(
        policy_version=policy.policy_version,
        container=audio.container,
        codec=audio.codec,
        decoder=audio.decoder,
        sample_rate=rate,
        channels=audio.channels,
        frames=audio.frames,
        duration_seconds=audio.duration_seconds,
        integrated_lufs=measure_loudness(audio),
        sample_peak_dbfs=db_from_linear(sample_peak_linear(samples)),
        true_peak_dbtp=db_from_linear(
            true_peak_linear(samples, policy.true_peak.oversampling_factor)
        ),
        rms_dbfs=db_from_linear(rms_linear(samples)),
        clipping=clip_stats(
            samples,
            rate,
            level_linear=linear_from_db(policy.clipping.full_scale_dbfs),
            min_run_samples=policy.clipping.min_run_samples,
        ),
        silence=edge_silence(
            samples, rate, threshold_linear=linear_from_db(policy.silence.threshold_dbfs)
        ),
        stereo_correlation=correlation,
    )
