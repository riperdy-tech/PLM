"""Media tools (ffmpeg, ffprobe) for tests marked ``media``.

The dev image pins FFmpeg 7, and CI runs the media tests inside it with
``PLM_REQUIRE_MEDIA_TOOLS=1``. On a host without the tools the tests skip; with that
variable set, a missing tool is a failure, so CI can never pass by skipping them.
"""

from __future__ import annotations

import os
import shutil

import pytest

__all__ = ["require_media_tool"]


def require_media_tool(name: str) -> str:
    """The absolute path of ``name``, or skip (fail when tools are required)."""
    path = shutil.which(name)
    if path is not None:
        return path
    message = f"{name} not found on PATH (the dev image provides it)"
    if os.environ.get("PLM_REQUIRE_MEDIA_TOOLS") == "1":
        pytest.fail(message + "; PLM_REQUIRE_MEDIA_TOOLS=1 forbids skipping")
    pytest.skip(message)
