# ADR-0010: P8 stores raw reports; P9 parses them and owns revenue

- Status: `ratified` — 2026-09-20
- Ruling: `R10` (`C7`)
- Seam: `C7` — PHASE-P8 → PHASE-P9 (report ownership leg)
- Supersedes / superseded by: `—`

## Context

Both phases touch distributor royalty statements: P8 §51 ("Report ingestion") may
ingest raw provider reports and store `raw_report`, `provider_period`,
`received_at`, `report_checksum`; P9 §53 ("Statement ingestion") and §54
(deduplication) parse statements into canonical facts. Two parsers over one
document produce two revenue numbers — the class of defect P0 §1 rates as
severity-equal to a crash. Ownership was never stated.

## Decision

P8 stores and versions raw provider reports and exposes *references* to them
(P8 §72 already lists "provider report references" and "raw report availability").
P9 owns parsing into canonical facts and the immutable archive of what it
ingested (§53/§54). P8 must not compute revenue.

## Evidence

- `PHASE-P8` §51 (ingestion permitted, "must not become the canonical financial
  analytics engine"), §72 (what P8 provides: release/store identity, delivery
  state, timestamps, report references, raw availability — explicitly *not*
  streams/revenue).
- `PHASE-P9` §53 (statement ingestion with `validate_schema()`), §54
  (deduplication), §93/§94 (reconciliation reports).

## Consequences

The C7 seam's first leg is a stable distribution record (C7 entry) plus report
references. P8's delivery pipeline stays provider-shaped; P9's fact pipeline stays
canonical. Reconciliation (P9 §55) compares them deliberately instead of
accidentally.

## Alternatives considered

- *P9 reads provider files directly* — rejected: P9 would need every distributor's
  schema, and P8 is the phase with the provider relationship.
- *P8 parses and hands facts to P9* — rejected: P8 would become a second
  financial engine; both specs explicitly forbid that role for P8.

## Cost if wrong

If P8's stored reports keep something P9 cannot reconstruct (e.g. a provider's
proprietary adjustment lines), P9's reconciliation shows a correction path (P9
§208's corrected-statement fixture covers exactly this). The C7 seam test pins
the reference fields; a change is an ADR plus registry amendment.
