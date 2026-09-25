"""Evaluators package for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from .lyrics import LyricEvaluationResult, evaluate_lyrics
from .musical import MusicalEvaluationResult, evaluate_musical_qualities
from .policy import PolicyValidationResult, screen_policy_and_risk
from .rights import RightsValidationResult, validate_provenance_and_rights
from .technical import TechnicalValidationResult, validate_technical_audio

__all__ = [
    "LyricEvaluationResult",
    "MusicalEvaluationResult",
    "PolicyValidationResult",
    "RightsValidationResult",
    "TechnicalValidationResult",
    "evaluate_lyrics",
    "evaluate_musical_qualities",
    "screen_policy_and_risk",
    "validate_provenance_and_rights",
    "validate_technical_audio",
]
