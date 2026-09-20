# services/market_intelligence/collection/seed_expansion.py
"""§8 Stage B: autonomous seed expansion from observed market language.

The proposer (LLM or rule-based) only *proposes*; `validate_candidate_seed` decides;
lineage (`parent_seed`, source observations, model identity, confidence) is stored on
the accepted query rows and in `market_seed_evidence`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..domain.enums import QueryType
from ..normalization.canonical import text_fingerprint
from ..storage.repositories import insert_seed_evidence, load_queries, upsert_query
from .seeds import SeedDecision, validate_candidate_seed

__all__ = ["SeedProposal", "SeedProposer", "propose_from_observations"]


@dataclass(frozen=True, slots=True)
class SeedProposal:
    """One proposed research term with the lineage §8 requires."""

    term: str
    query_type: QueryType
    source_observation_ids: tuple[str, ...] = ()
    proposed_by_model: str = "unspecified"
    model_name: str = "unspecified"
    model_version: str = "0"
    confidence: float = 0.0


SeedProposer = Callable[[Sequence[str]], Sequence[SeedProposal]]


def propose_from_observations(
    session: Session,
    *,
    source_id: str,
    proposer: SeedProposer,
    titles: Sequence[str],
    descriptions: Sequence[str],
    parent_query_id: str | None,
    settings: MarketSettings | None = None,
    min_confidence: float = 0.5,
    max_new_seeds: int = 25,
) -> list[SeedDecision]:
    """Propose, validate, persist — in that order; every outcome is returned.

    `max_new_seeds` bounds one expansion so a talkative proposer cannot explode the
    query pool (§43's "massive query-result explosion").
    """
    texts = (*titles, *descriptions)
    existing = frozenset(
        row["query_fingerprint"]
        for row in load_queries(session, source_id=source_id, active_only=False)
    )
    decisions: list[SeedDecision] = []
    created = 0
    for proposal in proposer(texts):
        decision = validate_candidate_seed(
            proposal, existing_fingerprints=existing, min_confidence=min_confidence
        )
        if decision.accepted and created >= max_new_seeds:
            decision = SeedDecision(False, f"expansion cap of {max_new_seeds} reached")
        if decision.accepted:
            query_id, was_created = upsert_query(
                session, source_id=source_id, query_text=proposal.term,
                query_type=proposal.query_type, locale="en", country_code="US",
                language="english", seed_group="expanded", priority=40,
                query_version="p1-queries-v1", parent_query_id=parent_query_id,
                proposal=proposal,
            )
            if was_created:
                created += 1
                existing = existing | {text_fingerprint(proposal.term)}
                insert_seed_evidence(
                    session, query_id=query_id,
                    raw_observation_ids=list(proposal.source_observation_ids),
                )
            else:
                decision = SeedDecision(False, "duplicate of an existing seed")
        decisions.append(decision)
    return decisions
