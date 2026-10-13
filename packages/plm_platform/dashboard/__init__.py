"""PLM visual dashboard. Serves fixed demo data until R5 wires it to the pipeline."""

from __future__ import annotations

from .app import create_app
from .orchestrator import DemoOrchestrator
from .service import DATA_SOURCE, DashboardService

__all__ = ["DATA_SOURCE", "DashboardService", "DemoOrchestrator", "create_app"]
