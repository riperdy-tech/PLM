# PLM unified remaining-work roadmap — 2026-10-29

**Author:** Claude Code session `plm-handover-audit-277422` (takeover from the ChatGPT/Nexus A/B session).
**Basis:** files on disk in `C:\Users\riper\Downloads\PLM` (branch `review/r6-live-adapters`, HEAD `4a35ce2`, 267 uncommitted entries, 0 staged), git history of all local/remote branches, and verification runs made in this session (§7). Plans and status entries are treated as statements of intent or prior claims, not as proof.

This is **one** view of what is still required to finish and verify PLM. It maps every open item to its sources in the three planning layers (P0–P9, R0–R6, A/B) and to the discovered side documents. It does not give a completion percentage: the layers measure different things and none has a formal closure record.

---

## 1. Where the real work lives (read first)

| Fact | Evidence |
| --- | --- |
| The main checkout `C:\Users\riper\Downloads\PLM` is on local-only branch `review/r6-live-adapters` at `4a35ce2` (R5 tip + one CLI fix). It has **100 tracked-modified + 196 untracked (non-ignored) paths = 267 status entries, 0 staged, 0 deleted**. | `git status --porcelain`, `git ls-files -m/-o --exclude-standard` (this session) |
| Everything after 2026-10-19 — the A/B program, Alembic 0038/0039/0040/0041 code, P6 texture/QA, P7 real client, P9 Reporting reads, B02 claims — is **uncommitted**. No commit, branch or remote contains it. | same |
| `origin/main` (`abe603b`, 2026-10-13) is far behind: 517 files differ from `4a35ce2`. | `git diff --stat 4a35ce2 abe603b` |
| A second worktree `claude/program-interface-dashboard-9c6dc4` (`4d4e210`) carries a separate dashboard line and `docs/reviews/2026-10-22-system-audit.md`, which does **not** exist in the main checkout. | `git worktree list`, inventory |
| The compose dev stack (15 containers) is running and healthy; its PostgreSQL is the shared dev DB at Alembic `0039`. Code-only migrations 0040/0041 are **not** applied there. | `docker ps`; STATUS B03 entries |
| `.env` in the main checkout holds real YouTube OAuth refresh token and Suno credentials. | key names only were read |

**Handling in this session.** The user's rule was to preserve and not stage/commit inherited work. This session therefore mirrored the exact 267-entry state into its own worktree (`.claude/worktrees/plm-handover-audit-277422`, byte-identical by `cmp`, baseline tree object `cd56903473d2ef89f595348714f789859e8a15df`), did all edits there, and never touched the main checkout's files, index or branch. Diff of this session's own work: `git diff cd56903473d2ef89f595348714f789859e8a15df` inside that worktree. The worktree has no `.env`, so no test there can reach a real provider.

---

## 2. Planning layers and their actual state

### 2.1 Layer 1 — P0–P9 original implementation

| Document | State |
| --- | --- |
| `docs/P0_master_integration_implementation_plan.md` | Status block (lines 13–19, "corrected 2026-10-13") correctly defers to STATUS.md. §6 waves W0–W6: W0.x and W6.3 ticked, W6.1/W6.2 partial. P0 has **no §13**; the R-plan's "P0 §13" means that status block. |
| `docs/P1_…` – `docs/P9_…_implementation_plan.md` | Authoritative phase internals. Their functional scope is implemented in code; production behaviour is tracked per capability in STATUS.md, not here. |
| `docs/plans/2026-09-2x-phase-P*-execution-plan.md`, `2026-09-26-wave-6-loop-closure-plan.md` | Historical execution plans; done. |

P0 §1 success sentence: **true only of the compose stack on mock providers and a mock outcome model** (STATUS "Program status in one paragraph"), and that claim itself awaits re-verification on consolidated code (U2).

### 2.2 Layer 2 — R0–R6 staged remediation

Source: `docs/reviews/2026-10-13-system-review-and-remediation-plan.md`. Line 9: the plan "authorizes **R0 only**. Each later stage is approved separately."

