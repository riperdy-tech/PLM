# ADR-0016: Wave 0 is the engineering half of P0; accounts are a human list

- Status: `ratified` — 2026-09-20
- Ruling: `R16` (`C10`)
- Seam: `C10` — missing engineering prelude
- Supersedes / superseded by: `—`

## Context

There is no PHASE-P0 plan file, yet seven phase briefs assume infrastructure
specified nowhere: OAuth applications, a secret store, a durable workflow
engine, FFmpeg, audit states. The account/OAuth/audit half cannot be built by an
agent at all — it requires human identity, KYC and platform reviews — so a
"PHASE-P0" that bundles both would block the mock-first build behind work no
agent can finish.

## Decision

Wave 0 (W0.1–W0.12) *is* the engineering half of P0. The account/OAuth/audit
half is the human task list in P0 §10, and it blocks only live-mode enablement —
never the mock-first build.

## Evidence

- P0 §5 C10 (the missing prelude; the seven briefs' assumptions).
- P0 §6 Wave 0 (12 tasks: scaffold, compose, contracts, config, db, storage,
  temporal, observability, harness, docs, CI, CLI) and the W0 gate.
- P0 §10 (human task list: YouTube API project/OAuth/audit, music provider
  contract, distributor KYC, Spotify app registration, budget ceilings, operator
  identities).

## Consequences

Every phase can be built and tested mock-first without a single credential;
live-mode enablement is a later, explicit switch (P5 §155–§157's shadow/capped/
autonomous modes, P8 §(staged rollout)). "Blocked pending credentials" is a
recorded state (P0 §6 Wave 1 note), never a silent skip.

## Alternatives considered

- *Write a PHASE-P0 plan file covering everything* — rejected: it would restate
  Wave 0 and add a human checklist an agent cannot execute; the master plan
  already carries both halves.
- *Gate the whole build on accounts existing* — rejected: it destroys the
  mock-first property the program is designed around.

## Cost if wrong

If a phase needs a specific piece of infrastructure Wave 0 did not build, it
surfaces at that phase's gate — mid-wave detection, and the fix is a Wave 0
addendum. If the human list is incomplete, live-mode enablement stalls visibly
before any public action, which is where a stall is cheap.
