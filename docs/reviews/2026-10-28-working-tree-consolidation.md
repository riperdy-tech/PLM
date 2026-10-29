# A02 — Existing worktree intake and consolidation plan (2026-10-28)

**Workspace:** `C:\Users\riper\Downloads\PLM`. Checked via connected PLM workspace, not an inferred local path. Working branch `review/r6-live-adapters`, observed HEAD `4a35ce2` (2026-10-19). The local database has separately reached Alembic 0039; the Git commit does **not** encompass all subsequently edited migration files.

## Intake (read-only inventory)

A fresh `git status --porcelain=v1 --untracked-files=normal` reported:
- **62 tracked modified paths** and **134 untracked paths** before applying this A/B hygiene pass (counts include local project-wide changes inherited from earlier sessions, not 196 independent audit fixes).
- Work spans P1/P3/P4/P6/P7/P9, platform, tests, migrations, local scripts and review documentation. It contains numerous previously tested features and evidence that **must not be reset, dropped or overwritten** for convenience.
- Connector scratch `.bridge/` appeared untracked. This A/B pass added `.bridge/` to `.gitignore` (local runtime `data/`, including migration backups, was already ignored). Independent `git check-ignore` returned ignored for both bridge scratch and local backup path.
- The first `git diff --check` found **two pre-existing trailing-empty-line issues** in `services/youtube_publishing/client/__init__.py` and `tests/unit/music/test_provider_adapters.py`. The extraneous final blank lines were removed without changing program logic. The **whole-tree follow-up `git diff --check` passed** (exit code 0, 2026-10-28).

## Read-only refresh after B01–B03 and A03 edits

A new `git status --porcelain=v1 --untracked-files=normal` inventory on
branch `review/r6-live-adapters` at the **same HEAD `4a35ce2`** found
**96 tracked modified/staged paths**, **156 untracked paths**, **252 total**
(job `2efbea2561e0310b98d7b2eef9ced65b`).
This is the current mixed working tree, **not** a count of commits or
independently completed findings; the older 62/134 figures above are
the initial baseline. `git diff --check` passed. A separate read-only
staging query found **0 staged paths** and a clean staged diff-check
(job `df9fd54bb24cc4453bf21948cce548eb`).
No files were staged, reset, cleaned, deleted, committed or pushed.

The refreshed count adds A/B remediation code and tests atop inherited
uncommitted work; individual ownership and provenance remain unreviewed.
Any future coherent commit requires its own exact staging review and
same-source checks, not a blanket stage of all 252 paths.

## Safety decisions

1. **Never run `git reset --hard`, `git clean`, automated whole-tree `git add .`, or force operations** on this inherited mixed worktree. They could destroy uncommitted implementation, original audit evidence, local artifacts and user work.
2. Split review by coherent existing history: R6 provider integrations, post-audit P5/P6 QA and audio diagnostics, migration 0038/0039, market/analytics measurement, dashboard/operator work, and the new A/B remediation edits. Compare against HEAD and previously saved evidence.
3. For each group, verify no keys/tokens/media/dumps are staged, retain `AGENTS.md`'s fail-closed behavior and the canonical `docs/STATUS.md` update, then create reviewable commits **only after** those exact staged contents and their tests are checked. A previous regression run on a changing worktree cannot certify a future commit.
4. Rebuild or revalidate migration history from revision graph, not by blindly committing a database dump. A local 0039 DB is not staging/production readiness.
5. After groups are consolidated, record clean staged diff, branch/commit provenance, CI outcome and integration gate output for the same code revision. This is mandatory before marking A02/A03 complete.

## A/B additions begun

- `docs/reviews/2026-10-28-audit-remediation-register.md`: historical RC1–RC6, original findings and later R5/content-quality risks, all without speculative closure.
- `docs/adr/0037-p1-observation-evidence-lookup.md`: narrow C1 raw/normalized lookup compatibility; no changing stored arm identities.
- `services/market_intelligence/api/routes.py` plus `tests/unit/market/test_observation_raw_evidence_lookup.py`: preserve C1 wire, resolve **unique** raw observation reference, explicitly refuse ambiguous raw IDs.
- `packages/plm_platform/pipeline/types.py`, `pipeline/http.py` and `tests/unit/test_pipeline_http_assemble.py`: extend baseline activity/P6 HTTP timeouts, maintaining bounded limits. This is **not** yet a durable P6 render job.

## Current A02 gate

**NOT COMPLETE.** Mixed inherited changes are still uncommitted. No bulk commit/push was attempted; no source work or evidence was deleted. Full Compose integration and back-to-back production-scale R5 runs have not been repeated for these changes. Continue source-group ownership and staging review before considering A02 closed.
