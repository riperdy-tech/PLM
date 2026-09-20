# ADR-0022: P1's `market` tables and the C1 envelope is the registry's flat object

- Status: `ratified` — 2026-09-20
- Ruling: `R2`, `R15` (`C1`)
- Seam: `C1`
- Supersedes / superseded by: `—`

## Context
Two P1.10/P1.11 reconciliations needed a durable record before the C1 seam froze:

1. **Envelope.** Registry C1 fixes the list response as the flat object
   `{items, next_cursor, as_of, score_version}` (P1 §25). `plm_contracts` also ships
   `Page[T]`/`Envelope[T]`, whose shapes differ (pagination and provenance wrappers).
   Which one the wire carries is not a style question — P2's adapter reads the flat
   fields, and a later "helpful" refactor into `Page` would rename them.
2. **Tables and `brand_id`.** P1.2's single migration owns the `market` schema tables;
   R19 requires `brand_id` on `market_arms`, while the frozen C1 payload carries no
   brand (the registry's field list cannot grow a field because P1's schema did).

## Decision
- The C1 list response is the **registry's flat object**, never `plm_contracts.Page`
  or `Envelope`. `as_of` is an aware ISO-8601 instant; `freshness` does not exist on
  the wire (P2 derives it, §46).
- P1.2's tables, as implemented in
  `migrations/versions/market/market_0002_initial_tables.py` and declared in
  `services/market_intelligence/storage/models.py::MARKET_TABLES`:
  `market_sources`, `market_queries`, `market_collection_runs`,
  `market_raw_observations`, `market_seed_evidence`, `market_entities`,
  `market_entity_identities`, `market_observations`,
  `market_taxonomy_classifications`, `market_arms`, `market_opportunity_scores`,
  `market_change_events`, `market_quarantined_records`, `market_snapshots`.
- `brand_id` stays required on `market_arms` and absent from the C1 payload.

## Evidence
- Registry C1: "Response envelope: `items`, `next_cursor`, `as_of`, `score_version`
  (P1 §25)"; "there is **no `freshness` field on the P1 wire**".
- `docs/plans/2026-09-20-phase-P1-execution-plan.md` P1.10 ruling 3 (reconciliation
  is recorded here so nobody "fixes" the payload back to an envelope) and open
  item 18 (`brand_id` vs the frozen wire).
- `tests/contract/test_c1_market_to_ar.py` (producer payload → consumer schema; the
  aware-`as_of` and no-`freshness` assertions).

## Consequences
P2's adapter reads the flat fields verbatim; platform `Page`/`Envelope` remain the
shapes for other seams that need them. A future envelope change is a registry
amendment first (R2), then both sides in one change.

## Alternatives considered
- `Page[OpportunityOut]` — rejected: different field names (`total`, `offset`), and
  the registry is the first authority (R2).
- Wrapping in `Envelope[T]` with the frozen fields inside — rejected: adds a nesting
  level P1 §25 does not show and buys nothing P2 asked for.
- Adding `brand_id` to the payload — rejected: the registry's field list is frozen;
  P2 gets identity from its own state model (R15).

## Cost if wrong
Contained to `api/schemas.py` plus one registry amendment; the seam test fails loudly
with the rename diagnosis if either side drifts, so the cost is a deliberate change,
never a silent one.
