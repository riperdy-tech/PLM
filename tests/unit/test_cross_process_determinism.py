"""Gate R2: the same inputs produce the same outputs in two separate interpreters.

Each run gets a different PYTHONHASHSEED, so any id, seed, checksum or decision derived
from builtin `hash()`, set order or other per-process state differs between them.
What this proves: P2 simulation draws, P3 experiment/package/track-spec output and P4
planning plus mock generation are replayable. It does not cover live providers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_probe(hash_seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    completed = subprocess.run(
        [sys.executable, "-m", "tests.support.determinism_probe"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=90,
    )
    return completed.stdout


def test_pipeline_slice_is_identical_across_processes() -> None:
    first = _run_probe("1")
    second = _run_probe("4242")
    first_digest, _, first_body = first.partition("\n")
    second_digest, _, second_body = second.partition("\n")
    assert first_body, "probe produced no output"
    assert first_digest == second_digest, _first_difference(first_body, second_body)


def _first_difference(left: str, right: str) -> str:
    for index, (a, b) in enumerate(zip(left, right, strict=False)):
        if a != b:
            start = max(0, index - 120)
            return f"outputs diverge at {index}:\n  run 1: {left[start:index + 120]}\n  run 2: {right[start:index + 120]}"
    return f"outputs differ in length: {len(left)} vs {len(right)}"
