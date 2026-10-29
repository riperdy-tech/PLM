"""Observability metrics for Music Generation Factory (§4.5, §111)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

__all__ = ["MusicMetricsCollector", "metrics"]


@dataclass
class MusicMetricsCollector:
    """In-memory Prometheus-compatible metric counters and latency histograms."""

    generations_total: int = 0
    generations_success: int = 0
    generations_failed: int = 0
    attempts_by_provider: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures_by_provider: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    total_audio_duration_seconds: float = 0.0
    total_credits_spent: float = 0.0
    credits_unknown_attempts: int = 0
    mock_credits_simulated: float = 0.0

    def record_attempt(
        self,
        *,
        provider_id: str,
        success: bool,
        duration_seconds: float = 0.0,
        credits: float | None = None,
        credit_basis: str = "unknown",
    ) -> None:
        self.generations_total += 1
        self.attempts_by_provider[provider_id] += 1
        if success:
            self.generations_success += 1
            self.total_audio_duration_seconds += duration_seconds
            if credit_basis == "provider_reported" and credits is not None:
                self.total_credits_spent += credits
            elif credit_basis == "mock_simulated" and credits is not None:
                self.mock_credits_simulated += credits
            else:
                self.credits_unknown_attempts += 1
        else:
            self.generations_failed += 1
            self.failures_by_provider[provider_id] += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "generations_total": self.generations_total,
            "generations_success": self.generations_success,
            "generations_failed": self.generations_failed,
            "attempts_by_provider": dict(self.attempts_by_provider),
            "failures_by_provider": dict(self.failures_by_provider),
            "total_audio_duration_seconds": round(self.total_audio_duration_seconds, 2),
            "total_credits_spent": round(self.total_credits_spent, 2),
            "credits_unknown_attempts": self.credits_unknown_attempts,
            "mock_credits_simulated": round(self.mock_credits_simulated, 2),
        }


metrics = MusicMetricsCollector()
