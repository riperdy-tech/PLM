# services/market_intelligence/normalization/canonical.py
"""§22: text normalisation and provider-id validation.

Normalisation must not remove meaningful musical terms — it folds case, Unicode,
whitespace and punctuation only. The original title is always retained beside the
normalised form; nothing here overwrites evidence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Final

__all__ = [
    "CHANNEL_ID_RE", "PLAYLIST_ID_RE", "VIDEO_ID_RE", "channel_canonical_url",
    "is_valid_channel_id", "is_valid_video_id", "normalize_text", "text_fingerprint",
    "video_canonical_url",
]

# Real YouTube video ids are exactly 11 URL-safe characters; anything else is
# malformed and is quarantined, never repaired (§22).
VIDEO_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE: Final[re.Pattern[str]] = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{13,43}$")

_APOSTROPHES: Final[re.Pattern[str]] = re.compile(r"['\u2018\u2019\u02bc]")
_PUNCT_RUN: Final[re.Pattern[str]] = re.compile(r"[^\w&-]+", flags=re.UNICODE)
_WHITESPACE_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")


def normalize_text(raw: str) -> str:
    """NFKC → casefold → drop apostrophes → punctuation runs to single spaces.

    Emoji and other symbol characters are dropped (they carry no musical term);
    `&` and `-` survive because `r&b` and `late-night` are terms. Apostrophes are
    removed, never spaced, so `R’n’B` folds onto `rnb` — not `r n b`.
    """
    folded = unicodedata.normalize("NFKC", raw).casefold()
    unspaced = _APOSTROPHES.sub("", folded)
    spaceless = _PUNCT_RUN.sub(" ", unspaced)
    return _WHITESPACE_RUN.sub(" ", spaceless).strip()


def text_fingerprint(raw: str) -> str:
    """sha256 over the normalised text — the dedupe key for non-ID concepts (§22)."""
    return hashlib.sha256(normalize_text(raw).encode("utf-8")).hexdigest()


def is_valid_video_id(value: str) -> bool:
    return VIDEO_ID_RE.match(value) is not None


def is_valid_channel_id(value: str) -> bool:
    return CHANNEL_ID_RE.match(value) is not None


def video_canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def channel_canonical_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"
