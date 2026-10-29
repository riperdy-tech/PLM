# ADR-0037 — Resolve P1 C1 evidence identifiers without rewriting arm lineage

- Status: Accepted for A–B **local code development** (2026-10-28); external deployments and historical rewrites are not authorized
- Owners: P1 API and cross-phase C1 consumers
- Supersedes: no historical revision or contract field
- Related: ADR-0025, `docs/contracts/CONTRACT_REGISTRY.md`, R5-OBS in `docs/STATUS.md`

## Context

The frozen C1 `EvidenceSummary.top_observation_ids` and persistent `observation_ids` carry **raw observation IDs** from the scored market inputs. The P1 read endpoint
`GET /market/v1/observations/{observation_id}` originally looks up only
`market_observations.id`, the **normalized** observation ID. R5 full-chain
lineage joins correctly through `market_raw_observations`, but its published
evidence pointers cannot be resolved using the documented read API.

Replacing the wire's IDs with normalized IDs would rewrite identity meaning
for existing decisions and frozen C1 consumers and require a versioned
backfill/contract transition. Silently choosing one normalized record for a
raw observation linked to many normalized records would be an ambiguous,
unverifiable answer.

## Decision

Preserve C1's existing payload, score lineage, stored IDs and historical rows
without migration. Expand the **read-only P1 observation lookup** as follows:

1. Resolve a valid ID by `market_observations.id` first, retaining existing
   normalized-ID behavior.
2. If no normalized ID matches, resolve by `market_observations.raw_observation_id`
   **only when exactly one normalized observation exists**.
3. If two or more normalized observations refer to the same raw ID, return
   a typed HTTP 409 `observation_id_ambiguous`. Do **not** pick the first row,
   return a fabricated normalization, or leak the underlying raw payload.
4. Unknown or malformed IDs are 404 `not_found`; the same service-token
   guard runs first. All queries are read-only and bounded (the ambiguity
   probe reads at most two records).
5. The returned `ObservationOut.id` is the actual **normalized observation
   ID**, and `provenance.raw_observation_id` is the original raw ID. Do not
   pretend they are the same entity or invent source metadata.

This expands lookup compatibility without changing or versioning the frozen
C1 `EvidenceOut` object or any persisted score decision. If future C1
consumers require a one-to-many raw/normalized resource, design a separate,
versioned list endpoint rather than silently overloading this scalar route.

## Acceptance and risks

Tests must exercise existing normalized IDs, unique raw aliases, multiple
normalized rows sharing one raw ID (409), malformed/unknown IDs (404) and
service-token authorization. A real local/Compose read-only probe should
verify historical C1 evidence resolvability, and unresolved/ambiguous cases
must be counted instead of treated as success.

No rights, provider authorization, P1 arm identity, duration bucket, score
weights, release status or P2 allocation semantics are changed by this ADR.
Changing duration-based arm identity requires its own separately reviewed
decision and migration compatibility plan.
