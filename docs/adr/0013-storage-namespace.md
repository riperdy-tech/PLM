# ADR-0013: One storage namespace, seven prefixes, content-addressed

- Status: `ratified` — 2026-09-20
- Ruling: `R13` (`C9`)
- Seam: `C9` — cross-phase storage layout
- Supersedes / superseded by: `—`

## Context

Three phases wrote three different layouts for the same MinIO deployment:
P4 §29 `music/{brand_id}/{experiment_id}/…`, P5 §193 `artifacts/candidates/…` +
`qa/evaluations/…`, P6 §42 `media/{brand}/{experiment}/{package_id}/…`. Consumers
read artifacts by URI, so divergent layouts mean no consumer can reason about
location, retention or access patterns — and every phase's migrations would
couple to a different prefix scheme.

## Decision

One namespace with top-level prefixes `market/`, `experiments/`, `music/`, `qa/`,
`media/`, `reports/`, `analytics/`. Producers write immutable content-addressed
artifacts; consumers read only by the URI recorded in a database row; checksums
are mandatory on every artifact and verified on read.

## Evidence

- `PHASE-P4` §29 (storage hierarchy), `PHASE-P5` §193 (artifact storage layout),
  `PHASE-P6` §42 (file naming).
- W0.6 `packages/plm_platform/storage.py` implements the seven prefixes with
  `put_verified`/`get_verified` checksum verification (Wave 0 evidence that the
  rule is buildable).

## Consequences

P4 §29 and P6 §42 become subtrees of the single namespace (`music/…`, `media/…`).
The mapping of P5 §193's `artifacts/candidates/…` subtree onto a prefix is
**recorded in registry C9 as `unspecified - must be resolved at the PHASE-P5
freeze`** — its `qa/` subtree is already conformant. URIs stay opaque text in
databases (ledger ruling L1), so no schema parses paths.

## Alternatives considered

- *Keep the three layouts and document a map* — rejected: every consumer would
  need the map, and retention/lifecycle policy would have to special-case each
  layout.
- *One prefix per phase mirroring the schemas* — rejected: artifacts are already
  phase-namespaced by their content; the seven activity prefixes match how
  operators reason about storage (market data, experiments, music, QA, media,
  reports, analytics).

## Cost if wrong

If the prefix set misgroups something, re-keying happens before any production
artifact exists (the C9 seam test and W0.6's existing implementation bound the
cost). A change is an ADR plus registry amendment, then producer and consumer.
