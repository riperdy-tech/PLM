# services/market_intelligence/collection/seeds.py
"""§8 Stage A: a curated bootstrap seed pack — discovery seeds, not assumptions.

Stage B (autonomous expansion) lives in `seed_expansion.py`; the LLM may propose,
deterministic validation decides whether a term enters the active pool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..domain.enums import QueryType
from ..normalization.canonical import text_fingerprint
from ..storage.repositories import sync_seed_group_active, upsert_query

__all__ = [
    "SeedCandidate", "SeedDecision", "SeedSpec", "install_bootstrap_seeds",
    "load_bootstrap_seeds", "validate_candidate_seed",
]

_SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "bootstrap_seeds.json"
DEFAULT_LOCALE = "en"
DEFAULT_LANGUAGE = "english"
DEFAULT_COUNTRY = "US"


@dataclass(frozen=True, slots=True)
class SeedSpec:
    term: str
    query_type: QueryType
    seed_group: str = "bootstrap"
    locale: str = DEFAULT_LOCALE
    language: str = DEFAULT_LANGUAGE
    country_code: str = DEFAULT_COUNTRY
    priority: int = 50


@dataclass(frozen=True, slots=True)
class SeedDecision:
    accepted: bool
    reason: str | None = None


@cache
def load_bootstrap_seeds() -> tuple[SeedSpec, ...]:
    """Read `data/bootstrap_seeds.json` once per process; malformed data fails loudly."""
    raw = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    seeds = tuple(
        SeedSpec(
            term=entry["term"],
            query_type=QueryType(entry["query_type"]),
            seed_group=entry.get("seed_group", "bootstrap"),
            locale=entry.get("locale", DEFAULT_LOCALE),
            language=entry.get("language", DEFAULT_LANGUAGE),
            country_code=entry.get("country_code", DEFAULT_COUNTRY),
            priority=int(entry.get("priority", 50)),
        )
        for entry in raw
    )
    if not seeds:
        raise ValueError("the bootstrap seed pack is empty — §8 requires Stage A seeds")
    return seeds


def install_bootstrap_seeds(
    session: Session,
    *,
    source_id: str,
    query_version: str,
    settings: MarketSettings | None = None,
) -> int:
    """Insert every seed that is not already present; returns the number of new rows.

    Idempotent: rerunning installs nothing and reports 0, so a scheduler retry cannot
    duplicate the seed pool. The pack is the truth for its seed groups: a stored seed
    a newer pack dropped (ADR-0044 retired the lofi/R&B pack) is deactivated, so it
    stops being collected while its evidence stays.
    """
    created = 0
    kept: dict[str, set[str]] = {}
    for spec in load_bootstrap_seeds():
        _, was_created = upsert_query(
            session, source_id=source_id, query_text=spec.term, query_type=spec.query_type,
            locale=spec.locale, country_code=spec.country_code, language=spec.language,
            seed_group=spec.seed_group, priority=spec.priority, query_version=query_version,
        )
        created += 1 if was_created else 0
        kept.setdefault(spec.seed_group, set()).add(text_fingerprint(spec.term))
    for seed_group, fingerprints in kept.items():
        sync_seed_group_active(session, source_id=source_id, seed_group=seed_group,
                               keep_fingerprints=frozenset(fingerprints))
    return created


class SeedCandidate(Protocol):
    """What validation needs from a proposal; `SeedProposal` satisfies it structurally.

    Read-only on purpose: a frozen dataclass exposes its fields as get-only attributes,
    which a protocol member declared writable refuses (mypy: "expected settable
    variable, got read-only attribute").
    """

    @property
    def term(self) -> str: ...

    @property
    def confidence(self) -> float: ...


def validate_candidate_seed(
    candidate: SeedCandidate,
    *,
    existing_fingerprints: frozenset[str],
    min_confidence: float,
    max_terms: int = 4,
) -> SeedDecision:
    """§8: deterministic validation decides; the LLM only proposes.

    Rejections carry a reason — a proposal is never silently discarded.
    """
    term = candidate.term.strip()
    confidence = float(candidate.confidence)
    if not term:
        return SeedDecision(False, "empty term")
    if len(term.split()) > max_terms:
        return SeedDecision(False, f"term longer than {max_terms} words")
    if confidence < min_confidence:
        return SeedDecision(False, f"confidence {confidence:.2f} below {min_confidence:.2f}")
    if text_fingerprint(term) in existing_fingerprints:
        return SeedDecision(False, "duplicate of an existing seed")
    if not any(ch.isalpha() for ch in term):
        return SeedDecision(False, "term carries no letters")
    return SeedDecision(True)
