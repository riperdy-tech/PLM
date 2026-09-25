"""Ingestion package for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from .c4_adapter import consume_c4_event, create_evaluation_from_c4

__all__ = ["consume_c4_event", "create_evaluation_from_c4"]
