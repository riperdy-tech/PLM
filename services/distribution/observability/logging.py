"""Structured logging configuration for Phase P8."""

from __future__ import annotations

import logging


def configure_distribution_logger(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the distribution logger."""
    logger = logging.getLogger("plm.distribution")
    logger.setLevel(level)
    return logger
