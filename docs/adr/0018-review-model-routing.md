# ADR-0018: Reviewers always get fresh context; no cheap-tier routing in this harness

- Status: `ratified` — 2026-09-20
- Ruling: `R18`
- Seam: `none` (process ruling)
- Supersedes / superseded by: `—`

## Context

The executing-plans/subagent-driven skills recommend routing some reviews to
cheaper models when the harness supports it. This harness exposes no reliable
model parameter for subagents: dispatches inherit the session model. The
temptation is to silently treat reviews as "light" work — the failure mode the
skills warn about, because the review is the fresh context, not the token
budget.

## Decision

Reviewers always get fresh context. No cheap-tier model routing is available in
this harness — recorded as a ruling, not fixed. Revisit if the harness changes.

## Evidence

- P0 §7 ("Model selection ruling (R18): this harness exposes no reliable model
  parameter for subagents; dispatches inherit the session model").
- Wave 0 dispatches: reviewer subagents ran with fresh contexts (D1 report in
  `.superpowers/sdd/progress.md`).

## Consequences

Per-task review cost is uniform; the schedule cannot optimize it. The value the
skills buy — a second pair of eyes that did not write the code — is preserved,
because freshness is unaffected by the missing parameter.

## Alternatives considered

- *Fake routing (dispatch "reviewer" prompts but reuse session context)* —
  rejected: performs the ritual without the second pair of eyes, which is worse
  than no review because it produces a false signal.
- *Skip per-task reviews* — rejected: fresh context is the point; Wave 0's D1/D2
  reviews found real issues (temporal healthcheck, worker healthcheck).

## Cost if wrong

Process cost: every review runs at session-model cost. If the harness later
exposes model selection, this ADR is superseded by a new one and cheap-tier
routing can be adopted where it is genuinely safe.
