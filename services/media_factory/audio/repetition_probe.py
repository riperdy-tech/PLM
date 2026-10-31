"""Read-only within-song repetition proxies (P5 plan §24, owner decision 2026-10-31).

The album-contrast probe compares one track with another. §24 asks whether one track
repeats itself (exact or near-identical segments, excessive loop reuse). This cuts one
song into equal windows and scores every window pair with the same
:mod:`.contrast_probe` features, so a looping song shows windows that score alike.

Diagnostic only, like the contrast probe: windows are cut at fixed times, not at bar
lines, so a loop whose period does not divide the window can score lower than it
sounds; there is no threshold, and nothing here clears or blocks a release.
"""
from __future__ import annotations

import io
from statistics import median
from typing import Final

import soundfile as sf  # type: ignore[import-untyped]

from .contrast_probe import measure_contrast, probe_album_features

__all__ = ["POLICY_VERSION", "probe_song_repetition"]

POLICY_VERSION: Final[str] = "p5-within-song-repetition-v1"
_MAX_WINDOWS: Final[int] = 20  # the contrast probe compares at most 20 inputs


def probe_song_repetition(audio: bytes, *, window_seconds: float = 30.0) -> dict[str, object]:
    """Score every pair of ``window_seconds`` windows of one song; a short tail is dropped."""
    if window_seconds < 2:
        raise ValueError("window_seconds must be at least 2")
    with sf.SoundFile(io.BytesIO(audio)) as reader:
        rate = reader.samplerate
        size = int(window_seconds * rate)
        count = reader.frames // size
        if count < 2 or count > _MAX_WINDOWS:
            raise ValueError(f"song gives {count} windows of {window_seconds}s; need 2..{_MAX_WINDOWS}")
        measured = {}
        for index in range(count):
            block = reader.read(frames=size, dtype="float32", always_2d=True)
            buffer = io.BytesIO()
            sf.write(buffer, block, rate, format="WAV", subtype="FLOAT")
            measured[f"w{index + 1:02d}@{index * window_seconds:g}s"] = measure_contrast(
                buffer.getvalue())
    report = probe_album_features(measured)
    pairs, limitations = report["pairs"], report["limitations"]
    assert isinstance(pairs, list) and isinstance(limitations, list)
    summary: dict[str, dict[str, float] | None] = {}
    for metric in ("rhythm_onset_proxy", "pitch_class_motion_proxy", "energy_arc_proxy"):
        scores = [p["metrics"][metric] for p in pairs if p["metrics"][metric] is not None]
        summary[metric] = (
            {"median": round(median(scores), 6), "max": max(scores)} if scores else None
        )
    return {
        "policy_version": POLICY_VERSION,
        "feature_policy_version": report["policy_version"],
        "evaluation": "diagnostic_only_no_threshold_no_clearance",
        "window_seconds": window_seconds,
        "windows": count,
        "summary": summary,
        "coverage": report["coverage"],
        "pairs": pairs,
        "limitations": [
            *limitations,
            ("windows are cut at fixed times, not bar lines; a loop out of phase "
             "with the window scores lower than it sounds"),
            ("higher window-to-window scores mean more repetition; compare songs "
             "with each other, there is no calibrated limit"),
        ],
    }
