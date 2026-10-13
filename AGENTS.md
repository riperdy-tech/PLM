# PLM — instructions for agents working in this repository

This file applies to PLM. The `AGENTS.md` in the parent `Downloads` folder describes a
different project (equity screening) and does not apply here, except for the principle
restated in section 2.

## 1. Mission

PLM is an autonomous music discovery and publishing engine (`docs/architecture.md`). The
program is done when this sentence (P0 §1) is true of the **running** system, with no
human step in the middle:

> A market observation collected by P1 becomes a scored opportunity, then a production
> directive, then an experiment and content package, then generated music, then a gate
> decision, then a playlist-level media package, then a published YouTube video and a
> distributed release, then measured revenue and cost attributed back to the originating
> arm — and that outcome updates the allocation policy that chose the arm.

What is true today is recorded in `docs/STATUS.md`. The staged plan for closing the gap
is `docs/reviews/2026-10-13-system-review-and-remediation-plan.md`. Each stage, R0
through R6, is approved separately against its own evidence gate. Do not start a stage
that has not been approved.

## 2. Fix the cause, not the example

When a bug or critique arrives as one example, such as one field, one candidate or
one test, find the systemic cause and fix it at the engine level. Never add a narrow rule
that only makes the example pass. If the root cause is out of scope for the current
stage, record it in `docs/STATUS.md` with the stage that owns it.

## 3. Rules that are never traded away

- **A missing required fact can never become a passing decision.** Unknown, zero and
  not-ready are three different states. A missing gate disposition is not `PASSED`. A
  missing rights field is not permission. A missing ISRC, artifact or identifier is
  never invented. Block the handoff with a reason code instead.
- **A mock pass is not evidence of production behaviour.** Name what a test proves. An
  in-process test with hand-built hand-offs proves the parts, not the pipeline.
- **External side effects go through the guard.** Every upload, submission, takedown or
  other external write calls
  `plm_platform.execution.ExternalWriteGuard.authorize_external_write`. Never infer
  permission from `ProviderMode.REAL` or from configuration alone.
- **Human hard-stops are not overridable by a generic approval.** This covers copyright
  and platform-policy disputes, KYC, account security, payment changes, and the
  architecture P29 list. They resolve only through their required external process.
- **Determinism.** No wall-clock reads or process-randomised hashing (`hash()`) in
  anything that must replay. Inject the clock and derive seeds with `hashlib`.
- **Money and measurement.** Never sum overlapping window snapshots. Every amount has an
  explicit currency. A proxy reward is not expected economic value; do not label it as
  such.
- **Runtime code never imports from `tests/`.** `tests/unit/test_runtime_imports.py`
  enforces this.
- **Secrets** live only in the environment or `.env`, never in code, fixtures or logs.

## 4. Keeping status honest

- When a change alters what a capability actually does, update `docs/STATUS.md` in the
  same commit.
- Plan checkboxes record that code was committed, not that a gate holds. Never tick a
  gate without its evidence.
- Report test results as they are: counts, failures and what was not run.

## 5. Working conventions

- Gates for every change are `ruff check .`, `mypy packages services`, and
  `pytest -m "not integration"`. Integration tests need `docker compose up -d --wait`.
- Phases talk over HTTP contracts only. Shared vocabulary lives in `plm_contracts`, and
  shared runtime in `plm_platform`. A service never imports another service, except in
  `tests/contract`.
- Cross-phase contracts are frozen in `docs/contracts/CONTRACT_REGISTRY.md`. Change them
  only through an ADR in `docs/adr/`.
