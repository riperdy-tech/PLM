"""Metadata, chapter, and tracklist compiler (P6 §13, §45, §178).

Assembles YouTube-compliant chapter markers, tracklist arrays, and metadata
candidates with synthetic media disclosure.

Titles are functional, for the focus-music identity (ADR-0044): what the video is for
and how long it runs, e.g. ``Deep Work Signal — 2 Hours of Instrumental Electronic
Music for Coding & Focus``. They never use a numbered-volume, "Official Mix", "Full
Album" or lo-fi formula, and never exceed YouTube's 100 characters.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Chapter, PlaylistTrack

__all__ = ["TITLE_MAX_CHARS", "MetadataCompilationResult", "compile_metadata_and_chapters",
           "duration_phrase", "functional_title"]

TITLE_MAX_CHARS = 100
"""YouTube's title limit."""


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


class MetadataCompilationResult:
    """Outcome of metadata compilation."""

    def __init__(
        self,
        chapters: list[dict[str, Any]],
        tracklist: list[dict[str, Any]],
        metadata_candidates: list[dict[str, Any]],
        chapter_objects: list[Chapter],
    ) -> None:
        self.chapters = chapters
        self.tracklist = tracklist
        self.metadata_candidates = metadata_candidates
        self.chapter_objects = chapter_objects


def compile_metadata_and_chapters(
    *,
    tracks: list[PlaylistTrack],
    brand_name: str,
    playlist_concept: str,
    ai_disclosure_required: bool = True,
) -> MetadataCompilationResult:
    """Compile timestamped chapter markers, tracklist, and publishable metadata options."""
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

    # 5. Metadata Candidates: functional titles, focus-identity tags (ADR-0044).
    length = duration_phrase(tracks[-1].end_offset_seconds if tracks else 0.0)
    brand_slug = brand_name.lower().replace(" ", "")
    curation = f"Curated by {brand_name}: tracks chosen and sequenced for long focus sessions."
    metadata_candidates = [
        {
            "variant_id": "meta_a_concept_focused",
            "title": functional_title(
                playlist_concept,
                f" — {length} of Instrumental Electronic Music for Coding & Focus"),
            "description": (
                f"{length} of instrumental electronic music for coding, programming and "
                f"deep work: {playlist_concept}. No vocals, a steady pulse, one long "
                "session.\n\n"
                f"Tracklist:\n{chapter_block}\n\n"
                f"{disclosure_statement}\n\n"
                f"{curation}"
            ).strip(),
            "tags": ["deep focus", "coding music", "programming music", "instrumental music",
                     "electronic music", "study music", "deep work", brand_slug],
            "category_id": "10",  # Music
            "contains_synthetic_media": ai_disclosure_required,
        },
        {
            "variant_id": "meta_b_mood_focused",
            "title": functional_title(
                playlist_concept,
                f" — {length} Deep Focus Electronic Music for Programming & Work"),
            "description": (
                f"Deep focus music for programming and concentrated work: {length} of "
                f"{playlist_concept}, instrumental electronic and free of vocals.\n\n"
                f"Timestamps:\n{chapter_block}\n\n"
                f"{disclosure_statement}\n\n"
                f"{curation}"
            ).strip(),
            "tags": ["focus music", "music for coding", "programming music", "instrumental",
                     "electronic", "study", "work", brand_slug],
            "category_id": "10",
            "contains_synthetic_media": ai_disclosure_required,
        },
    ]

    return MetadataCompilationResult(
        chapters=chapters_json,
        tracklist=tracklist_json,
        metadata_candidates=metadata_candidates,
        chapter_objects=chapter_objects,
    )
