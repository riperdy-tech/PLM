"""Structured logging for YouTube publishing."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("youtube_publishing")


def log_publish_event(event_name: str, **kwargs: Any) -> None:
    logger.info("publish_event: %s %s", event_name, kwargs)
