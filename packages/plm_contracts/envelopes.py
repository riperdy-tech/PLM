"""Response envelopes.

* :class:`Envelope` — P9 §89 metric API semantics: every response carries ``as_of``,
  ``source_freshness``, ``quality_status``, ``metric_version`` and ``currency`` (``None``
  where not applicable — never a guessed default); aggregated metrics add
  ``observation_count``.
* :class:`Page` — cursor pagination for collection APIs (P1 §25, P9 §141).

Reconciliation (W0.3): P1's ``as_of`` / ``score_version`` and P9's collection parameters
are not duplicated per-payload. The envelope carries ``as_of`` and the version field
(``metric_version``, e.g. P1's ``p1-v1``); the page carries the items and the cursor.
P9 §141's ``filters`` and ``sort`` are query parameters and stay with each service's API
layer.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from .enums import QualityStatus, SourceFreshness

__all__ = ["CurrencyCode", "Envelope", "Page"]

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
"""ISO-4217-style reporting currency code (three uppercase letters).

Not a closed enum: P9 §23 makes the reporting currency configuration-driven (initially
``KRW``); the specs name ``USD`` and ``KRW`` as examples, not as the universe.
"""


class Envelope[T](BaseModel):
    """P9 §89 metric response envelope; ``data`` wraps the payload of type ``T``.

    ``data`` is the W0.3 name for the wrapped payload (no phase spec names it) and is the
    one field here that is not verbatim from P9 §89.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: AwareDatetime
    source_freshness: SourceFreshness
    quality_status: QualityStatus
    metric_version: str = Field(min_length=1)
    currency: CurrencyCode | None
    observation_count: int | None = Field(default=None, ge=1)
    data: T


class Page[T](BaseModel):
    """Cursor pagination (P1 §25 / P9 §141): ``items`` plus an explicit ``next_cursor``.

    ``next_cursor`` is required-but-nullable: null means "no further pages", and a missing
    field is not the same statement. ``limit`` is the request's page size when echoed;
    ``total`` is optional because counting a full collection can be expensive.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[T]
    next_cursor: str | None
    limit: int | None = Field(default=None, ge=1)
    total: int | None = Field(default=None, ge=0)
