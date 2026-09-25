"""Deterministic fingerprinting and hashing utilities for content genomes and experiments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .experiment import ExperimentVariable
    from .genome import ContentGenome


def _canonical_json(obj: Any) -> str:
    """Format an object into sorted canonical JSON for deterministic hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def compute_genome_fingerprint(genome: ContentGenome) -> str:
    """Compute a deterministic 64-character SHA256 hex digest of a ContentGenome."""
    dumped = genome.model_dump(mode="json")
    canonical = _canonical_json(dumped)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_experiment_fingerprint(
    experiment_id: str,
    arm_id: str,
    variables: Sequence[ExperimentVariable],
) -> str:
    """Compute a deterministic fingerprint of an experiment's design variables."""
    payload = {
        "experiment_id": experiment_id,
        "arm_id": arm_id,
        "variables": [v.model_dump(mode="json") for v in variables],
    }
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_package_hash(
    experiment_id: str,
    treatment_id: str,
    track_spec_ids: Sequence[str],
) -> str:
    """Compute a deterministic content-addressed hash of a materialized ContentPackage."""
    payload = {
        "experiment_id": experiment_id,
        "treatment_id": treatment_id,
        "track_spec_ids": sorted(track_spec_ids),
    }
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
