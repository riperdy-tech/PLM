"""Metadata, chapter, and tracklist compiler (P6 §13, §45, §178).

Assembles YouTube-compliant chapter markers, tracklist arrays, and metadata
candidates with synthetic media disclosure.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Chapter, PlaylistTrack

__all__ = ["MetadataCompilationResult", "compile_metadata_and_chapters"]


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
    # 1. Compile Chapters (YouTube requires chapter 0 to start at 00:00)
    chapter_objects: list[Chapter] = []
    chapters_json: list[dict[str, Any]] = []

    for idx, t in enumerate(tracks):
        # Force first chapter to exactly 0.0s for YouTube compatibility
        start_time = 0.0 if idx == 0 else t.start_offset_seconds
        title = t.title or f"Track {idx + 1}"

        chapter = Chapter(
            title=title,
            start_seconds=start_time,
            end_seconds=t.end_offset_seconds,
            song_id=t.song_id,
        )
        chapter_objects.append(chapter)
        chapters_json.append(
            {
                "title": title,
                "start_seconds": round(start_time, 2),
                "end_seconds": round(t.end_offset_seconds, 2),
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

    # 5. Metadata Candidates
    metadata_candidates = [
        {
            "variant_id": "meta_a_concept_focused",
            "title": f"{playlist_concept} | {brand_name} Official Mix",
            "description": (
                f"Enjoy this curated mix of {playlist_concept}.\n\n"
                f"Tracklist:\n{chapter_block}\n\n"
                f"{disclosure_statement}\n\n"
                f"Curated by {brand_name}."
            ).strip(),
            "tags": ["music", "lofi", "ambient", brand_name.lower().replace(" ", "")],
            "category_id": "10",  # Music
            "contains_synthetic_media": ai_disclosure_required,
        },
        {
            "variant_id": "meta_b_mood_focused",
            "title": f"Relaxing {playlist_concept} [Full Album Mix]",
            "description": (
                f"Immerse yourself in {playlist_concept}.\n\n"
                f"Timestamps:\n{chapter_block}\n\n"
                f"{disclosure_statement}\n\n"
                f"Brought to you by {brand_name}."
            ).strip(),
            "tags": ["relaxing", "study music", "beats", brand_name.lower().replace(" ", "")],
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
