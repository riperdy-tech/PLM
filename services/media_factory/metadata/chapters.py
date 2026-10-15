"""YouTube chapter rules (P6 §21, R4).

YouTube shows chapters only when the description's timestamps satisfy its rules, and a
package whose chapters break them would publish a description that does not do what it
claims. The rules, as declared data (:data:`CHAPTER_RULES`):

* the first chapter starts at ``0:00`` (``chapters_first_not_at_zero``);
* there are at least three chapters (``chapters_too_few``);
* each chapter lasts at least ten seconds (``chapter_too_short``);
* timestamps ascend strictly (``chapters_not_ascending``);
* every title is non-empty (``chapter_title_empty``) and at most
  ``max_title_chars`` long (``chapter_title_too_long``).

The checks run on the whole-second timestamps written into the description, since that
is what YouTube reads. Any violation blocks the package; P6 never drops or edits a
chapter to make the set pass.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from ..domain.models import Chapter
from ..errors import MediaAssemblyError

__all__ = [
    "CHAPTER_RULES",
    "ChapterRules",
    "ChapterViolation",
    "chapter_violations",
    "require_valid_chapters",
]


@dataclass(frozen=True, slots=True)
class ChapterRules:
    version: str = "yt-chapters-v1"
    min_chapters: int = 3
    min_chapter_seconds: int = 10
    max_title_chars: int = 100


CHAPTER_RULES: Final = ChapterRules()


@dataclass(frozen=True, slots=True)
class ChapterViolation:
    code: str
    index: int
    detail: str


def chapter_violations(chapters: Sequence[Chapter], total_duration_seconds: float,
                       rules: ChapterRules = CHAPTER_RULES) -> list[ChapterViolation]:
    """Every rule the chapters break (empty when they are valid)."""
    found: list[ChapterViolation] = []
    if len(chapters) < rules.min_chapters:
        found.append(ChapterViolation(
            "chapters_too_few", -1,
            f"{len(chapters)} chapters; YouTube needs at least {rules.min_chapters}"))
    stamps = [math.floor(c.start_seconds) for c in chapters]
    if chapters and stamps[0] != 0:
        found.append(ChapterViolation(
            "chapters_first_not_at_zero", 0, f"first chapter starts at {stamps[0]}s"))
    end = math.floor(total_duration_seconds)
    for i, chapter in enumerate(chapters):
        if i and stamps[i] <= stamps[i - 1]:
            found.append(ChapterViolation(
                "chapters_not_ascending", i, f"{stamps[i]}s follows {stamps[i - 1]}s"))
        following = stamps[i + 1] if i + 1 < len(stamps) else end
        if following - stamps[i] < rules.min_chapter_seconds:
            found.append(ChapterViolation(
                "chapter_too_short", i,
                f"chapter {i + 1} lasts {following - stamps[i]}s "
                f"(minimum {rules.min_chapter_seconds}s)"))
        title = chapter.title.strip()
        if not title:
            found.append(ChapterViolation("chapter_title_empty", i, f"chapter {i + 1}"))
        elif len(title) > rules.max_title_chars:
            found.append(ChapterViolation(
                "chapter_title_too_long", i,
                f"chapter {i + 1} title has {len(title)} characters "
                f"(maximum {rules.max_title_chars})"))
    return found


def require_valid_chapters(chapters: Sequence[Chapter], total_duration_seconds: float,
                           rules: ChapterRules = CHAPTER_RULES) -> None:
    """Refuse with the first violation's code; the message lists all of them."""
    found = chapter_violations(chapters, total_duration_seconds, rules)
    if found:
        raise MediaAssemblyError(
            found[0].code,
            f"chapters break YouTube rules ({rules.version}): "
            + "; ".join(f"{v.code}: {v.detail}" for v in found),
            ["chapters"],
        )
