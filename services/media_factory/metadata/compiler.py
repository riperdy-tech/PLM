"""Metadata, chapter, and tracklist compiler (P6 §13, §45, §178).

Assembles YouTube-compliant chapter markers, tracklist arrays, and metadata
candidates with synthetic media disclosure.

Titles lead with what the video is for and what it sounds like (owner 2026-11-17, on
P1's market data; see :mod:`.packaging`): the function, the sub-style's genre, the length
or a feature, e.g. ``Deep Work Music | Retro Synth for Coding & Writing``, in three
candidates of different formats for YouTube's Test & Compare. No poetic scene line opens
a title. They never use a numbered-volume, "Official Mix", "Full Album" or lo-fi formula,
and never exceed YouTube's 100 characters. The description follows the growth spec's
layout: a line with the scene, the length and the primary search words, a second line of
secondary ones, the tracklist (chapters), the AI notice, and at most three hashtags.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from typing import Any

from ..domain.models import Chapter, PlaylistTrack
from .localization import localize
from .packaging import ACTIVITIES, choose_titles, genre_for, scene_for, series_for

__all__ = ["HASHTAGS", "TITLE_MAX_CHARS", "MetadataCompilationResult",
           "compile_metadata_and_chapters", "duration_phrase"]

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
        """(scene, genre): the scene the picture and description show, and the genre the
        titles name; the upload variation check records them."""


def compile_metadata_and_chapters(
    *,
    tracks: list[PlaylistTrack],
    brand_name: str,
    playlist_concept: str,
    ai_disclosure_required: bool = True,
    visual_world: str | None = None,
    subgenre: str | None = None,
    seed: str = "",
    recent_titles: Collection[str] = (),
) -> MetadataCompilationResult:
    """Compile timestamped chapter markers, tracklist, and publishable metadata options.

    ``subgenre`` names the genre the titles carry; ``seed`` (the package id) varies the
    formats and activities between videos; ``recent_titles`` are the brand's recent
    titles, which no new title may be a template fill of."""
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

    # 4. Metadata candidates: three titles, one description.
    length = duration_phrase(tracks[-1].end_offset_seconds if tracks else 0.0)
    scene = scene_for(visual_world, playlist_concept)
    genre = genre_for(subgenre)
    first, second, third = sorted(ACTIVITIES, key=lambda a: hashlib.sha256(
        f"{seed}:{a}".encode()).digest())[:3]
    brand_slug = brand_name.lower().replace(" ", "")
    series = series_for(visual_world)
    chosen = choose_titles(genre=genre, length=length, seed=seed, recent_titles=recent_titles)
    if not chosen:  # every format is a template fill: the variation check refuses it
        chosen = [f"Deep Work Music | {genre} for {first} & {second}"]
    titles = {f"meta_{letter}": title[:TITLE_MAX_CHARS]
              for letter, title in zip("abc", chosen, strict=False)}
    description = "\n".join((
        (f"{scene}: {length.lower()} of calm instrumental {genre.lower()} for "
         f"{first.lower()}, {second.lower()} and {third.lower()}."),
        ("No lyrics and no vocals, a soft steady pulse in the background: focus music for "
         "work, studying and late-night concentration."),
        "",
        f"Tracklist:\n{chapter_block}",
        "",
        HASHTAGS,
    ))
    # The same translations ride on every candidate: YouTube shows them only to viewers
    # of that language, and Test & Compare tests the English title (ADR-0045).
    localizations = localize(visual_world, genre=genre,
                             seconds=tracks[-1].end_offset_seconds if tracks else 0.0,
                             chapter_block=chapter_block)
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
        hooks=(scene, genre),
    )