| Stage | Documented approval | Gate evidence recorded | Re-verified on current code? |
| --- | --- | --- | --- |
| R0 truth baseline | **Yes** (plan:9, :109) | CI green 2026-10-14 (STATUS); owner review of STATUS/P0 (gate bullet, plan:136) **not recorded** | Ruff/mypy re-run this session (§7) |
| R1 contracts / fail-closed seams | **No written record found** | commit `bdc808d`, STATUS | No |
| R2 determinism / measurement | **No written record found** | ADR-0031, `test_gate_r2_*` | No |
| R3 runtime | **No written record found** | commit `126064a` ("Gate R3 evidence") | No |
| R4 media / quality | **No written record found** | STATUS "R4: 50 passed" | No |
| R5 full chain | **No written record found** (HANDOVER.md:140 "stop and wait for owner approval before R6") | commit `589decb` 2026-10-19: two consecutive green full-chain runs | **No** — STATUS:53 "No isolated R5 full chain or double-run has passed yet" after A/B changes |
| R6 live adapters | **Not approved** (HANDOVER.md:20, STATUS:1185, A/B handover) | none | n/a — but see §3 |
| Later: economics gate, P10, PARTS X/XII/XIII/XXIII | not approved | none | n/a |

R1–R5 were executed and their gates recorded; the approvals were given in conversation and never written down. **Owner confirmed R1–R5 approvals on 2026-10-29 (D4, [`2026-10-29-owner-decisions-d1-d4.md`](2026-10-29-owner-decisions-d1-d4.md)).** That confirms authorization only; the "Re-verified" column is unchanged.

### 2.3 Layer 3 — A/B original-audit remediation

Source: `docs/reviews/2026-10-28-audit-remediation-register.md` (untracked). Scope authority: register line 109 — "owner-requested stages A–B authorize code, local tests and review"; production DB/media writes, live-account writes and human rights conclusions remain separately gated.

**The 27/57 figure reconciles exactly with the register** (counted row by row this session):

| Register section | implemented—reverify | partial | open | deferred |
| --- | --- | --- | --- | --- |
| RC1–RC6 (6 rows) | 3 (RC1, RC2, RC6) | 3 (RC3, RC4, RC5) | 0 | 0 |
| Part 2 findings X01…PL-05 (40 rows) | 24 | 15 | 0 | 1 (PL-05, excluded from 57) |
| Later R5 / content-quality rows (12) | 0 | 4 (R5-OBS, R5-DUR, R5-TIME, R5-RENDER) | 8 | 0 |
| **Total of 57 counted** | **27** | **22** | **8** | — |

So "27/57" means **27 rows marked "implemented—reverify"**, i.e. a fix is recorded but has not been re-validated on a consolidated revision. **Zero rows are closed.** It is not a completion figure for A/B, and not for PLM.

Work packages A01–A03 and B01–B07 are all `partial` or `triaged`; none meets its own "gate to count finished".

---

## 3. Governance findings that need the owner (not fixable by code alone)

