"""P3 Domain models and contracts."""

from __future__ import annotations

from .enums import (
    ConstraintType,
    EnergyLevel,
    ExperimentStatus,
    ExperimentType,
    PackageStatus,
    TrackRole,
    VariableClass,
    VocalGender,
    VocalMode,
)
from .experiment import (
    Experiment,
    ExperimentVariable,
    Hypothesis,
    Treatment,
)
from .fingerprint import (
    compute_experiment_fingerprint,
    compute_genome_fingerprint,
    compute_package_hash,
)
from .genome import (
    CANONICAL_DIMENSIONS,
    BpmRange,
    ConstraintEnvelope,
    ContentGenome,
    DurationRange,
    GenomeDimensionValue,
)
from .package import (
    ContentPackage,
    PlaylistSpec,
    ThumbnailSpec,
    TitleSpec,
    TrackSpec,
    VisualSpec,
)

__all__ = [
    "CANONICAL_DIMENSIONS",
    "BpmRange",
    "ConstraintEnvelope",
    "ConstraintType",
    "ContentGenome",
    "ContentPackage",
    "DurationRange",
    "EnergyLevel",
    "Experiment",
    "ExperimentStatus",
    "ExperimentType",
    "ExperimentVariable",
    "GenomeDimensionValue",
    "Hypothesis",
    "PackageStatus",
    "PlaylistSpec",
    "ThumbnailSpec",
    "TitleSpec",
    "TrackRole",
    "TrackSpec",
    "Treatment",
    "VariableClass",
    "VisualSpec",
    "VocalGender",
    "VocalMode",
    "compute_experiment_fingerprint",
    "compute_genome_fingerprint",
    "compute_package_hash",
]
