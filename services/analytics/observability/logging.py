"""Structured logging configuration for Phase P9."""

from __future__ import annotations

import logging


def configure_analytics_logger(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the analytics logger."""
    logger = logging.getLogger("plm.analytics")
    logger.setLevel(level)
    return logger
