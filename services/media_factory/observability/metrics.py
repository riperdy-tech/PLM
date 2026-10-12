"""Observability metrics for Media Factory (Phase P6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.enums import PackageStatus, RenderJobStatus

__all__ = ["MediaFactoryMetricsCollector", "metrics"]


@dataclass
class MediaFactoryMetricsCollector:
    """In-memory metrics collector for playlist, visual, and rendering operations."""

    packages_total: int = 0
    packages_ready: int = 0
    packages_failed: int = 0
    render_jobs_total: int = 0
    render_jobs_completed: int = 0
    render_jobs_failed: int = 0
    render_seconds_total: float = 0.0

    def record_package(self, status: PackageStatus) -> None:
        self.packages_total += 1
        if status == PackageStatus.READY_FOR_PUBLISH:
            self.packages_ready += 1
        elif status == PackageStatus.FAILED:
            self.packages_failed += 1

    def record_render_job(self, status: RenderJobStatus, duration_seconds: float = 0.0) -> None:
        self.render_jobs_total += 1
        self.render_seconds_total += duration_seconds
        if status == RenderJobStatus.COMPLETED:
            self.render_jobs_completed += 1
        elif status == RenderJobStatus.FAILED:
            self.render_jobs_failed += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "packages_total": self.packages_total,
            "packages_ready": self.packages_ready,
            "packages_failed": self.packages_failed,
            "render_jobs_total": self.render_jobs_total,
            "render_jobs_completed": self.render_jobs_completed,
            "render_jobs_failed": self.render_jobs_failed,
            "render_seconds_total": round(self.render_seconds_total, 2),
        }


metrics = MediaFactoryMetricsCollector()
