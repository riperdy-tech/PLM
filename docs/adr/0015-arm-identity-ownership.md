# ADR-0015: P1 mints arm_id; P2 owns arm state and posteriors

- Status: `ratified` — 2026-09-20
- Ruling: `R15` (`C1`)
- Seam: `C1` — PHASE-P1 → PHASE-P2 (market opportunity)
- Supersedes / superseded by: `—`

## Context

`arm_id` appears on both sides of seam C1 — P1 mints it in the opportunity
object (§20: `"arm_id": "rnb_late_night_drive_en_vocal_60"`), P2 builds its whole
decision model on it (§6, §7) — but no document said who owns identity, who owns
state, or how the two phases reach each other (no base path or auth was stated
for P1's API).

## Decision

P1 mints and owns `arm_id` (identity, taxonomy, market score). P2 owns arm
*state*, posteriors and exposure. P1's API is served under base path
`/market/v1` with a service token from `plm_platform`.

## Evidence

- `PHASE-P1` §20 (arm_id minted with the opportunity), §25 (API with no base
  path or auth stated — the seam's second gap).
- `PHASE-P2` §7 (arm identity, canonical fingerprint), §45 (`content_arm_state`),
  §46 (`arm_feature_snapshot`), §47 (`arm_posterior`), §54 (exposure tracking) —
  the state side.
- P0 §5 C1: "Freeze as C1 before P2 starts."

## Consequences

Arms are created exactly once, by P1, from market vocabulary; P2 never invents
arms and never rewrites taxonomy. P2 persists its own view (state, priors,
exposure, snapshots) keyed by P1's id. The `/market/v1` prefix and token auth
become part of C1's frozen entry, so P2's adapter (P2.2) has a concrete base URL
and credential source.

## Alternatives considered

- *P2 mints arms* — rejected: an arm is a market-vocabulary object; P1
  classifies the market into arms, P2 allocates among them.
- *Both phases mint, reconciled later* — rejected: duplicate arms double-count
  evidence and split posteriors — a silent learning corruption.
- *No auth (internal network trust)* — rejected: `plm_platform` already owns
  secrets and service tokens (W0.4); using them costs nothing.

## Cost if wrong

If arm identity needs richer structure (e.g. versioned taxonomy migrations —
P1 §29 already anticipates taxonomy versions), the change rides the taxonomy
version, not the id format; C1's seam test pins the payload either way. Remedy
is an ADR plus registry C1 amendment.
