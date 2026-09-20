# ADR-0020: One plm CLI, one group per phase

- Status: `ratified` — 2026-09-20
- Ruling: `R20`
- Seam: `all` (operator surface)
- Supersedes / superseded by: `—`

## Context

P5 §202 defines an operational command set (`p5 evaluate <candidate_id>`,
`p5 review queue`, `p5 policy validate <version>`, …). The other phases define
no commands at all — but every phase has operator actions (evaluate a candidate,
run a cycle, recheck a manifest) that would otherwise become ad-hoc scripts
bypassing the database. Nine binaries with nine flag conventions is the failure
mode.

## Decision

One `plm` CLI with a subcommand group per phase — for the phases that define no
CLI (P1, P2, P3, P6, P9) as well as P5's. P5's command set is exposed through its
phase group; commands are wrappers around APIs/workflows (P5 §202's own rule),
never scripts that bypass the database.

## Evidence

- `PHASE-P5` §202 (the command list and the "wrappers, not ad-hoc scripts" rule).
- W0.12 implementation: `packages/plm_platform/cli.py` (Typer app, `plm doctor`,
  `plm migrate`, one stub group per phase reporting "not implemented until
  PHASE-Pn", exit code 2).

## Consequences

One entry point, one help system, one exit-code convention (0 ok / 1 check
failed / 2 not implemented). `plm doctor` is the W0 gate's health probe. A phase
adds commands by filling its own group; cross-phase commands (doctor, migrate)
live at the top level.

## Alternatives considered

- *A binary per phase* — rejected: nine entry points and inconsistent UX for
  operators; also every phase would reinvent flag parsing.
- *No CLI (API-only)* — rejected: P5 §202 already specifies commands, and
  operators need reproducible one-liners for incident work; the CLI existed
  before any phase needed it (W0.12).

## Cost if wrong

If a phase later needs a fundamentally different interface (a TUI for the review
queue, say), it extends or wraps its group rather than replacing the CLI —
additive. Renaming the top-level binary later is a user-facing change, so it is
deliberately chosen now while nothing depends on it.
