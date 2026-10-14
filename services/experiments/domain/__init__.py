"""P3 Domain models and contracts."""

from __future__ import annotations

from .enums import (
    ConstraintType,
    EnergyLevel,
    ExperimentStatus,
    ExperimentType,
    FactorDesign,
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
    RandomizationRecord,
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
    "FactorDesign",
    "GenomeDimensionValue",
    "Hypothesis",
    "PackageStatus",
    "PlaylistSpec",
    "RandomizationRecord",
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
