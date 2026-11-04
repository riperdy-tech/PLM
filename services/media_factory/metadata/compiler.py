"""Metadata, chapter, and tracklist compiler (P6 §13, §45, §178).

Assembles YouTube-compliant chapter markers, tracklist arrays, and metadata
candidates with synthetic media disclosure.

Titles follow the growth spec (2026-11-03, ADR-0045): a scenario or feeling hook from
:mod:`.packaging` plus the words people search for, in three candidates that differ on
one axis (scenario first, keyword first, feeling first) for YouTube's Test & Compare,
e.g. ``Night Shift in the Server Hall — Focus Music for Studying & Coding``. They never
use a numbered-volume, "Official Mix", "Full Album" or lo-fi formula, and never exceed
YouTube's 100 characters. The description follows the spec's layout: a hook line with the
primary search words, a second line of secondary ones, the tracklist (chapters), where
the music and the visuals come from, the AI notice, and at most three hashtags.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from ..domain.models import Chapter, PlaylistTrack
from .localization import localize
from .packaging import choose_activities, choose_hooks, series_for

__all__ = ["HASHTAGS", "TITLE_MAX_CHARS", "MetadataCompilationResult",
           "compile_metadata_and_chapters", "duration_phrase", "functional_title"]

TITLE_MAX_CHARS = 100
"""YouTube's title limit."""

HASHTAGS = "#focusmusic #studymusic #deepwork"
"""At most three (YouTube shows the first three above the title)."""

_TAGS = ("focus music", "study music", "music for studying", "concentration music",
         "deep work music", "coding music", "background music for work",
         "instrumental music no lyrics")
"""Search phrases seen in YouTube's autocomplete (2026-11-03); tags carry little weight,
so they repeat the title's words rather than add new claims."""


def duration_phrase(seconds: float) -> str:
    """``2 Hours``, ``1 Hour`` or ``90 Minutes``: the length a listener plans around."""
    minutes = max(1, round(seconds / 60))
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} Hour" if hours == 1 else f"{hours} Hours"
    return f"{minutes} Minute" if minutes == 1 else f"{minutes} Minutes"


def functional_title(concept: str, suffix: str) -> str:
    """``concept`` + ``suffix``, the concept shortened (never the suffix) to fit 100."""
    room = TITLE_MAX_CHARS - len(suffix)
    concept = concept.strip()
    if len(concept) > room:
        concept = concept[:room - 1].rstrip() + "…"
    return f"{concept}{suffix}"


def _fit(prefix: str, hook: str) -> str:
    """``prefix`` + ``hook``, the hook shortened (never the prefix) to fit 100."""
    room = TITLE_MAX_CHARS - len(prefix)
    hook = hook.strip()
    if len(hook) > room:
        hook = hook[:room - 1].rstrip() + "…"
    return f"{prefix}{hook}"


class MetadataCompilationResult:
    """Outcome of metadata compilation."""

    def __init__(
        self,
        chapters: list[dict[str, Any]],
        tracklist: list[dict[str, Any]],
        metadata_candidates: list[dict[str, Any]],
        chapter_objects: list[Chapter],
        hooks: tuple[str, str] = ("", ""),
    ) -> None:
        self.chapters = chapters
        self.tracklist = tracklist
        self.metadata_candidates = metadata_candidates
        self.chapter_objects = chapter_objects
        self.hooks = hooks
        """(scene, feeling) the titles use; the upload variation check records them."""


