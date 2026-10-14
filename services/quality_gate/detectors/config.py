"""Declared detector thresholds (``detector_policy.json``), validated (R4, ADR-0034)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any, Final, Literal

from plm_platform.protected_artists import NORMALISATION_VERSION
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "AudioFingerprintParams",
    "DetectorPolicy",
    "DetectorPolicyError",
    "LyricSimilarityParams",
    "load_detector_policy",
    "parse_detector_policy",
]

POLICY_FILE: Final[str] = "detector_policy.json"


class DetectorPolicyError(ValueError):
    """The detector policy data is malformed."""


class AudioFingerprintParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm_version: str = Field(min_length=1)
    sample_rate_hz: int = Field(ge=4000, le=48000)
    n_fft: int = Field(ge=256, le=8192)
    hop_length: int = Field(ge=32)
    peak_neighbourhood_frames: int = Field(ge=3)
    peak_neighbourhood_bins: int = Field(ge=3)
    peak_floor_db: float = Field(gt=0.0)
    max_peaks_per_second: int = Field(ge=1)
    fan_out: int = Field(ge=1)
    pair_dt_min_frames: int = Field(ge=1)
    pair_dt_max_frames: int = Field(ge=1, le=63)
    pair_df_max_bins: int = Field(ge=1)
    offset_tolerance_frames: int = Field(ge=0, le=4)
    min_candidate_hashes: int = Field(ge=1)
    match_min_aligned_hashes: int = Field(ge=1)
    match_min_score: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _consistent(self) -> AudioFingerprintParams:
        if self.hop_length > self.n_fft:
            raise ValueError("hop_length cannot exceed n_fft")
        if self.pair_dt_min_frames > self.pair_dt_max_frames:
            raise ValueError("pair_dt_min_frames cannot exceed pair_dt_max_frames")
        if self.n_fft // 2 + 1 > 1 << 10:
            raise ValueError("n_fft too large for the 10-bit frequency field of a hash")
        return self

    @property
    def fingerprint_version(self) -> str:
        """``algorithm_version`` plus a hash of the extraction parameters.

        Fingerprints are comparable only when this string is equal; changing any
        extraction parameter in the data file changes it.
        """
        extraction = self.model_dump(
            include={
                "sample_rate_hz",
                "n_fft",
                "hop_length",
                "peak_neighbourhood_frames",
                "peak_neighbourhood_bins",
                "peak_floor_db",
                "max_peaks_per_second",
                "fan_out",
                "pair_dt_min_frames",
                "pair_dt_max_frames",
                "pair_df_max_bins",
            }
        )
        digest = hashlib.sha256(json.dumps(extraction, sort_keys=True).encode()).hexdigest()
        return f"{self.algorithm_version}+{digest[:12]}"


class LyricSimilarityParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shingle_size: int = Field(ge=2, le=12)
    min_candidate_shingles: int = Field(ge=1)
    match_min_score: float = Field(gt=0.0, le=1.0)
    match_min_shared_shingles: int = Field(ge=1)

    @property
    def corpus_version(self) -> str:
        """Lyric references are comparable only under the same normalisation and n."""
        return f"{NORMALISATION_VERSION}+n{self.shingle_size}"


class DetectorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    policy_version: str = Field(min_length=1)
    notes: tuple[str, ...] = ()
    audio_fingerprint: AudioFingerprintParams
    lyric_similarity: LyricSimilarityParams


def parse_detector_policy(data: Mapping[str, Any]) -> DetectorPolicy:
    try:
        return DetectorPolicy.model_validate(dict(data))
    except ValidationError as exc:
        raise DetectorPolicyError(f"invalid detector policy: {exc}") from exc


@cache
def load_detector_policy() -> DetectorPolicy:
    """The thresholds shipped next to this module (validated, cached)."""
    text = resources.files(__package__).joinpath(POLICY_FILE).read_text(encoding="utf-8")
    return parse_detector_policy(json.loads(text))
