"""Scenario hooks and search words for focus-video titles (growth spec 2026-11-03).

The spec's title rule is ``[scenario/feeling hook] + [functional keyword]``, the hook in
the first ~40 characters (mobile and TV cut titles there), three candidates per video that
differ on one axis (scenario first, keyword first, feeling first) for YouTube's Test &
Compare. Generic head terms alone ("study music") lose to the giants; a scenario on top
of them is what a viewer recognizes.

* **Scenes** describe what the video shows: each focus visual world (ADR-0044) has its
  own, so a title never promises a picture the video does not have.
* **Feelings** describe the use, not the sound, and claim nothing about the listener.
* **Activities** are the words people type into YouTube search, checked against
  YouTube's autocomplete on 2026-11-03 ("music for studying", "music for coding",
  "deep work music", "concentration music", "music to focus on reading", "music to focus
  on writing").

:func:`choose_hooks` takes a scene and a feeling the brand's recent packages did not use,
so the upload variation check (:mod:`..playlist.variation`) never meets a repeated hook.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from typing import Final

__all__ = ["ACTIVITIES", "FEELINGS", "HOOK_MAX_CHARS", "SCENES", "choose_activities",
           "choose_hooks"]

HOOK_MAX_CHARS: Final[int] = 40

SCENES: Final[dict[str, tuple[str, ...]]] = {
    "architect_studio_night": ("Late Night in the Architect's Studio",
                               "Amber Lamps in a Quiet Studio", "After Hours at the Studio"),
    "drafting_table_dawn": ("Dawn at the Drafting Table", "First Light, First Draft",
                            "Early Hours at the Drafting Table"),
    "minimal_desk_monolith": ("One Desk, One Task, No Noise", "A Clean Desk Before Deep Work",
                              "Minimal Desk, Clear Head"),
    "observatory_workspace": ("Working Late at the Observatory", "Night Shift at the Observatory",
                              "Stars Overhead, Work on Screen"),
    "server_hall_glow": ("Night Shift in the Server Hall", "Server Lights and Quiet Focus",
                         "Humming Racks, Deep Work"),
    "circuit_grid_abstract": ("Inside the Circuit Grid", "Signal Paths and Steady Focus",
                              "Lost in the Grid, in the Flow"),
    "blueprint_hall": ("Blueprints Across the Hall", "Planning Hours in the Blueprint Hall",
                       "Big Plans, Quiet Hall"),
}
"""Scenario hooks per focus visual world (P6's :data:`..visuals.worlds.WORLDS`)."""

FEELINGS: Final[tuple[str, ...]] = (
    "Headphones On, World Off", "When the Noise Fades and Work Begins",
    "For the Hour It Finally Clicks", "Settle In and Get It Done",
    "Stay in the Flow a Little Longer", "The Quiet Before the Breakthrough",
    "One More Hour of Focus", "Clear Head, Steady Hands",
)

ACTIVITIES: Final[tuple[str, ...]] = (
    "Studying", "Coding", "Deep Work", "Concentration", "Reading", "Writing")


def _fresh(options: Collection[str], seed: str, recent: Collection[str]) -> str:
    ordered = sorted(options)
    unused = [o for o in ordered if o not in recent] or ordered
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return unused[int.from_bytes(digest[:4], "big") % len(unused)]


def choose_hooks(visual_world: str | None, fallback_scene: str, *, seed: str,
                 recent: Collection[str] = ()) -> tuple[str, str]:
    """(scene, feeling) for a package: hooks absent from ``recent`` where any are left.

    A world without scenes (one outside the focus identity) uses ``fallback_scene`` (the
    playlist concept)."""
    scenes = SCENES.get(visual_world or "")
    scene = _fresh(scenes, f"{seed}:scene", recent) if scenes else fallback_scene.strip()
    return scene, _fresh(FEELINGS, f"{seed}:feeling", recent)


def choose_activities(seed: str) -> tuple[str, str, str]:
    """Three different search activities, varied per package."""
    digest = hashlib.sha256(f"{seed}:activities".encode()).digest()
    pool = list(ACTIVITIES)
    picked = []
    for byte in digest[:3]:
        picked.append(pool.pop(byte % len(pool)))
    return picked[0], picked[1], picked[2]
