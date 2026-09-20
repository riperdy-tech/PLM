# ADR-0006: P4 adds four read endpoints and extends rights_snapshots

- Status: `ratified` — 2026-09-20
- Ruling: `R6` (`C4`)
- Seam: `C4` — PHASE-P4 → PHASE-P5 (candidate ready)
- Supersedes / superseded by: `—`

## Context

P4's `music_candidate_ready_for_p5` event (§68) is compatible with P5's expected
event shape (§7) — but the event is all P5 gets. P5 §7 requires retrieval of the
full candidate record, artifact manifest, provenance chain, prompt metadata,
generation attempts and license snapshot; P4 §67 exposes none of those. Worse,
P4's `rights_snapshots` (§65) lacks plan/tier, account identity, terms version
and timestamps that P5 §33 requires — without them P5 §117's scenario defaults
every candidate to quarantine, which is a silent quality collapse, not an error.

## Decision

P4 adds four read endpoints — `GET /music/candidates/{id}/attempts`, `/license`,
`/artifacts`, `/provenance` — and extends `rights_snapshots` with `plan_or_tier`,
`account_identity_ref`, `terms_version`, `created_at`, `downloaded_at`.

## Evidence

- `PHASE-P4` §67 (endpoint list; the four are absent), §65 (`rights_snapshots`
  field list), §68 (the event payload).
- `PHASE-P5` §7 (retrieval requirements), §33 (snapshot requirements:
  "plan/tier", "account identity reference", "commercial-use terms version",
  "download timestamp"), §117 (unknown commercial rights → quarantine).

## Consequences

This is a P4.1/P4.2 amendment and must land before P5.3 starts (pre-step W3.0).
The event itself is unchanged and stays v1. P5 gets read-only access to what it
must verify; P4 keeps ownership of the records.

## Alternatives considered

- *Widen the event to carry everything* — rejected: provenance chains and
  attempts are large, slow-changing records; putting them on the event forces
  every consumer to store copies.
- *P5 reads P4's tables directly* — forbidden: cross-phase database reads are
  banned by the repository rules (P0 §4); the seam must be an API.

## Cost if wrong

If the added snapshot fields still under-specify a live provider's terms model,
live-mode rights checks fail at P4.8 — late, but gated and visible. The remedy is
the standing one: new ADR, registry C4 amendment, then P4 and P5 together. The
C4 seam test pins the four endpoint payload shapes as they land.