1. **R6-scope live work exists without an R6 approval record.** Uncommitted code and STATUS rows mark the P7 YouTube client, P9 Analytics/Reporting ingestion and "Closed-loop Bayesian learning (P9→P2)" as `real … tested/verified live` against channel *Rhodes & Rain Records* (`UC3Ps0F0z8Mw9ftakIcZ1cWA`). The plan requires R6 approval and staged READ_ONLY → SHADOW → LIVE rollout.
2. **At least one public upload bypassed `ExternalWriteGuard`.** `output_media/full_album/publication_summary.json` records video `lPYmaPHPQr4` (public). The 2026-10-23 content-quality audit states `scripts/produce_full_playlist.py` uploaded directly through `RealYouTubeClient`. The branch-only 2026-10-22 audit records a second public video `aqTf6gzjsNI` published by a Control Deck path that calls the real client directly. Both direct paths are now disabled in the main checkout (STATUS P7 "Legacy album producer direct writes: stub"), but no authorization record exists for the uploads that already happened. This violates AGENTS.md §3 ("External side effects go through the guard").
3. **Possible contamination of the shared dev P2 posterior.** The STATUS P9 row "Closed-loop Bayesian learning — real" records a live reward for `lPYmaPHPQr4` applied to arm `lofi_study_focus` (posterior mean 0.40825 → 0.45798). The STATUS row for `scripts/autonomous_loop_worker.py` (same document) says that path used a hardcoded video ID, a fixed experiment identity and a 7-day query posted as a D28 MATURE reward, and must not be used to train P2. The two rows contradict each other; the dev P2 state may carry an unauditable reward. This session changed the first row's mark from `real` to `partial` with a pointer (STATUS.md) and did **not** touch any database.
4. **Stale or overstated documents:** STATUS.md header "Last verified 2026-10-15, branch review/r5-full-chain"; HANDOVER.md R5 "gate not met" (superseded by `589decb`); SESSION_HANDOFF_2026-10-21.md "fully integrated, closed-loop autonomous engine" (contradicted by STATUS and the 2026-10-23 audit); the A/B handover's `__nexus_history__/…/INDEX.md` (does not exist) and "not copied into the PC repository" (they are on disk).

**Owner decisions (2026-10-29, recorded in [`2026-10-29-owner-decisions-d1-d4.md`](2026-10-29-owner-decisions-d1-d4.md)):** D1 — the live R6 work and both uploads are **retroactively recorded**, not approved as R6, and R6 stays unapproved; D2 — the dev P2 posterior is **annotated, not reset** (no DB write); D3 — provenance-grouped commits **authorized** and made locally on `review/r6-live-adapters` (not pushed); D4 — R1–R5 approvals **confirmed**.

---

## 4. Document and manifest index

Path relative to repo root. T = tracked, T-mod = tracked and modified, U = untracked.

### 4.1 Plans, status and handovers

