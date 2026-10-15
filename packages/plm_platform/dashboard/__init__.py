"""PLM dashboard: the six PART XVII views over live data; demo data only behind ``--demo``."""

from __future__ import annotations

from .actions import ExceptionActions, ReviewerConfig, TemporalSignaller
from .app import create_app, create_demo_app, create_live_app
from .live import LiveDashboard
from .orchestrator import DemoOrchestrator
from .service import DEMO_DATA_SOURCE, DashboardService
from .sources import LIVE_DATA_SOURCE

__all__ = [
    "DEMO_DATA_SOURCE",
    "LIVE_DATA_SOURCE",
    "DashboardService",
    "DemoOrchestrator",
    "ExceptionActions",
    "LiveDashboard",
    "ReviewerConfig",
    "TemporalSignaller",
    "create_app",
    "create_demo_app",
    "create_live_app",
]
