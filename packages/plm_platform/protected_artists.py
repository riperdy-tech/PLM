"""The protected-artist policy shared by P4's prompt builder and P5's policy screen (R4).

One file, ``plm_platform/policy/protected_artists.json``, one loader. P4 refuses to build
a generation prompt that names a protected artist, so nothing reaches a provider; P5
screens the prompt, genre tags and lyrics it receives on C4 with the same list and the
same normalisation, so both phases agree on what a match is.

Normalisation (:func:`normalise_for_screening`): Unicode NFKD, combining marks dropped,
casefolded, and every run of characters that is not a letter or digit becomes one space.
A name matches only as a whole token sequence: ``"Beyoncé"`` matches ``"beyonce"`` and
``"Drake-style"`` matches ``drake``, but ``"mandrake"`` does not.

The list is a screening control, not a register of every protected person. An artist
absent from it is not cleared by its absence (ADR-0034).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

__all__ = [
    "NORMALISATION_VERSION",
    "POLICY_FILE",
    "ProtectedArtist",
    "ProtectedArtistPolicy",
    "ProtectedArtistPolicyError",
    "ScreeningHit",
    "ScreeningResult",
    "load_protected_artist_policy",
    "normalise_for_screening",
    "parse_protected_artist_policy",
]

POLICY_PACKAGE: Final[str] = "plm_platform.policy"
POLICY_FILE: Final[str] = "protected_artists.json"
NORMALISATION_VERSION: Final[str] = "nfkd-casefold-alnum-v1"
"""Names the normalisation below; a change to it is a new version."""

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def normalise_for_screening(text: str) -> str:
    """NFKD, drop combining marks, casefold, non-alphanumerics to single spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = stripped.casefold()
    # Only ASCII letters and digits survive, so the result never depends on locale.
    # A name in a non-Latin script normalises to nothing and is refused by the policy
    # validator: such names must be listed in Latin transliteration (ADR-0034).
    return " ".join(_NON_ALNUM.sub(" ", folded).split())


class ProtectedArtistPolicyError(ValueError):
    """The protected-artist policy data is malformed or would screen nothing."""


class ProtectedArtist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artist_id: str = Field(min_length=1)
    names: tuple[str, ...] = Field(min_length=1)

    @field_validator("artist_id")
    @classmethod
    def _artist_id(cls, value: str) -> str:
        if not _IDENTIFIER.match(value):
            raise ValueError(f"artist_id must be a snake_case identifier: {value!r}")
        return value

    @field_validator("names")
    @classmethod
    def _names_normalise_to_something(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        empty = [name for name in value if not normalise_for_screening(name)]
        if empty:
            raise ValueError(f"names that normalise to nothing cannot be screened: {empty}")
        return value


@dataclass(frozen=True, slots=True)
class ScreeningHit:
    """One listed term found in the screened text."""

    reason_code: str
    term_id: str
    """The artist id, or the marker itself for an AI-policy marker."""
    matched: str
    """The normalised form that matched."""


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    policy_version: str
    hits: tuple[ScreeningHit, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.hits)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(sorted({hit.reason_code for hit in self.hits}))

    def term_ids(self, reason_code: str) -> tuple[str, ...]:
        return tuple(sorted({h.term_id for h in self.hits if h.reason_code == reason_code}))


class ProtectedArtistPolicy(BaseModel):
    """The versioned list of protected artists and AI-policy conflict markers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    policy_version: str = Field(min_length=1)
    notes: tuple[str, ...] = ()
    artist_reason_code: str
    ai_policy_reason_code: str
    artists: tuple[ProtectedArtist, ...] = Field(min_length=1)
    ai_policy_conflict_markers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _well_formed(self) -> ProtectedArtistPolicy:
        for code in (self.artist_reason_code, self.ai_policy_reason_code):
            if not _REASON_CODE.match(code):
                raise ValueError(f"reason codes must be UPPER_SNAKE_CASE: {code!r}")
        ids = [artist.artist_id for artist in self.artists]
        if len(set(ids)) != len(ids):
            raise ValueError("an artist_id is listed twice")
        empty = [m for m in self.ai_policy_conflict_markers if not normalise_for_screening(m)]
        if empty:
            raise ValueError(f"markers that normalise to nothing cannot be screened: {empty}")
        return self

    def screen(self, *texts: str | None) -> ScreeningResult:
        """Screen each given text separately; ``None`` and empty texts contribute nothing.

        Texts are not joined, so a name cannot be assembled across two of them.
        """
        hits: list[ScreeningHit] = []
        for text in texts:
            if not text:
                continue
            haystack = f" {normalise_for_screening(text)} "
            for artist in self.artists:
                for name in artist.names:
                    needle = normalise_for_screening(name)
                    if f" {needle} " in haystack:
                        hits.append(
                            ScreeningHit(self.artist_reason_code, artist.artist_id, needle)
                        )
            for marker in self.ai_policy_conflict_markers:
                needle = normalise_for_screening(marker)
                if f" {needle} " in haystack:
                    hits.append(ScreeningHit(self.ai_policy_reason_code, needle, needle))
        ordered = sorted(set(hits), key=lambda h: (h.reason_code, h.term_id, h.matched))
        return ScreeningResult(policy_version=self.policy_version, hits=tuple(ordered))


def parse_protected_artist_policy(data: Mapping[str, Any]) -> ProtectedArtistPolicy:
    """Validate policy data; raises :class:`ProtectedArtistPolicyError` naming the defect."""
    try:
        return ProtectedArtistPolicy.model_validate(dict(data))
    except ValidationError as exc:
        raise ProtectedArtistPolicyError(f"invalid protected-artist policy: {exc}") from exc


@cache
def load_protected_artist_policy() -> ProtectedArtistPolicy:
    """The policy shipped in ``plm_platform/policy/protected_artists.json`` (cached)."""
    text = resources.files(POLICY_PACKAGE).joinpath(POLICY_FILE).read_text(encoding="utf-8")
    return parse_protected_artist_policy(json.loads(text))
