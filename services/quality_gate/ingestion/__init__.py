"""Ingestion package for Phase P5 (Quality, Rights, Provenance & Release-Gate Engine)."""

from .artifact_fetch import (
    ArtifactFetcher,
    ArtifactFetchRefused,
    MinioArtifactFetcher,
    staged_audio,
)
from .c4_adapter import CandidateEventRejected, consume_c4_event

__all__ = [
    "ArtifactFetchRefused",
    "ArtifactFetcher",
    "CandidateEventRejected",
    "MinioArtifactFetcher",
    "consume_c4_event",
    "staged_audio",
]
