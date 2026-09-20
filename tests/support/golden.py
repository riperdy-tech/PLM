"""Golden-file comparisons for produced artifacts (P6 rendering depends on this).

A golden test pins the *whole* produced artifact against a stored expectation
file. On mismatch the failure message shows a unified diff (text) or the first
differing byte with a hex window (binary). When the change is intended, the
golden file is regenerated through the documented update path::

    PLM_UPDATE_GOLDEN=1 pytest tests/unit/test_...        # any shell

Any non-empty value other than ``0``/``false``/``no`` enables updating; a
call can also force it with ``update=True`` (e.g. from a one-off script).
After rewriting, the calling test is *skipped*, not passed — a regenerated
expectation is not a verified comparison, so the run must be repeated (and
the diff reviewed) without the variable before the change can be trusted.

Files live under ``tests/fixtures/golden/`` by convention; build paths with
:func:`golden_path`, which keeps tests independent of the working directory.
Comparisons are exact: no normalization happens here on purpose. If a value
is legitimately variable (a wall-clock timestamp, a temp path), normalize it
*inside the producing test* before comparing — a golden file that hides
differences cannot catch regressions.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Final

import pytest

from tests.support.paths import REPO_ROOT, display_path

UPDATE_ENV_VAR: Final[str] = "PLM_UPDATE_GOLDEN"
"""Set this (to anything but 0/false/no) to rewrite golden files instead of comparing."""

GOLDEN_ROOT: Final[Path] = REPO_ROOT / "tests" / "fixtures" / "golden"
"""Conventional home for golden files, relative to the repository root."""

_FALSY: Final[frozenset[str]] = frozenset({"", "0", "false", "no"})
_MAX_DIFF_LINES: Final[int] = 60
_HEX_WINDOW: Final[int] = 16


def update_enabled() -> bool:
    """True when the environment requests golden files be rewritten."""
    return os.environ.get(UPDATE_ENV_VAR, "").strip().lower() not in _FALSY


def golden_path(*parts: str) -> Path:
    """A path under ``tests/fixtures/golden/`` assembled from ``parts``."""
    return GOLDEN_ROOT.joinpath(*parts)


def assert_golden(
    actual: str | bytes,
    golden: str | Path,
    *,
    update: bool | None = None,
    encoding: str = "utf-8",
) -> None:
    """Compare ``actual`` against the golden file at ``golden``; rewrite on update.

    ``golden`` may be absolute, or relative to the repository root (the usual
    case: build it with :func:`golden_path`). ``actual`` decides the mode: a
    ``str`` compares against the file's text, ``bytes`` against its bytes.
    ``update`` overrides the environment switch when given.
    """
    path = _resolve(golden)
    if update_enabled() if update is None else update:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, actual, encoding)
        pytest.skip(
            f"{UPDATE_ENV_VAR}: rewrote {display_path(path)} — inspect the change and "
            "re-run without the variable to verify"
        )
    if not path.is_file():
        pytest.fail(
            f"golden file missing: {display_path(path)}\n"
            f"record it with: {UPDATE_ENV_VAR}=1 pytest <this test>, then review the file",
            pytrace=False,
        )
    if isinstance(actual, bytes):
        expected_bytes = path.read_bytes()
        if actual != expected_bytes:
            raise AssertionError(_bytes_message(expected_bytes, actual, path))
    else:
        expected_text = path.read_text(encoding=encoding)
        if actual != expected_text:
            raise AssertionError(_text_message(expected_text, actual, path))


def _resolve(golden: str | Path) -> Path:
    path = Path(golden)
    return path if path.is_absolute() else REPO_ROOT / path


def _write(path: Path, actual: str | bytes, encoding: str) -> None:
    if isinstance(actual, bytes):
        path.write_bytes(actual)
    else:
        # newline="" keeps the bytes exactly as produced (no platform translation).
        with path.open("w", encoding=encoding, newline="") as handle:
            handle.write(actual)


def _text_message(expected: str, actual: str, path: Path) -> str:
    diff = list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"golden: {display_path(path)}",
            tofile="actual",
            n=3,
            lineterm="",
        )
    )
    body = "".join(diff[: _MAX_DIFF_LINES * 4]).rstrip("\n")
    if len(diff) > _MAX_DIFF_LINES * 4:
        body += f"\n... (diff truncated; {len(diff)} hunks total)"
    return (
        f"golden mismatch: {display_path(path)}\n"
        f"{body}\n"
        f"to accept the new output: {UPDATE_ENV_VAR}=1 pytest <this test>, "
        "then review the diff before committing"
    )


def _bytes_message(expected: bytes, actual: bytes, path: Path) -> str:
    limit = min(len(expected), len(actual))
    offset = next((i for i in range(limit) if expected[i] != actual[i]), limit)
    start = max(0, offset - _HEX_WINDOW)
    expected_window = expected[start : offset + _HEX_WINDOW].hex(" ")
    actual_window = actual[start : offset + _HEX_WINDOW].hex(" ")
    return (
        f"golden mismatch (binary): {display_path(path)}\n"
        f"  first difference at byte {offset}; expected {len(expected)} bytes, "
        f"actual {len(actual)} bytes\n"
        f"  bytes {start}: expected {expected_window}\n"
        f"  bytes {start}: actual   {actual_window}\n"
        f"to accept the new output: {UPDATE_ENV_VAR}=1 pytest <this test>, "
        "then review the diff before committing"
    )
