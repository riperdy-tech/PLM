"""Path anchors for the test harness (kept import-free to avoid cycles).

``tests/support/__init__.py`` re-exports both names, but the helper modules
import from here directly: importing ``tests.support`` while it is still
initialising would be circular.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
"""Repository root, derived from this file — tests must never depend on the cwd."""


def display_path(path: Path) -> str:
    """Render a path relative to the repository root when possible (for messages)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