def compile_metadata_and_chapters(
    *,
    tracks: list[PlaylistTrack],
    brand_name: str,
    playlist_concept: str,
    ai_disclosure_required: bool = True,
    visual_world: str | None = None,
    seed: str = "",
    recent_hooks: Collection[str] = (),
) -> MetadataCompilationResult:
    """Compile timestamped chapter markers, tracklist, and publishable metadata options.

    ``seed`` (the package id) varies the hooks and search words between videos;
    ``recent_hooks`` are the brand's recent ones, which are not taken again while others
    are left."""
    # 1. Compile chapters from the final timeline
    chapter_objects: list[Chapter] = []
    chapters_json: list[dict[str, Any]] = []

    for idx, t in enumerate(tracks):
        # A chapter starts where its track starts on the final timeline and runs to the
        # next chapter. Nothing is moved to satisfy YouTube's rules; the chapter rules
        # (:mod:`.chapters`) refuse a set that breaks them.
        start_time = t.start_offset_seconds
        end_time = (tracks[idx + 1].start_offset_seconds if idx + 1 < len(tracks)
                    else t.end_offset_seconds)
        title = t.title or f"Track {idx + 1}"

        chapter = Chapter(
            title=title,
            start_seconds=start_time,
            end_seconds=end_time,
            song_id=t.song_id,
        )
        chapter_objects.append(chapter)
        chapters_json.append(
            {
                "title": title,
                "start_seconds": round(start_time, 2),
                "end_seconds": round(end_time, 2),
                "timestamp_str": chapter.format_timestamp(),
                "song_id": t.song_id,
            }
        )

    # 2. Compile Tracklist
    tracklist_json: list[dict[str, Any]] = [
        {
            "position": t.position + 1,
            "song_id": t.song_id,
            "title": t.title or f"Track {t.position + 1}",
            "duration_seconds": round(t.end_offset_seconds - t.start_offset_seconds, 2),
            "source_artifact_id": t.source_artifact_id,
        }
        for t in tracks
    ]

    # 3. Format Chapter timestamps for description
    chapter_lines = [f"{c['timestamp_str']} - {c['title']}" for c in chapters_json]
    chapter_block = "\n".join(chapter_lines)

    # 4. Mandatory YouTube AI Disclosure (P5 §4.1, P6 §4)
    disclosure_statement = (
        "Notice: Sound recording and audiovisual elements were created using synthetic media / AI tools."
        if ai_disclosure_required
        else ""
    )

    # 5. Metadata candidates (growth spec, ADR-0045): three titles, one description.
    length = duration_phrase(tracks[-1].end_offset_seconds if tracks else 0.0)
    scene, feeling = choose_hooks(visual_world, playlist_concept, seed=seed,
                                  recent=recent_hooks)
    first, second, third = choose_activities(seed)
    brand_slug = brand_name.lower().replace(" ", "")
    series = series_for(visual_world)
    titles = {
        "meta_a_scenario": functional_title(scene, f" — Focus Music for {first} & {second}"),
        "meta_b_keyword": _fit(f"{length} of Focus Music for {first} & {second} · ", scene),
        "meta_c_feeling": functional_title(
            feeling, f" · Instrumental Focus Music for {first}"),
    }
    description = "\n".join((
        (f"{scene}: {length.lower()} of calm instrumental electronic music for "
         f"{first.lower()}, {second.lower()} and {third.lower()}."),
        ("No lyrics and no vocals, a soft steady pulse in the background: focus music for "
         "work, studying and late-night concentration."),
        "",
        f"Tracklist:\n{chapter_block}",
        "",
        (f"Music: original instrumentals made with AI music tools, selected and sequenced "
         f"by {brand_name}."),
        f"Visuals: original artwork created for {brand_name}.",
        disclosure_statement,
        f"Curated by {brand_name}: tracks chosen and sequenced for long focus sessions.",
        "" if series is None else f"Series: {series[1]}",
        "",
        HASHTAGS,
    )).replace("\n\n\n", "\n\n").strip()
    # The same translations ride on every candidate: YouTube shows them only to viewers
    # of that language, and Test & Compare tests the English title (ADR-0045).
    localizations = localize(scene, seconds=tracks[-1].end_offset_seconds if tracks else 0.0,
                             chapter_block=chapter_block, brand_name=brand_name)
    metadata_candidates = [
        {
            "variant_id": variant_id,
            "title": title,
            "description": description,
            "tags": [*_TAGS, brand_slug],
            "category_id": "10",  # Music
            "contains_synthetic_media": ai_disclosure_required,
            "localizations": localizations,
            "series": None if series is None else series[0],
        }
        for variant_id, title in titles.items()
    ]

    return MetadataCompilationResult(
        chapters=chapters_json,
        tracklist=tracklist_json,
        metadata_candidates=metadata_candidates,
        chapter_objects=chapter_objects,
        hooks=(scene, feeling),
    )
