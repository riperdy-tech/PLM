# ADR-0040 — C3 budget limit must be a finite amount

- Status: Accepted for A–B code remediation, 2026-10-28; deployment and external writes remain separately gated.
- Scope: C3 P3→P4 track-spec validation and P4's internal projection.
- Owner: B03 budget/guard correctness; RC1 and RC5.

## Context

The C3 `budget_limit` was nonnegative but allowed positive infinity. An unbounded
amount can pass P3→P4 validation and then be rejected by P4's stricter
`GenerationPlan` constructor, potentially turning a caller-input error into
an unhandled server failure. NaN is also not a legitimate budget amount.
Unknown spend must not be represented as an infinite allowance.

## Decision

Require `budget_limit` to be nonnegative **and finite** in the shared C3
`TrackSpecPayload` and the corresponding P4 `AdaptedTrackSpec` projection.
Reject NaN, +infinity and -infinity at the shared seam with a typed validation
refusal before any plan persistence, budget reservation or provider call.
An explicit zero remains distinguishable from a missing budget, and retains
the established live-provider hard stop.

This narrows validation of an already-required field, without changing its
name, type, presence, meaning or wire format; C3 schema version stays at 2.0.
The rejection applies to nonstandard Python/JSON numeric payloads too.

## Verification and limits

The C3 producer→consumer contract test now includes all three nonfinite
numbers alongside the existing negative-budget case. Record focused,
lint/type and post-change full regression outcomes in `docs/STATUS.md`.
This does not create a positive per-plan budget/credit reservation system,
currency pricing or independent provider-usage evidence. B03 remains partial.
