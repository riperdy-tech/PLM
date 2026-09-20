"""Brand-scoped ID conventions (ruling R19) and the ``<name>-vN`` versioned identifier shape.

R19: ``brand_id`` is a first-class field everywhere; one brand is seeded for the first
build. IDs minted by services are opaque strings whose uniqueness scope is the brand —
cross-brand work keys on ``(brand_id, id)``, never on the bare id. Domain models should
inherit :class:`BrandScoped` (or carry a ``brand_id: BrandId`` field) instead of burying
the brand inside a payload.

Versioned identifiers: the ``<name>-vN`` shape used across the specs (``p1-v1``,
``genome-v1``, ``youtube-policy-v1``). The underscore form (``revenue_v2``) also appears
in the corpus but is not covered here; the helpers implement the hyphen form the W0.3
brief pins down.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, StringConstraints

__all__ = [
    "VERSIONED_ID_PATTERN",
    "BrandId",
    "BrandScoped",
    "format_versioned_id",
    "is_versioned_id",
    "parse_versioned_id",
]

_BRAND_ID_PATTERN: Final[str] = r"^[a-z0-9]+(_[a-z0-9]+)*$"
BrandId = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=_BRAND_ID_PATTERN)
]
"""A brand slug: lowercase alphanumeric segments joined by single underscores.

Matches the corpus example ``neon_seoul`` (P6 §4). Widen this pattern deliberately if a
brand ever needs a different character set.
"""

VERSIONED_ID_PATTERN: Final[str] = (
    r"^(?P<name>[a-z0-9]+(-[a-z0-9]+)*)-v(?P<version>[1-9][0-9]*)$"
)
"""The ``<name>-vN`` shape (``p1-v1``, ``youtube-policy-v3``); ``N`` starts at 1."""

_VERSIONED_ID_RE: Final[re.Pattern[str]] = re.compile(VERSIONED_ID_PATTERN)
_VERSION_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class BrandScoped(BaseModel):
    """R19 base model: ``brand_id`` comes first and is required.

    Subclasses add their own fields; config is frozen and extra-forbidding so brand-scoped
    artifacts cannot silently grow fields. Subclasses may relax ``model_config``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    brand_id: BrandId


def format_versioned_id(name: str, version: int) -> str:
    """Format and validate a versioned identifier: ``format_versioned_id("p1", 1) == "p1-v1"``.

    Raises ``ValueError`` for a malformed name or a version below 1.
    """
    if version < 1:
        raise ValueError(f"version must be >= 1, got {version}")
    if not _VERSION_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid versioned identifier name {name!r}: expected lowercase alphanumeric "
            "hyphen-separated segments (e.g. 'p1', 'youtube-policy')"
        )
    return f"{name}-v{version}"


def parse_versioned_id(value: str) -> tuple[str, int]:
    """Split a versioned identifier into ``(name, version)``; raises ``ValueError`` if malformed."""
    match = _VERSIONED_ID_RE.fullmatch(value)
    if match is None:
        raise ValueError(
            f"{value!r} is not a `<name>-vN` versioned identifier "
            "(e.g. 'p1-v1', 'youtube-policy-v3')"
        )
    return (match.group("name"), int(match.group("version")))


def is_versioned_id(value: str) -> bool:
    """True when ``value`` has the ``<name>-vN`` shape."""
    return _VERSIONED_ID_RE.fullmatch(value) is not None
