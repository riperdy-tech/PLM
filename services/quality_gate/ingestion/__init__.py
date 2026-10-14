"""Ingestion package for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from .c4_adapter import CandidateEventRejected, consume_c4_event

__all__ = ["CandidateEventRejected", "consume_c4_event"]
