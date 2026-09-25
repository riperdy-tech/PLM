"""Domain enums for Phase P3: Content Genome & Experiment Framework."""

from __future__ import annotations

from enum import StrEnum


class ExperimentType(StrEnum):
    """§16: Types of content experiments."""

    SINGLE_FACTOR = "single_factor"
    MULTI_FACTOR = "multi_factor"
    PORTFOLIO_BATCH = "portfolio_batch"
    EXPLORATORY_PROBE = "exploratory_probe"
    REPLICATION = "replication"
    FOLLOW_UP = "follow_up"


class VariableClass(StrEnum):
    """§14: Classification of variables within an experiment."""

    FIXED = "fixed"
    CONTROLLED = "controlled"
    EXPERIMENTAL = "experimental"
    INCIDENTAL = "incidental"


class ConstraintType(StrEnum):
    """§9: Hierarchy of constraint enforcement."""

    HARD = "hard"
    SOFT = "soft"
    FREE = "free"


class ExperimentStatus(StrEnum):
    """§30: Lifecycle state machine for an experiment."""

    DRAFT = "draft"
    VALIDATED = "validated"
    MATERIALIZED = "materialized"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    QUARANTINED = "quarantined"


class PackageStatus(StrEnum):
    """Lifecycle status for a materialized ContentPackage."""

    DRAFT = "draft"
    MATERIALIZED = "materialized"
    READY = "ready"
    CONSUMED = "consumed"
    REJECTED = "rejected"


class TrackRole(StrEnum):
    """Role of a track within a playlist or release package (§20)."""

    FOCUS = "focus"
    ANCHOR = "anchor"
    SECONDARY = "secondary"
    BRIDGE = "bridge"
    AMBIENT = "ambient"
    INTERLUDE = "interlude"


class VocalMode(StrEnum):
    """Vocal presence style (§7.6)."""

    INSTRUMENTAL = "instrumental"
    VOCAL = "vocal"
    SAMPLED = "sampled"
    SPOKEN_WORD = "spoken_word"
    CHOIR = "choir"


class VocalGender(StrEnum):
    """Vocal delivery characteristics (§7.7)."""

    FEMALE = "female"
    MALE = "male"
    DUET = "duet"
    ANDROGYNOUS = "androgynous"
    SYNTHETIC = "synthetic"
    NONE = "none"


class EnergyLevel(StrEnum):
    """Perceived dynamic energy level (§7.8)."""

    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"
