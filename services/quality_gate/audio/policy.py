"""The declared, versioned thresholds of the P5 technical and musical audio checks (R4).

Every threshold the audio evaluators apply lives in ``audio_qa_policy.json`` next to this
module and is validated here. ``policy_version`` travels with each check, the
measurement evidence and the evaluator results, so a decision can be reproduced against
the exact thresholds that produced it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AudioQaPolicy",
    "ClippingPolicy",
    "DecodePolicy",
    "DynamicsPolicy",
    "FormatPolicy",
    "LoudnessPolicy",
    "MusicalScoringPolicy",
    "SilencePolicy",
    "StereoPolicy",
    "TruePeakPolicy",
    "load_audio_qa_policy",
    "parse_audio_qa_policy",
]

_POLICY_FILE = "audio_qa_policy.json"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)


class DecodePolicy(_Section):
    ffmpeg_timeout_seconds: float = Field(gt=0)


class FormatPolicy(_Section):
    expected_sample_rate_hz: int = Field(gt=0)
    expected_channels: int = Field(gt=0)


class LoudnessPolicy(_Section):
    method: str = Field(min_length=1)
    target_lufs: float = Field(lt=0)
    tolerance_lu: float = Field(ge=0)
    escalation_lu: float = Field(ge=0)


class TruePeakPolicy(_Section):
    oversampling_factor: int = Field(ge=4)
    ceiling_dbtp: float = Field(le=0)


class ClippingPolicy(_Section):
    full_scale_dbfs: float = Field(le=0, gt=-3)
    min_run_samples: int = Field(ge=2)
    block_min_events: int = Field(ge=1)


class SilencePolicy(_Section):
    threshold_dbfs: float = Field(lt=0)
    max_leading_seconds: float = Field(ge=0)
    max_trailing_seconds: float = Field(ge=0)


class StereoPolicy(_Section):
    warn_below_correlation: float = Field(ge=-1, le=1)
    block_below_correlation: float = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def _block_is_stricter_than_warn(self) -> StereoPolicy:
        if self.block_below_correlation > self.warn_below_correlation:
            raise ValueError("block_below_correlation must not exceed warn_below_correlation")
        return self


class DynamicsPolicy(_Section):
    crest_factor_min_db: float = Field(ge=0)
    crest_factor_max_db: float = Field(gt=0)
    in_range_score: float = Field(ge=0, le=1)
    out_of_range_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> DynamicsPolicy:
        if self.crest_factor_min_db >= self.crest_factor_max_db:
            raise ValueError("crest_factor_min_db must be below crest_factor_max_db")
        return self


class MusicalScoringPolicy(_Section):
    loudness_score_span_lu: float = Field(gt=0)
    loudness_weight: float = Field(ge=0, le=1)
    dynamics_weight: float = Field(ge=0, le=1)
    loudness_confidence: float = Field(ge=0, le=1)
    dynamics_confidence: float = Field(ge=0, le=1)
    composite_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> MusicalScoringPolicy:
        if abs(self.loudness_weight + self.dynamics_weight - 1.0) > 1e-9:
            raise ValueError("loudness_weight + dynamics_weight must equal 1")
        return self


class AudioQaPolicy(BaseModel):
    """The validated audio QA policy. Frozen; identify it by ``policy_version``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    policy_version: str = Field(min_length=1, max_length=32)
    notes: tuple[str, ...] = ()
    decode: DecodePolicy
    format: FormatPolicy
    loudness: LoudnessPolicy
    true_peak: TruePeakPolicy
    clipping: ClippingPolicy
    silence: SilencePolicy
    stereo: StereoPolicy
    dynamics: DynamicsPolicy
    musical_scoring: MusicalScoringPolicy


def parse_audio_qa_policy(raw: str | bytes) -> AudioQaPolicy:
    """Validate a policy document; an invalid one raises, it is never defaulted."""
    return AudioQaPolicy.model_validate(json.loads(raw))


@lru_cache(maxsize=1)
def load_audio_qa_policy() -> AudioQaPolicy:
    """The packaged policy (``audio_qa_policy.json``), validated once per process."""
    raw = resources.files(__package__).joinpath(_POLICY_FILE).read_bytes()
    return parse_audio_qa_policy(raw)
