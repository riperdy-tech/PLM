# Owner decisions D1–D4 (2026-10-29)

**Source:** the project owner, in the Claude Code takeover session on 2026-10-29,
answering the four decisions raised in
[`2026-10-29-unified-remaining-work-roadmap.md`](2026-10-29-unified-remaining-work-roadmap.md) §3.
Recorded by that session. These decisions change governance records only; none
closes an A/B finding or an R-stage gate.

| ID | Question | Decision |
| --- | --- | --- |
| D1 | Authorize, retroactively record or quarantine the existing R6 live work and the two public uploads | **Retroactively recorded** |
| D2 | Reset or annotate the dev P2 posterior | **Annotate only** — no database write |
| D3 | Authorize provenance-grouped commits of the inherited uncommitted tree (A02) | **Authorized** |
| D4 | Confirm R1–R5 approvals for the record | **Confirmed** |

## D1 — R6 live work and uploads: retroactive record

The owner retroactively authorizes, for the record, the R6-scope live work done
between 2026-10-19 and 2026-10-29 without an R6 approval:

- the real YouTube Data/Analytics/Reporting clients and their read-only live
  calls against channel *Rhodes & Rain Records* (`UC3Ps0F0z8Mw9ftakIcZ1cWA`);
- the P9 closed-loop run that posted a reward for video `lPYmaPHPQr4`;
- the two public uploads `lPYmaPHPQr4` (via `scripts/produce_full_playlist.py`)
  and `aqTf6gzjsNI` (via a Control Deck path), both made directly through
  `RealYouTubeClient`, outside `ExternalWriteGuard`.

Limits of this decision:

- It is a record of what already happened, **not an R6 approval**. R6 remains
  unapproved; no new live provider call, upload or publish is authorized by it.
- It does not make those runs evidence. They bypassed the guard, so they carry
  no budget reservation, idempotency record or guard audit row; AGENTS.md §3 was
  violated at the time and this record does not change that fact.
- The uploads stay public. Nothing was changed on YouTube.
- The direct-write paths remain disabled in code (STATUS P7 "Legacy album
  producer direct writes: stub").

## D2 — dev P2 posterior: annotation

The shared development P2 posterior for arm `lofi_study_focus` moved from mean
0.40825 / variance 0.10649 to 0.45798 / 0.04878 when the P9 closed-loop run
applied a reward for `lPYmaPHPQr4`. That reward is **not auditable**: the video
was uploaded outside the guard, and the producing path (see the
`scripts/autonomous_loop_worker.py` STATUS row) used a fixed experiment identity
and could post a seven-day Analytics query as a D28 `MATURE` reward.

Decision: annotate, do not reset. No database was read or written for this.
Consequences:

- Any P2 allocation, Thompson draw or report computed from the shared dev DB
  after that update carries this reward. Treat `lofi_study_focus` posterior
  values in that DB as contaminated for evaluation purposes.
- The shared dev DB is not production evidence in any case. A future reset or
  correction is a separate database write and needs its own approval.

## D3 — provenance-grouped commits

The inherited uncommitted tree (296 paths: 100 modified, 196 untracked, 0
deleted; 267 `git status` entries before the takeover patch) and the takeover
session's changes were committed on local branch `review/r6-live-adapters` on
top of `4a35ce2`. Nothing was pushed. Groups were reconstructed from file
timestamps, topics and the docs that describe each piece of work, following the
split in [`2026-10-28-working-tree-consolidation.md`](2026-10-28-working-tree-consolidation.md);
hunk-level authorship inside shared files (`docs/STATUS.md`,
`packages/plm_platform/cli.py`, `pyproject.toml` …) was not reconstructed.

| # | Commit | Group | Paths | Full non-integration suite on this tree |
| --- | --- | --- | --- | --- |
| 1 | `ebf5668` | P7 candidate selection, preflight gates, `youtube_0038` | 14 | 1,967 passed, 10 failed, 18 errors |
| 2 | `0727d45` | R6 live YouTube/Analytics adapters and operator scripts (D1) | 46 | 2,194 passed, 12 failed, 18 errors |
| 3 | `5929e83` | P6 content-quality follow-up, texture and timing evidence | 85 | 2,559 passed, 6 failed |
| 4 | `b5d6a12` | Alembic 0038 reconciliation and merge 0039 | 47 | 2,934 passed, 6 failed |
| 5 | `dd221a8` | P1 measured-duration arm identity (B01) | 19 | 2,964 passed, 6 failed |
| 6 | `6f364f9` | P4/C3 cost and credit provenance, budget boundaries (B03) | 35 | 2,977 passed, 24 failed, 42 errors |
| 7 | `c684efb` | P6 durable render claims (B02, pre-ADR-0043) | 23 | 3,083 passed, 25 failed, 42 errors |
| 8 | `3d89621` | P4 paid-replay refusal and provider provenance (B03/B04) | 13 | 3,211 passed, 5 failed |
| 9 | `dde41ef` | R5 isolation, shared CLI/config, status documents | 14 | 3,246 passed, 1 skipped, 0 failed |
| 10 | `d6fd506` | Takeover session: ADR-0043/0042, paid-attempt checkpoints | 32 | 3,282 passed, 1 skipped, 0 failed |
| 11 | this commit | D1–D4 record | 4 | docs only |

Commit 9's tree equals the takeover baseline (`cd56903`), and commit 10's equals
the final tested tree (`9a79d70`). Secret scan before committing: no real
credential from the main checkout's `.env` appears in any committed file (the
only matches are the documented `.env.example` placeholder service token); no
binary or media file is included.

Verification per commit (each tree extracted with `git archive` outside any
checkout, no `.env`, main-checkout venv):

- Every commit: Ruff clean; `pytest -m "not integration" --collect-only` with
  0 collection errors.
- Full suite per commit: the table above. Commits 1–8 are **not green**. The
  earlier sessions' edits interleave across these topics: P7's timing gate
  (commit 1) needs P6 fixtures that land in commit 3; commit 6 makes P4
  `money_basis` NOT NULL while the engine that fills it lands in commit 8; and
  CLI tests need the shared `plm_platform/cli.py` (commit 9). A control run of commit 9 in the same extracted setup passed
  (3,246), so the failures are code dependencies, not the environment.
  Keeping the provenance split was preferred over merging groups until each
  is green; **do not bisect inside 1–8 by test result.** Each commit message
  states its own result.
- Commit 10 (= final): also Ruff and mypy (465 files) clean in the main
  checkout; disposable PostgreSQL 16 checks as recorded in STATUS.

A02 is **not closed** by this: the register requires CI on a pushed revision
and the same-SHA integration gates (U1, U2).

## D4 — R1–R5 approvals

The owner confirms that stages R1–R5 of
[`2026-10-13-system-review-and-remediation-plan.md`](2026-10-13-system-review-and-remediation-plan.md)
were approved when they were executed (the approvals were given in conversation
and not written down). This confirms **authorization only**. It does not
re-verify any gate: R5 has not been re-run since the A/B changes (roadmap U2),
and R0's owner review of STATUS/P0 is still unrecorded. R6 and later stages
remain unapproved.