| Path | Git | Purpose / relation | Class |
| --- | --- | --- | --- |
| `AGENTS.md` | T | Repo rules; R-stage approval rule | Authoritative |
| `README.md` | T-mod | Entry point; links STATUS, plan, register | Authoritative |
| `docs/architecture.md` | T | PARTS I–XXVII master spec | Authoritative (ADR-0002 rank 3) |
| `docs/contracts/CONTRACT_REGISTRY.md` | T | Frozen seams C1–C10 | Authoritative (rank 1) |
| `docs/contracts/DECISION_LOG.md`, `PHASE_MAP.md` | T | Rulings, numbering | Supporting |
| `docs/P0_master_integration_implementation_plan.md` | T | Master integration plan | Authoritative for sequencing |
| `docs/P1_market_intelligence_implementation_plan.md` … `docs/P9_analytics_economics_engine_implementation_plan.md` (P2 `autonomous_ar_engine`, P3 `content_genome_experiment_framework`, P4 `music_generation_factory`, P5 `quality_rights_provenance_release_gate`, P6 `playlist_visual_video_factory`, P7 `youtube_publishing_engine`, P8 `dsp_distribution_engine`) | T | Phase specs | Authoritative per phase |
| `docs/plans/2026-09-2*-phase-P*-execution-plan.md`, `docs/plans/2026-09-26-wave-6-loop-closure-plan.md` | T | Wave execution plans | Historical |
| `docs/CONTROL_PLANE_FRONTEND_IMPLEMENTATION_PLAN.md` | T | Operator frontend blueprint, unreferenced | Supporting (orphaned) |
| `docs/superpowers/specs/2026-10-13-plm-visual-dashboard-design.md`, `docs/superpowers/plans/2026-10-13-plm-visual-dashboard.md` | T | Original dashboard spec/plan | Historical |
| `docs/reviews/2026-10-13-system-review-and-remediation-plan.md` | T | R0–R6 findings and gates | Authoritative for stages |
| `docs/STATUS.md` | T-mod | Capability ledger (+~1,300 uncommitted lines) | Authoritative; header stale |
| `docs/HANDOVER.md` | T-mod | 2026-10-16 mid-R5 handover | Historical |
| `docs/SESSION_HANDOFF_2026-10-21.md` | U | Control Deck / loop worker claims | Superseded (overstated) |
| `docs/reviews/2026-10-28-audit-remediation-register.md` | U | A/B 57-row register | Authoritative A/B tracker |
| `docs/reviews/2026-10-28-working-tree-consolidation.md` | U | A02 intake | Supporting |
| `docs/reviews/2026-10-28-b02-render-recovery-assessment.md` | U | B02 design and gaps | Supporting (active) |
| `docs/reviews/2026-10-28-b03-plan-budget-atomicity-boundary.md` | U | B03 budget boundary | Supporting (active) |
| `docs/reviews/2026-10-28-p4-zero-plan-limit-safety.md` | U | B03/B04 evidence | Supporting |
| `docs/reviews/2026-10-28-p4-0040-isolated-postgresql-rehearsal.md` | U | 0040 disposable PG rehearsal | Supporting |
| `docs/PLM_AB_SESSION_HANDOVER_2026-10-29.md`, `docs/PLM_AB_SESSION_MANIFEST_2026-10-29.json` | U | Prior session handover + its JSON index | Supporting (some stale claims, §3.4) |
| `docs/reviews/2026-10-23-content-quality-audit.md` | U | Content/audience audit; disabled unsafe scripts | Supporting |
| `docs/reviews/2026-10-25-*.md`, `2026-10-26-*.md` / `.csv` / `.json`, `2026-10-27-*.md` | U | 0038/0039 migration evidence chain; P6 texture/listening diagnostics | Supporting; migration items mostly historical (0039 applied to dev) |
| `docs/proposals/*` | U | 0038 reconciliation drafts (unaccepted) | Supporting |
| `docs/runbooks/{reconcile-alembic-0038,offline-audio-contrast-review,p6-album-texture-inventory,youtube-reporting-readonly-preflight}.md` | U | Operator runbooks | Supporting |
| `docs/adr/0000`–`0036` | T | Ratified/accepted ADRs | Authoritative |
| `docs/adr/0037`–`0042` | U | A/B ADRs (code-only scope) | Authoritative for A/B code; not rolled out |
| `docs/adr/0043-p6-render-recovery-evidence.md` | U (this session) | B02 persisted recovery evidence | Authoritative for A/B code; not rolled out |
| `docs/reviews/2026-10-29-unified-remaining-work-roadmap.md` | U (this session) | This document | Supporting; supersedes the A/B handover's scorecard view |
| `docs/reviews/2026-10-22-system-audit.md` | branch `claude/program-interface-dashboard-9c6dc4` only | Records upload `aqTf6gzjsNI` | Supporting; missing from main checkout |
| `__nexus_history__/…/INDEX.md` | — | Cited by the A/B handover | **Does not exist** |
| `docs/Self-Improving Trading System Research.md` | T | Unrelated project | Out of scope |

### 4.2 Runtime manifests and records (code locations)

| Concept | Definition | Migration |
| --- | --- | --- |
| C5 release manifest (`ReleaseManifestPayload`) | `packages/plm_contracts/seams/c5.py:54` | — |
| P5 release manifest record | `services/quality_gate/domain/models.py:586`; builder `services/quality_gate/gate/manifest.py:20`; table `qa.release_manifests` `services/quality_gate/storage/models.py:202` | `qa_0011`, `qa_0018` |
| C6 media package / publication | `packages/plm_contracts/seams/c6.py:86`, `:110` | — |
| P6 package `manifest_uri` / `manifest_hash` | `media.media_packages` `services/media_factory/storage/models.py:118` | `media_0012` |
| P6 render job + `input_manifest_hash` | `media.render_jobs` `services/media_factory/storage/models.py:98`; `render_claims.py` | `media_0012` |
| P6 render recovery evidence (new) | `media.render_recovery_evidence` `services/media_factory/storage/models.py` | `media_0042_render_recovery_evidence.py`, revision `0042_media_render_evidence` (code-only) |
| P7 publish manifest, attempts, publications | `services/youtube_publishing/storage/models.py:42`, `:81`, `:106`; builder `services/youtube_publishing/manifests.py:96` | `youtube_0013`, `0033`, `0038` |
| P8 release, ISRC allocations | `services/distribution/storage/models.py:35`, `:83` | `distribution_0014`, `0019` |
| P4 attempts, candidates, lineage, rights, costs | `services/music_factory/storage/models.py:71`, `:126`, `:161`, `:171`, `:189` | `music_0010`…`0041` |
| Migration backup manifest | `data/migration_backups/0039_local_plm_20261027T141743Z_c3e08a9cd61f/manifest.json` (git-ignored) | — |

