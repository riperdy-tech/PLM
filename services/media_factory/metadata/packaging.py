"""What a focus video's title says, and the scene its picture shows (owner 2026-11-17).

The title opens with what the video is for and what it sounds like, never a poetic scene
line. The 2026-11-03 growth spec's ``[scenario hook] + [keyword]`` rule was dropped on our
own market data: of the 15 recent long focus videos with the most views per subscriber
(P1, 2026-11-17), about 2 open with a phrase; the rest lead with the function, the genre
and the length or a feature ("Deep Work Music | Ambient Focus Beats for Extreme
Concentration...", "3 Hours of Generative Ambient for Deep Focus · ...", "Hyperfocus
Techno | 3 Hour Deep Work Pomodoro..."). So a title here is built from:

* **the genre** of the video's sub-style, as people search for it (:data:`GENRES`);
* **the length** (``2 Hours``, ``104 Minutes``);
* **activities**, the words people type into YouTube search (autocomplete, 2026-11-03);
* **a feature** listeners filter on (no vocals, no lyrics).

:func:`choose_titles` fills the :data:`FORMATS` so that no title is a template fill of a
recent one (the upload variation check, :mod:`..playlist.variation`, refuses those).

* **Scenes** name what the picture shows, plainly, one per focus world (ADR-0044
  amendment 2026-11-17: everyday places people work in). The description and P6's own
  drawn thumbnails use it; the title does not.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from collections.abc import Collection, Sequence
from typing import Final

__all__ = ["ACTIVITIES", "DEFAULT_GENRE", "FEATURES", "FORMATS", "GENRES", "PURPOSES",
           "SCENES", "SERIES",
           "choose_titles", "genre_for", "scene_for", "series_for"]

SCENES: Final[dict[str, str]] = {
    "rainy_night_desk": "Desk by a Rainy Window at Night",
    "sunrise_home_office": "Home Office at Sunrise",
    "late_night_library": "Quiet Library at Night",
    "city_apartment_night": "City Apartment Above the Lights",
    "snowy_cabin_desk": "Cabin Desk on a Snowy Evening",
    "train_window_journey": "Working by a Train Window",
    "minimal_desk_monolith": "Clean Minimal Desk",
}
"""The plain scene of each focus visual world (P6's :data:`..visuals.worlds.WORLDS`)."""

SERIES: Final[dict[str, tuple[str, str]]] = {
    "rainy_night_desk": ("night_desk", "Night Desk Sessions"),
    "late_night_library": ("night_desk", "Night Desk Sessions"),
    "city_apartment_night": ("night_desk", "Night Desk Sessions"),
    "sunrise_home_office": ("first_light", "First Light Focus"),
    "minimal_desk_monolith": ("first_light", "First Light Focus"),
    "snowy_cabin_desk": ("quiet_places", "Quiet Places"),
    "train_window_journey": ("quiet_places", "Quiet Places"),
}
"""The channel's recurring series (growth spec rank 4): (series id, name) by visual world."""

GENRES: Final[dict[str, str]] = {
    "future_garage_focus": "Future Garage",
    "soft_cyber_electronica": "Soft Electronica",
    "minimal_pulse": "Minimal Electronic",
    "ambient_breakbeat": "Ambient Breakbeat",
    "downtempo_synth": "Downtempo Synth",
    "deep_work_chillstep": "Chillstep",
    "restrained_retro_future": "Retro Synth",
    "organic_electronic": "Organic Electronic",
    "piano_synth_concentration": "Piano & Synth",
}
"""The genre a sub-style's video is titled with: the name people search for."""

DEFAULT_GENRE: Final[str] = "Ambient Electronic"
"""A video without a known sub-style (the brand's genre: calm instrumental electronic)."""

ACTIVITIES: Final[tuple[str, ...]] = (
    "Studying", "Coding", "Reading", "Writing", "Working", "Programming")

PURPOSES: Final[tuple[str, ...]] = ("Deep Focus", "Flow State", "Productivity",
                                    "Concentration")
FEATURES: Final[tuple[str, ...]] = ("No Vocals", "No Lyrics", "Instrumental Only")

FORMATS: Final[tuple[str, ...]] = (
    "Deep Work Music | {genre} for {a} & {b}",
    "{length} of {genre} for {p} · {f}",
    "Focus Music for {a} & {b} — {genre}, {f}",
    "{genre} Focus Music | {length} for {a} & {b}",
    "Concentration Music: {genre} for {a} | {length}, {f}",
    "{genre} for {p} | {length} of Music for {a}",
)
"""Title formats, each led by the function, the genre or the length (see the module
docstring); ``{a}``/``{b}`` are :data:`ACTIVITIES`, ``{p}`` :data:`PURPOSES`, ``{f}``
:data:`FEATURES`."""

_WORD = re.compile(r"[^\W_]+")


def scene_for(visual_world: str | None, fallback: str) -> str:
    """The world's plain scene, or ``fallback`` (the playlist concept) for a world
    outside the focus identity."""
    return SCENES.get(visual_world or "") or fallback.strip()


def genre_for(subgenre: str | None) -> str:
    return GENRES.get(subgenre or "", DEFAULT_GENRE)


def series_for(visual_world: str | None) -> tuple[str, str] | None:
    """The (series id, name) a visual world belongs to, or ``None``."""
    return SERIES.get(visual_world or "")


def _seeded(options: Sequence[str], seed: str) -> list[str]:
    return sorted(options, key=lambda o: hashlib.sha256(f"{seed}:{o}".encode()).digest())


def _too_close(title: str, others: Collection[set[str]], max_difference: int) -> bool:
    words = set(_WORD.findall(title.lower()))
    return any(len(words ^ other) <= max_difference for other in others)


def choose_titles(*, genre: str, length: str, seed: str, recent_titles: Collection[str] = (),
                  count: int = 3, max_difference: int = 2) -> list[str]:
    """``count`` titles in different formats, none within ``max_difference`` words of a
    recent title or of each other (the variation check's template-fill rule); the formats
    and activities are tried in an order varied by ``seed``. Fewer when nothing else fits
    (the variation check then refuses the package, loudly)."""
    taken = [set(_WORD.findall(t.lower())) for t in recent_titles]
    fills = [{"a": a, "b": b, "p": p, "f": f}
             for a, b in itertools.permutations(ACTIVITIES, 2)
             for p in PURPOSES for f in FEATURES]
    chosen: list[str] = []
    for fmt in _seeded(FORMATS, f"{seed}:format"):
        for fill in sorted(fills, key=lambda x: hashlib.sha256(
                f"{seed}:{fmt}:{sorted(x.items())}".encode()).digest()):
            title = fmt.format(genre=genre, length=length, **fill)
            if not _too_close(title, taken, max_difference):
                chosen.append(title)
                taken.append(set(_WORD.findall(title.lower())))
                break
        if len(chosen) == count:
            break
    return chosen
