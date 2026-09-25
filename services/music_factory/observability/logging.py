"""Structured JSON logging for Music Generation Factory."""

from __future__ import annotations

import logging
import sys

__all__ = ["get_logger"]


def get_logger(name: str = "plm.music_factory") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