No `MANIFEST*` file exists anywhere in the repository.

---

## 5. Unified remaining-work register

Each item merges every plan that describes the same underlying issue. "Tested" names the environment; mocks, SQLite and in-process TestClient runs are not production evidence.

| ID | Underlying issue | Sources (P / R / A-B / other) | Implemented | Tested (environment) | Unverified | Gate / dependency | Done when |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **U1** | Uncommitted, mixed-provenance tree; no reproducible revision | A02, A03; RC2; PL-03; R0 gate (CI) | Inventory docs only | Full Ruff/mypy/pytest on the exact snapshot (this session, §7) | CI on a committed revision | D3 given; grouped commits made locally (not pushed) | Provenance-grouped commits, CI green on that SHA |
| **U2** | Full chain not re-proven after A/B changes | A03, X02, RC2, R5-MEM, PL-01, PL-04; R5 gate; P0 §1 | Destructive-test isolation guard (`PLM_R5_ISOLATED_STACK`) | 12 guard unit tests; old R5 double-run (pre-A/B code) | Isolated compose R5 ×2 on consolidated SHA; memory budget | U1; isolated stack (`plm_r5_*` DB, loopback) | Two consecutive isolated R5 passes on the U1 SHA, resource profile recorded |
| **U3** | P6 long render is not durably recoverable | B02; R5-RENDER, R5-TIME, X04, RC3; R3 gate (worker kill/resume); P6 plan | Queue/claim/finish/quarantine fencing; claim-scoped namespaces; read-only inspection; staged retry; **this session: persisted recovery evidence replaces caller booleans, owner-observed exit quarantine** | SQLite unit; disposable PG16 (this session: existing 5 + new race tests) | Independent supervisor process, heartbeat/lease, real FFmpeg kill/restart, MinIO byte reconciliation, multi-worker compose | Migration `0042_media_render_evidence` rollout needs approval | Kill-restart test on real FFmpeg+MinIO+PG passes; R5 with forced worker death recovers |
| **U4** | Spend authorization and money semantics incomplete | B03; RC4, RC5, PL-02, P3-02, P4-04 (budget); R3 cost ceiling; ADR-0038–0041 | Unpriced/legacy cost provenance; zero/NaN limits refused; paid-replay refusal; reservation replay; **this session: paid attempts made durable before/after each REAL call** | SQLite unit; disposable PG 0039→0041 | Concurrency-safe **positive** per-plan limits; C3 currency (needs ADR, frozen seam); invoice/receipt reconciliation (no P4 path exists — Suno reports no credits, so real multi-variant plans stall); invented planner `credit_limit`; unguarded Suno `/download/authorize`; pipeline drops a failed step's receipts; 0040/0041 rollout | ADR for C3 currency; DB rollout approval | Positive per-plan limit holds under concurrent PG runs; receipts reconciled from a provider record |
| **U5** | Generation provenance / ranking | B04; P4-03, P4-04, P4-06, QA-RIGHTS; R6 (P4 provider) | Blank provider job IDs refused; no invented lyrics/model (this session also removed the Suno client's `chirp-v3-5` default and kept clip IDs on any post-submit error); C4 join checks; capacity 4–10 | SQLite unit, contract tests | Real provider job IDs, commercial entitlement, ranking beyond first success; vocal requests still send the prompt in Suno's lyric field | R6 approval for real provider | Ranked selection on recorded real responses; entitlement evidence stored |
| **U6** | Rights / similarity are risk signals only | B05; P5-01, P5-03, QA-REUSE, R5-P5-SCALE; R4 | Detectors, UNKNOWN→quarantine, protected-artist policy | Unit + golden fixtures | Reference corpus, labelled positives/negatives, scale benchmark | Human/legal hard stops | Calibrated thresholds on labelled set; bounded scan time at catalog scale |
| **U7** | Listening/diversity and legacy packages | B06; P6-03, QA-LEGACY, QA-LISTEN | Texture signatures, near-copy gate, review tooling | Unit; local read-only audits | Human-reviewed labels; legacy 8 READY packages backfill or hold | Operator-approved backfill | Labelled review set; legacy packages explicitly re-QA'd or held |
| **U8** | Media timing and player-visible publication evidence | B07; P6-04, QA-CHAPTER, QA-OLD-MP4, P5-02 | Stream-duration QA, chapter validation, P7 legacy hold | Real local FFmpeg, loopback P6→P7 | Watch-page chapter check; old MP4 tails | R6 for live checks | Player evidence recorded per published video |
| **U9** | P1 identity and duration taxonomy | B01; R5-OBS, R5-DUR, P1-01, P1-03; R2 | Raw-ID lookup (409 on ambiguity), measured-duration v2, v3 design (off) | SQLite, disposable PG | Historical v1↔v2 arm/score mapping, live P1→P2→P3 | Migration strategy approval | Mapping applied on a clone, R5 consumes v2 arms |
| **U10** | Measurement and economics | P2-02, P9-02, RC5; R2; Later economics gate | Typed snapshots, Decimal/FX, proxy versioned | Gate R2 tests (SQLite) | C7 `estimated_revenue` float; EEV; capital allocation | Economics gate (not approved) | Separate economics gate |
| **U11** | Live adapters and staged rollout | R6; P1-02, P7-01, P8-01, P8-02, P9-01; plus §3 governance | P7 real client, P9 Analytics/Reporting reads (uncommitted, unapproved) | Mock HTTP; some live reads/uploads outside the guard | Everything under READ_ONLY→SHADOW→LIVE; distributor, UPC, P20; Spotify/Trends | R6 approval (D1 recorded the past work only) | Per-capability conformance on recorded responses, then shadow, then guarded live |
| **U12** | Truthful status | RC6, PL-01; R0 | Many corrections | — | STATUS header, HANDOVER, SESSION_HANDOFF overstatement | none | Docs agree with each other and with code |
| **U13** | Deferred architecture | PL-05; PARTS X, XII, XIII, XXIII, P10 | absent | — | — | Later | Separate plan |

The 27 "implemented—reverify" register rows are not separate work: they close through **U1 + U2** (same-SHA unit/contract/integration and R5 evidence), plus the row-specific checks in the register's "remaining verification" column.

---

## 6. Recommended order

1. **U3 (B02) code batch** — highest-value authorized engineering; no external writes; isolated PG available. *(Persisted evidence + owner-exit fencing done this session; supervisor/heartbeat and real kill/restart remain.)* Then **U4/U5 paid-attempt durability** *(done this session)*.
2. **U12** — status truth fixes (cheap; done in part this session).
3. **U1** — needs D3. Without commits, no later gate can be tied to a revision.
4. **U2** — isolated R5 ×2 on the U1 SHA. Needs a separate compose project (own DB, ports, MinIO bucket); the shared dev DB must never run the destructive R5 suite.
5. **U4 → U5** — positive per-plan limits need the C3 currency ADR first (frozen seam).
6. **U9, U6, U7, U8** — require labelled data, clones or human review.
7. **U11** — only after an R6 approval (D1 recorded the past live work; it did not approve R6).
8. **U10, U13** — later gates.

---

## 7. Verification record for this session

See STATUS.md "Takeover audit and B02 recovery evidence (2026-10-29)" for exact counts, commands and boundaries.
