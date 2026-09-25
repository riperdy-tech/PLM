"""Observability package for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from .logging import get_logger
from .metrics import QualityGateMetricsCollector, metrics

__all__ = ["QualityGateMetricsCollector", "get_logger", "metrics"]
