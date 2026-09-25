"""Gate package for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from .engine import GateEngineResult, run_candidate_evaluation
from .manifest import build_release_manifest

__all__ = ["GateEngineResult", "build_release_manifest", "run_candidate_evaluation"]
