# PLM A–B Remediation: Detailed Session Handover

**Handover snapshot:** 2026-10-29 (Asia/Seoul)  
**Project:** PLM autonomous music discovery / generation / release / measurement platform  
**Selected connected-PC root:** `C:\Users\riper\Downloads\PLM`  
**Bridge workspace:** `main`  
**Nexus run identifier:** `a784bde74dea41449f95a58ede3dcab1`  
**Requested overarching objective:** Complete remediation phases **A–B** from `docs/reviews/2026-10-28-audit-remediation-register.md` by making real, tested code changes, not merely writing analyses.  
**Overall result:** **In progress, NOT complete.** Latest explicitly retained audit count: **27/57 implemented or implementation-awaiting-verification (47.4%), 22 partial, 8 open; 0 formally closed**. These counts have **not been formally revised** by later B02 partial fixes.  
**Connection caveat:** The PLM PC bridge was working for all previous modifications and tests described below. While preparing *this* handover, the bridge repeatedly returned `Tunnel-client did not poll after this request was enqueued`; therefore the handover and companion manifest were generated as **downloadable conversation artifacts**, **not copied into the PC repository**, and neither a current repo scan nor a new test run was possible during handover preparation. Source paths and results below are drawn from actual earlier bridge reads/edits/commands and prior recorded session state, not from a fresh post-disconnection scan.

> **Key distinction:** A test pass in a fake SQLite/stand-in setup validates a narrower contract than a real PostgreSQL + FFmpeg + MinIO + process-kill/restart validation. Do not describe B02 as fully recovered, A–B as done, or R5/R6 as passed on the strength of these tests.

---

## 1. Start here: exact paths for plans, manifests, and authorities

All paths below are **relative to** `C:\Users\riper\Downloads\PLM` unless otherwise shown.

### 1.1 Authoritative implementation plans and audit tracker

| Item | Relative path | Purpose / authority | Verification status |
|---|---|---|---|
| **Current A–B implementation and findings tracker** | `docs/reviews/2026-10-28-audit-remediation-register.md` | 57-item audit register, A01–A03/B01–B07 workstreams, per-finding progress and latest appended B02/B03/B04 evidence | **Previously read and edited on connected PC** |
| **Master integrated implementation plan** | `docs/P0_master_integration_implementation_plan.md` | P0 master integration implementation and phase plan links | **Previously linked from repo README/AGENTS; file body not reread in the latest task** |
| **Staged R0–R6 system review/remediation plan** | `docs/reviews/2026-10-13-system-review-and-remediation-plan.md` | Approved stages and gate requirements. R-stage approval must be respected | **Previously linked from repo AGENTS/README; latest body not reread** |
| **B02 render recovery assessment and next boundaries** | `docs/reviews/2026-10-28-b02-render-recovery-assessment.md` | P6 render claims, isolation, inventory, retry staging, worker recovery gaps | **Previously read and edited on connected PC** |
| **B03 atomic budget design boundary** | `docs/reviews/2026-10-28-b03-plan-budget-atomicity-boundary.md` | Money/currency/budget reservation boundaries; explicit contract and migration issues | **Previously referenced in PLM work, not freshly reread** |
| **A02 worktree consolidation assessment** | `docs/reviews/2026-10-28-working-tree-consolidation.md` | Why other users' concurrent/untracked changes must not be staged blindly | **Previously referenced, not freshly reread** |
| **P4 zero-plan/limit and replay safety notes** | `docs/reviews/2026-10-28-p4-zero-plan-limit-safety.md` | B03/B04 changes and known missing guarantees | **Previously edited and/or referenced** |
| **Current capability/status ledger** | `docs/STATUS.md` | Real vs fake/partial capability and recent tests; must match scope | **Previously read and edited on connected PC** |
| **Architecture** | `docs/architecture.md` | High-level P1–P9 design | **Referenced by AGENTS.md, content not newly inspected** |
| **Cross-phase frozen contracts registry** | `docs/contracts/CONTRACT_REGISTRY.md` | Frozen C3/C4/C5/C6 seams; cross-phase changes require ADR | **Previously linked in README/AGENTS** |
| **External-write replay/budget ADR** | `docs/adr/0041-external-write-budget-reservation-replay.md` | Prior B03 reservation/ledger reasoning | **Referenced in prior PLM handovers** |
| **Unknown-cost provenance ADR** | `docs/adr/0039-p4-credit-unknown-provenance.md` | Credits vs priced currency and historical provenance | **Referenced in prior PLM handovers** |
| **Repo rules** | `AGENTS.md` | Hard safety rules, no fake evidence, no external writes without guard, test conventions | **Read on connected PC** |
| **Repo introduction** | `README.md` | `docs/STATUS.md`, master plan, staged review and source tree links | **Read on connected PC** |
| **Historical Nexus handover index** | `__nexus_history__/a784bde74dea41449f95a58ede3dcab1/INDEX.md` | Nexus run's handover/history index; **not** the release/media manifest | **Path returned by bridge access; not modified** |

**Per-phase plans:** `README.md` identifies plans of the form `docs/P1_...` through `docs/P9_...`. Exact complete filenames were **not enumerated in the last verified reads**. Do not fabricate individual names.

### 1.2 Where is “the manifest”?

There are **three distinct meanings** of “manifest”; keep them separate:

1. **This session's handover manifest (created now):** `PLM_AB_SESSION_MANIFEST_2026-10-29.json` in the same downloadable artifact directory as this file. It indexes the project root, plans, changed files, verification jobs, limits, and the handover file. This is a **new handover index**, not a PLM production payload.
2. **PLM runtime release and media manifests:** Existing code uses C5 `ReleaseManifestPayload` records and P6 media-package `manifest_uri` / `manifest_hash` database fields. Their **concrete object URIs depend on the stored record, environment and instance**; no single on-disk release manifest has been verified for this run. P6 source identities include `input_manifest_hash`. Relevant source areas include `packages/plm_contracts/` (contract vocabulary) and `services/media_factory/` (renderer/media package), but the precise release-manifest schema module was **not verified in the latest connected-PC reads**.
3. **Any pre-existing `MANIFEST.md`, `manifest.json` or project artifact-manifest file:** **Exact path unverified.** No confirmed existing `MANIFEST*` path is present in the latest recorded directory listing. When the PC bridge reconnects, enumerate files using the connector's `bridge_files({pattern:"*manifest*"})`, inspect the resulting paths and update this section. Do not claim a guessed path exists.

Do **not** confuse the Nexus `INDEX.md`, the companion handover JSON, the master implementation plan, and P5/C5's runtime release manifest.

### 1.3 Windows absolute path examples

- A–B tracker: `C:\Users\riper\Downloads\PLM\docs\reviews\2026-10-28-audit-remediation-register.md`
- P0 implementation plan: `C:\Users\riper\Downloads\PLM\docs\P0_master_integration_implementation_plan.md`
- R-stage remediation plan: `C:\Users\riper\Downloads\PLM\docs\reviews\2026-10-13-system-review-and-remediation-plan.md`
- B02 plan: `C:\Users\riper\Downloads\PLM\docs\reviews\2026-10-28-b02-render-recovery-assessment.md`
- B03 plan: `C:\Users\riper\Downloads\PLM\docs\reviews\2026-10-28-b03-plan-budget-atomicity-boundary.md`
- Nexus history index: `C:\Users\riper\Downloads\PLM\__nexus_history__\a784bde74dea41449f95a58ede3dcab1\INDEX.md`

---

## 2. Safety, approvals, and worktree boundaries that MUST survive handover

- **Scope:** User requested actual phase A–B remediation on *PLM*, not the unrelated `rs`/stock-screener repository. Scope is the one connected PC workspace `main`.
- **Do not mark a gate as passed without evidence.** “Implemented”, “partial”, “verified in fake unit tests”, and “production proven” are different levels.
- **External side effects:** No unguarded uploads, releases, paid real-provider calls, takedowns or live publishing. `ExternalWriteGuard.authorize_external_write` is the required authority at an external-write boundary. Never infer approval from provider mode alone.
- **No production database mutation or broad migration rollout without the relevant approval.** Prior migration work was code/disposable database work; production/development DB remained on the earlier revision where noted.
- **No destructive Git/Compose actions, mass reset, clean, force-push or mixed staging/commit.** Existing dirty worktree contains changes made by other concurrent workstreams; provenance has not been consolidated.
- **No runtime import from `tests`**; no invented IDs, receipts, cost exchange rates, or clearance. Missing evidence must block.
- **Determinism:** injected clocks, deterministic IDs and SHA256—not random process hash, not hidden wall-clock-dependent replay identity.
- **Cross-phase contracts (C3/C4/C5/C6)** are frozen absent approved ADR/version handling.
- **User preference:** perform coherent implementation batches first, then fewer grouped test/lint passes; avoid tiny edits interleaved with redundant broad validations.
- **Connectivity:** Do not ask the user to manually run scripts while connected PC execution is available; when bridge is disconnected, report this truthfully, retain artifacts and resume from the exact known state when available.

**Last recorded Git state (after most recent B02 hardening):** `STAGED=0`, `WORKTREE_ENTRIES=265` (`git status --porcelain`), `git diff --check` PASS, job `5e7c7a982b31de2b591d50fd191ee2de`. These are **repo-wide entries, not 265 files authored by this session**; count includes inherited modifications and untracked items. No Git commit or staging performed.

---

## 3. Phase-level scorecard (not a formal closure claim)

| Phase | Scope | Work actually accomplished / present | Material gaps |
|---|---|---|---|
| **A01** | Audit/remediation register | Created or maintained 57-finding A–B register, actual evidence and status notes | 0 findings formally closed as of last recorded tracker |
| **A02** | Working-tree provenance and integration | Traced mixed modifications, ignored `.bridge/`, documented worktree-consolidation risk | Dirty inherited workspace not isolated/staged/merged; 265 changed/untracked entries remain |
| **A03** | Revalidate R0–R5 gates | Multiple historical large nonintegration test passes, isolated smoke/SQL checks documented | No authoritative *post-latest-changes* full R5 full-chain gate; Compose and real providers not verified |
| **B01** | Arm/taxonomy ID and observation API | Versioned/canonical arm ID and 64-char boundary work; ambiguity refuses instead of silently passing; some unit/PG tests | Full taxonomy/versioned contract and empirical behavior not formally closed |
| **B02** | Media render intent, fencing, recovery | Major code work detailed in §§4–7: durable queue/claim, stale quarantine, scoped storage, bounded read-only object inspection, supervisor-gated retry intent, second-attempt execution, added queue/claim/finish fences | Independent supervised process/heartbeat, termination evidence, real artifacts, real PG/FFmpeg kill-restart, full integration |
| **B03** | Provider spending, plan budgets, credits vs money | Zero/invalid limit fail-closed, partial replay/budget reservations, unknown currency cost provenance and migrations documented; see §8 | End-to-end atomic cumulative positive-limit enforcement with source-priced money/receipt verification and C3 currency contract versioning incomplete |
| **B04** | Generation/provider provenance and completion | Paid-replay safeguards, invalid provider IDs fail-closed, C4 provenance guards, plan/lyrics/model constraints, P4→P5 completion protection; see §8 | Real-provider receipts/rights, robust evidence-ranked provider selection and production-quality validation incomplete |
| **B05** | Rights/legal clearance | Existing fail-closed rights gates and documentation maintained | Independently verified corpus, rights lineage and human hard-stops not closed |
| **B06** | Selection/diversity | Some candidate bank and selection constraints worked on | Real diversity and ranking behavior under representative provider data unverified |
| **B07** | Media QA/publishing | P6 checks, mock output contracts and tests integrated | Real-world media and publishing/economic gate remain unverified |

**Audit counter:** 27/57 (47.4%) implemented or awaiting verification, 22 partial, 8 open, 0 formally closed. Later code fixes should not silently increment the counter or mark the B02 row closed. Refresh against the actual audit register before changing counts.

---

## 4. B02 technical architecture and progressive code changes

### 4.1 Durable render intent, owner identity, and terminal fencing

Primary code: `services/media_factory/storage/render_claims.py`, `services/media_factory/storage/models.py`, `services/media_factory/storage/repositories.py`, `services/media_factory/service.py`.

**Before render:** P6 API/CLI calls `assemble_and_persist` with an *independent* `render_session_factory` and edge-owned `worker_id_factory`; an intent transaction first commits `QUEUED`, then a distinct claim transaction commits `RUNNING` before the FFmpeg/toolchain or output-file write. The package caller transaction is separate, so process death can leave a queryable RUNNING record instead of erasing evidence. Direct in-process legacy callers without the factory preserve their original behavior.

**`queue_render_job`:** Validates QUEUED status, no preexisting worker/start/end/output/error, 64-hex input hash, deterministic job ID derived from package/profile/attempt, aware created timestamp, and required fields. Uses PostgreSQL/SQLite dialect `INSERT ... ON CONFLICT DO NOTHING RETURNING` rather than a nested SAVEPOINT (which previously had a SQLite rollback/phantom-intent bug). Duplicate exact intent is idempotent; conflicting fields fail. As of the latest B02 hardening, generic queueing is **restricted to attempt_count = 1**. Later attempts use the reconciled retry stage only.

**`claim_queued_render_job`:** Conditional SQL UPDATE atomically transitions `QUEUED → RUNNING` only for exact job ID and original SHA256, with no worker already present. At the **latest** update it also refuses a claim when a persisted package is already present, a higher attempt exists for that package/profile, or an attempt >1 lacks an adjacent completed `QUARANTINED/render_run_stale` predecessor matching input hash and render version. Worker + timestamp are persisted; second claim returns false. Claim does *not* establish actual FFmpeg process supervision.

**`finish_claimed_render_job`:** Accepts terminal COMPLETED / FAILED / QUARANTINED only, validates completion checksum or failure error_code, and conditionally UPDATEs exact RUNNING job + worker + input hash + original timestamp. The latest update also refuses a terminal completion if a later attempt or already persisted package appeared. The package record and terminal transition commit together in the package caller's transaction; a late worker cannot turn its stale output into a publishable package via this function. Real process termination remains unproved.

**`quarantine_stale_render_job`:** Bounded candidate scan returns old RUNNING work for *review*; age alone cannot imply dead worker. The state move to QUARANTINED requires a `worker_termination_confirmed=True` attestation, original worker ID, original start, exact hash and cutoff, then a conditional UPDATE. It fences the old worker at SQL state level but **does not independently verify termination** or delete orphan blobs.

### 4.2 Deterministic claim-scoped artifact namespaces

Primary code: `services/media_factory/artifacts.py`, `services/media_factory/storage/render_claims.py`, `services/media_factory/rendering/assembler.py`, `services/media_factory/rendering/renderer.py`, `services/media_factory/artwork/generator.py`, `services/media_factory/service.py`.

A committed owner claim obtains a 64-char lowercase SHA256 namespace from unambiguous JSON of **[job ID, worker ID, input SHA256, UTC-normalized started_at]**. Render masters, background and thumbnails use independent per-execution keys beneath:

```text
media/renders/<brand_id>/<media_package_id>/claims/<64-hex-namespace>/.../<content-digest>
media/thumbnails/<brand_id>/<media_package_id>/claims/<64-hex-namespace>/.../<content-digest>
```

The file names after the claim root are implementation-specific and content-addressed; the form above is schematic, not a claim that specific production objects exist. `scoped_artifact_root` validates the namespace before any scoped output write. The raw worker token is not present in object URI. C6 continues to receive actual URI/checksum values. Legacy unclaimed direct rendering still uses legacy paths.

**Limit:** Separate prefixes prevent later attempts *naming* the same path as orphan earlier runs. This does **not** establish no running worker, safe deletion, complete blob integrity, or publish/replay authorization. SQLite's timezone-naive persisted starts cannot be losslessly reconstructed without an explicit UTC policy.

### 4.3 Read-only claim artifact inspection

Files: `services/media_factory/artifacts.py`, new `services/media_factory/render_reconciliation.py`, `services/media_factory/cli.py`, `packages/plm_platform/cli.py`; tests in `tests/unit/test_media_render_reconciliation.py`.

Platform command (registered, but real MinIO query **not** executed in this effort):

```text
plm media render-inspect --job-id <persisted-render-job-id> --brand-id <brand-path-hint> --limit 20
```

- Reads exact persisted render job, requires verifiable owner identity to recompute namespace; refuses timezone-naive start rather than silently assuming UTC.
- If completed package exists, checks its brand and stored claim namespace; if no package record exists, brand comes only from an **unverified operator hint**.
- Globally bounded 1–100 metadata-only MinIO LIST of two namespaces, render and thumbnail, reports object URIs, metadata-reported bytes, key-pinned SHA256 or invalid key, count, truncation.
- Does **not** fetch/read bytes; hash in object key is **not** verified data hash. Does **not** write/delete/retry/publish or certify worker death.
- Empty result under an operator hint is **not** evidence that no orphan exists.

### 4.4 Reconciled retry staging and execution (code-only)

`queue_reconciled_render_retry` stages only an independent next QUEUED attempt by a single PostgreSQL/SQLite `INSERT ... SELECT ... ON CONFLICT DO NOTHING`. The SQL checks exact previous QUARANTINED `render_run_stale` job, worker and start, original input hash, `finished_at <= now`, unchanged package/profile/version, no newer attempt, no persisted package. Attempt IDs are deterministic. `max_attempts` defaults to 3 and supports 2..10. It is blocked unless *all three caller-supplied booleans* are exactly `True`: `worker_termination_confirmed`, `orphan_artifacts_reconciled`, `source_inputs_reverified`.

**Crucial remaining hole:** Those three booleans are not sourced from an independent supervised process or persisted supporting evidence. The retry function is a low-level supervisor primitive, **not a secure evidence-verification authority**. Do not present it as autonomous safe recovery.

The internal `assemble_and_persist(..., recovery_job_id=...)` path only accepts a separately staged, still-QUEUED attempt number >1, reevaluates actual computed input hash/profile/render version and package ID, atomically claims it, and passes the matching `render_attempt` through the assembler to renderer. The same original owner/input/start completion fence controls package insertion. Public P6 HTTP/CLI **do not expose** an automatic recovery flag or scheduler.

### 4.5 Input fingerprint regression discovered and corrected

A simulated retry changed the playlist concept yet the render input fingerprint stayed the same, despite the concept affecting title/thumbnail content. Real regression test initially failed. Corrected `MediaRenderer.input_manifest_hash` and `run_preflight` (plus assembler/service call path) to include the **supplied nonempty playlist concept** consistently before claim and at preflight/completion. The direct legacy invocation with an empty/default concept retains historical hash behavior. This is an important code-level safety fix but not a cross-phase schema migration or real media playback test.

### 4.6 Latest hardening: no generic attempt-2 bypass and no obsolete claims

Latest substantive B02 change:

- Generic `queue_render_job` now **rejects any caller-created attempt >1**. Before this patch, a caller with a well-formed deterministic ID could bypass reconciled retry staging.
- `claim_queued_render_job` checks **at point of ownership transfer** for existing package, higher attempt, and—only for retry—a matching QUARANTINED immediate predecessor. A raw legacy or anomalous QUEUED row alone is not authorization.
- `finish_claimed_render_job` independently checks for existing package/higher attempt at point of terminal transition, alongside original worker/start/hash fence.
- `tests/unit/test_media_render_job_history.py` seeds an intentionally abnormal historical second-attempt row with a test-only repository insert rather than reopening the generic runtime queue. Historical inspection continues to display legacy data.
- Added `tests/unit/test_media_render_claim_fence_hardening.py` with targeted bypass, anomalous retry, stale package, newer attempt, late finish, and legitimate initial run tests.

**Actual latest verification:** job `6711db43c5387cc91fe3f7761461371d`: 81 focused P6 API/claims/retry/history tests passed (11 third-party deprecation warnings), changed-file Ruff passed, mypy 465 source files passed, Git diff-check passed. `5e7c7a982b31de2b591d50fd191ee2de` recorded no staged files and 265 worktree entries, with diff-check pass.

---

## 5. B02 files that were modified/added across these recent turns

This is a targeted list **not** the full dirty-worktree listing. Code modules and tests:

| Path | Changed behavior / purpose |
|---|---|
| `services/media_factory/artifacts.py` | Namespace validation, scoped output roots and bounded metadata-only MinIO claim-object listing |
| `services/media_factory/storage/render_claims.py` | Namespace derivation; durable queue, claim/finish, stale quarantine, read-only scan, reconciled retry staging; **latest** generic attempt restriction and claim/finish anti-stale SQL fences |
| `services/media_factory/storage/models.py` | **Inspected**, shows existing render_jobs schema, but latest session did not change schema/migrations for this patch |
| `services/media_factory/storage/repositories.py` | **Inspected** for `get_render_job` / `insert_media_package` / histories; included in prior broader work but not latest B02 edit |
| `services/media_factory/service.py` | Durable separate-session claim, namespace, internal preapproved `recovery_job_id`, package/finish fence |
| `services/media_factory/rendering/assembler.py` | Passes namespace, attempt number and concept-derived input fingerprint to renderer |
| `services/media_factory/rendering/renderer.py` | Claim-specific master/background keys, attempt and concept-aware preflight fingerprint |
| `services/media_factory/artwork/generator.py` | Claim-specific thumbnail storage keys |
| `services/media_factory/render_reconciliation.py` | **New** read-only job/object inventory with explicit uncertainty and no retry/delete |
| `services/media_factory/cli.py` | `render-inspect` command implementation |
| `packages/plm_platform/cli.py` | Registers platform `plm media render-inspect` wrapper |
| `tests/unit/test_media_render_claims.py` | Queue/claim/fence/quarantine/namespace regressions |
| `tests/unit/test_media_render_reconciliation.py` | **New** bounded LIST-only fake-MinIO and provenance tests |
| `tests/unit/test_media_render_retry.py` | **New** reconciled retry staging and rollback/attestation tests |
| `tests/unit/test_media_render_retry_boundaries.py` | **New** published-package and max-attempt constraints |
| `tests/unit/test_media_render_retry_service.py` | **New** fake toolchain crash then approved attempt-2 service flow; changing concept must fail |
| `tests/unit/test_media_render_claim_fence_hardening.py` | **New** latest queue/claim/finish bypass and obsolete-result tests |
| `tests/unit/test_media_render_job_history.py` | Legacy attempt history test fixture adjustment |
| `tests/unit/test_media_rendering.py` | Scoped storage/integrity checks on stand-in rendering |
| `tests/unit/test_media_api.py` | P6 API claim/namespace checks |
| `docs/STATUS.md` | Multiple dated, explicitly PARTIAL B02 entries and verified jobs |
| `docs/reviews/2026-10-28-b02-render-recovery-assessment.md` | Engineering plan, conclusions, proofs and remaining boundaries |
| `docs/reviews/2026-10-28-audit-remediation-register.md` | Auditable partial B02 progress; formal count unchanged |

**Do not confuse “inspected” with “edited.”** The last B02 hardening specifically edited `storage/render_claims.py`, `tests/unit/test_media_render_claim_fence_hardening.py`, `tests/unit/test_media_render_job_history.py` and the three status/review documents.

---

## 6. B02 test chronology and what each run proves

This list is meant to prevent rerunning completed work and prevent falsely promoting a narrow test result into a full gate.

| Run / job ID | Observed result | Interpretation / notes |
|---|---|---|
| `474072a48d2928966b05b79e30cc3abe` | 86 P6 rendering/API/claims/history tests passed, 11 warnings; subsequent Ruff style error | Initial per-execution namespace coverage; **job exit was not overall success** |
| `a993023e9fd590046cc686b4ee80b616` | 21 focused tests passed (34 deselected); test-style Ruff issue | Namespace helper refactor; not full green |
| `27c819733e24ae8a2bb19d5d7956b809` | 2 focused/API tests passed, Ruff, mypy 464 files, git diff-check PASS | Final narrow namespace fixes; no real FFmpeg/MinIO |
| `d0b541b7b6befd9bfa8aa0d579dbc093` | 29 passed, **1 failed**: platform CLI was not registered | Correct regression found during object-inventory integration |
| `7c65c31f08f8adfd8aeded6b473f24e6` | 30 tests passed, Ruff flagged `__all__` ordering | After CLI registration; static issue subsequently fixed |
| `57345ddb424518027ad9668630b4c765` | Ruff PASS, mypy 465 files PASS, git diff-check PASS | Read-only inventory final static batch |
| `32e35f24fcd10f1257b2e092e52799e9` | 103 passed, **1 failed**: changed playlist concept accepted during retry | Real fingerprint defect discovered, fixed in code |
| `cba3f271cd2dfb386a7f0a888a89a467` | 16 retry-focused tests passed; Ruff import-order issue | After fingerprint fix; lint issue corrected |
| `2f75fa6b64363600b2aa6d7615a4587b` | 106 broader P6 tests passed; Ruff PASS; mypy 2 typing errors | Types corrected in follow-up; not overall successful job |
| `ca74387187a099eff83f7ad576bf430f` | 18 retry-focused tests, Ruff, mypy 465 files, git diff-check all PASS | Latest pre-hardening retry-intent code/static verified |
| `6711db43c5387cc91fe3f7761461371d` | 81 focused tests passed; Ruff PASS, mypy 465 files PASS, git diff-check PASS | **Latest after generic queue/claim/finish hardening** |
| `5e7c7a982b31de2b591d50fd191ee2de` | `STAGED=0`, `WORKTREE_ENTRIES=265`, git diff-check PASS | Latest worktree snapshot; not a test run |

**Not executed after latest code changes:** entire repo `ruff check .`, full repo `pytest -m "not integration"`, isolated PostgreSQL integration against latest SQL, Compose full chain, real FFmpeg kill/restart, actual MinIO reconciliation, real providers or external publication.

---

## 7. The next B02 implementation boundary (not yet completed)

1. **Independent worker supervision:** Move long FFmpeg render execution out of the HTTP process into an independently supervised worker/process with unambiguous job execution/termination identity; process group cleanup on timeout/cancellation; test process kill and restart. Do not infer dead worker from `started_at` alone.
2. **Durable heartbeat/lease and provenance:** Version or migrate state carefully (only with approved migration); record heartbeat/lease expiration, who verified worker termination, exact execution identity, time, evidence reference and result. Existing `worker_termination_confirmed=True` flag is insufficient as the sole proof.
3. **Orphan reconciliation:** Enumerate exact claim namespace; verify actual bytes and content digest/QA/provenance; distinguish incomplete/unknown/confirmed outputs; create auditable safe-reclaim policy. No automatic deletion based on a LIST result or age.
4. **Atomic recovery gate:** Pair persisted supervisor evidence with transition so a caller cannot freely assert three booleans. Preserve the old worker completion fence after retry.
5. **Isolated PostgreSQL concurrency:** Use opt-in disposable DB fixture in `tests/integration/test_media_render_claims_postgres.py` (requires `PLM_RENDER_PG_ISOLATED=1` and explicitly isolated local database). Do not write production or shared test DB.
6. **Real FFmpeg and MinIO:** Test partial-object upload, crash, stale-write exclusion, re-run with a new namespace, checksum and cleanup in a fully isolated environment. Only then consider raising B02 from partial.
7. **Review contract/migration impact:** Any change that alters a cross-phase frozen seam or existing deployment schema requires appropriate ADR/migration plan; avoid accidental production change.

The current B02 SQL fences are necessary **but not sufficient** for automatic, provably safe recovery.

---

## 8. Other A–B work carried out earlier in this remediation effort

### 8.1 A01/A02/A03: audit inventory, worktree and verification

- Audit register tracked A–B findings individually rather than treating a test pass as formal closure; work documented in `docs/reviews/2026-10-28-audit-remediation-register.md` and `docs/STATUS.md`.
- Worktree had inherited edits/untracked files and new `.bridge/` artifacts; `.bridge/` excluded from tracking. A02 consolidation plan is `docs/reviews/2026-10-28-working-tree-consolidation.md`. **Do not stage/commit/reset 265 mixed entries as a single change** without provenance.
- Earlier large nonintegration regression snapshots were reported (examples in previous handovers include 3,027, 3,056, 3,086 and ~3,115 passed with some skips/integration tests excluded), plus Ruff and mypy at the then-current source baseline. **These older passes do not certify the current latest B02 patch**, which has only the focused 81-test result recorded. No unapproved R5 or R6 production-equivalent run.

### 8.2 B01: versioned arm IDs / observation API

- The previous effort improved canonical/versioned content-arm identity, ID-length handling (64-char limits) and invalid/ambiguous observation API responses. A reported API behavior change returns **409 for ambiguous input** rather than quietly selecting an arm. Earlier evidence included ~19 focused API tests, then ~104 B01-focused tests and three PostgreSQL tests in follow-ups.
- Full B01 taxonomy governance and provider/contract-dependent gate still require the audit register's explicit per-finding verification, not inference from tests.

### 8.3 B03: budget, credits/currency, provider request replay

- **Unknown provider prices must remain unknown:** removed invented conversion `actual_cost = cost_credits * 0.05`. New attempts/cost records separate provider credits from actual priced currency. Unknown actual amount/currency/receipt remain `NULL` with `money_basis='unpriced'`; reject a priced numeric zero masquerading as unknown. Existing historical values retain explicit `legacy_unverified` labeling.
- Added/reviewed an opt-in/code-only `0040_p4_unpriced_costs` Alembic migration and a later `0041` reservation-related migration/ADR. Earlier isolated PostgreSQL migration tests reported `0039 → 0040 → 0041` PASS. **Configured development DB remained at 0039; do not state production migration applied.**
- P4 zero-plan budgets and zero credit limits fail closed; NaN/Infinity/invalid limits refused. Earlier focused tests included a 52-case batch. Some replay/cost reservation and external-write guard logic was implemented across `services/music_factory/generation/engine.py`, `packages/plm_platform/execution.py`, `packages/plm_platform/pipeline/http.py` and related ledger/client modules/tests, with ADR-0041.
- **Unresolved:** cumulative positive-limit budget enforcement under concurrency, durable provider receipt/provenance and reconciliation, exact currency-priced money (vs credits), C3 frozen contract versioning, authoritative R5 end-to-end evidence. `docs/reviews/2026-10-28-b03-plan-budget-atomicity-boundary.md` is the design context. Avoid guessed FX or priced amounts.

### 8.4 B04: provider job ID, C4 event and candidate provenance

- P4 real paid replay: a legacy `SUCCEEDED` generation attempt with an empty/whitespace provider job ID must **not trigger an additional paid provider call**, even if a candidate record already exists. It requires external reconciliation before further funding.
- C4 event builder refuses incomplete provider IDs (`provider_job_id_missing`); a candidate bank rights snapshot cannot be presented as verified if its producing attempt lacks a provider job ID. No invented rights evidence.
- Earlier code touched `services/music_factory/generation/engine.py`, `services/music_factory/api/events.py`, `services/music_factory/candidates/bank.py`, `services/music_factory/generation/planner.py`, `services/music_factory/api/routes.py`, `packages/plm_platform/pipeline/http.py`, various P4/C3/C4 contract/unit tests and documents.
- Additional work protected model/lyrics/provenance consistency, P4→P5 handoff completion/failure gates and candidate capacity (4–10 where required), but real provider quality/ranking/rights remained unverified.
- Later focused evidence reported **275 P4/C3/C4 tests passed**, Ruff, mypy 464 files and diff-check PASS; code-only replay fixes did **not** run live paid providers or assert real licensing proof.

### 8.5 B05–B07

- Rights clearance, diverse selection, and downstream real media QA remain material open areas. A mock C5→C6/P6 test is not proof of rights-cleared publication, healthy economics or a production deployment.
- Preserve human hard-stops for copyright/platform disputes, KYC/account/payment changes and other mandated external processes.

### 8.6 Earlier migration/review safety work preceding the current A–B sprint

Earlier PLM sessions (before the 2026-10-28 audit register work) also addressed Alembic `0038_release_reviews` migration safety, data snapshot and comparison evidence. Reported source areas included `packages/plm_platform/migration_safety.py`, `packages/plm_platform/cli.py`, `migration_review_snapshot.py` and `migration_review_compare.py`, plus associated unit/integration tests, `docs/reviews/*`, `docs/STATUS.md`, and `docs/runbooks/reconcile-alembic-0038.md`. Earlier regression evidence included up to **2,805 non-integration tests passed** and mypy over the then-current **463 source files**, with integration checks excluded. This is **historical context**, not an assertion that the migration was applied to any current production DB or that the latest B02 code passed those full tests. The full exact edit set from those older sessions was not re-enumerated during this handover; confirm filenames from Git/STATUS before attributing a line change.

Earlier B02 timing adjustments were also reported (render timeout **600 → 3,600 seconds** and associated allowance **570 → 3,300 seconds**) with a focused 11-test check. Whether those settings should remain in the final worker architecture must be revisited when durable supervision and lease semantics are designed; a longer timeout is not a worker-termination proof.

---

## 9. Known tests, verification suites and execution protocol

Last confirmed connected-PC execution mechanism (do not assume currently connected):

- `tools.mcp__PLM__bridge_access({})`: confirm `main` and permissions.
- `bridge_read` / `bridge_search` / `bridge_files`: read actual current files. `bridge_read` limits ranges to ≤400 lines; fresh SHA256 required for `bridge_edit`.
- `bridge_edit` (exact old/new and latest SHA256), `bridge_create` (new paths only): authorized file edits.
- `bridge_run({workspace:"main",request_id:"unique-id",command:"...",timeout_seconds:...})`, then `bridge_job({job_id,offset,limit})`: real test execution. **Never double-submit a still-running job.** Commands should be tests/status only, not PowerShell scripts that write source files.
- Approved test baseline described by `AGENTS.md`: Ruff `ruff check .`; mypy `mypy packages services`; nonintegration `pytest -m "not integration"`. Full suite can be heavy; perform a coherent code batch then grouped checks. Isolated integration requires authorized disposable services and no shared DB changes.

**Last B02 grouped command coverage:**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_media_render_claim_fence_hardening.py `
  tests/unit/test_media_render_claims.py `
  tests/unit/test_media_render_retry.py `
  tests/unit/test_media_render_retry_boundaries.py `
  tests/unit/test_media_render_retry_service.py `
  tests/unit/test_media_render_job_history.py `
  tests/unit/test_media_api.py -q
.\.venv\Scripts\python.exe -m ruff check `
  services/media_factory/storage/render_claims.py `
  tests/unit/test_media_render_claim_fence_hardening.py `
  tests/unit/test_media_render_job_history.py
.\.venv\Scripts\python.exe -m mypy packages services
git diff --check
```

Observed success: 81 tests passed, 11 third-party warnings, Ruff pass, mypy 465 source files pass, Git diff-check pass. The last actual command was not rerun for this handover because the PC connection was unavailable.

---

## 10. Known blockers, boundaries and priority next actions

### 10.1 Immediate next-session recovery checklist

1. Reconnect the **PLM** bridge; confirm root `C:\Users\riper\Downloads\PLM`, workspace `main`, permission scope, exact dirty worktree. Do not switch to unrelated stock-screener/rs project.
2. Read `AGENTS.md`, `README.md`, `docs/STATUS.md` recent B02/B03/B04 entries, and A–B register; verify no one else has changed the relevant files. Check `__nexus_history__/a784bde74dea41449f95a58ede3dcab1/INDEX.md` for prior checkpoints if needed.
3. Enumerate project filenames containing `manifest`; **resolve actual manifest location**, distinguishing runtime C5 release manifests, P6 media objects, and a static `MANIFEST` file if any. Update the handover manifest if a real path is found.
4. Inspect the latest B02 claim/staging code and the new fence test. Avoid repeating already passed 81/106/18/30-test batches unless source changes or environment invalidate the results.
5. Choose **one substantial next implementation batch**: (a) credible independent worker/termination evidence and durable reconciliation or (b) a separate isolated PostgreSQL race suite for latest queue/claim/finish fences. Real end-to-end tests require explicit isolated infrastructure.
6. Address A02 provenance separation before a commit or stage; preserve all unrelated edits. Continue B03 atomic positive-limit budget design without fake money/receipts.
7. After meaningful code implementation, run one grouped targeted test/lint/mypy/diff check, then authoritative gate(s) only if feasible and permitted. Record job IDs, failures, skipped tests and unverified side effects in STATUS and register.
8. No final A–B DONE marker until the register's closure gates are truly satisfied.

### 10.2 Unverified / explicitly NOT done

- Live connection to PC during handover generation (bridge timeout/disconnected).
- Existing static `MANIFEST.md`/`manifest.json` project pathname; exact C5 release manifest schema source pathname; specific live `manifest_uri`/`manifest_hash` storage object.
- Post-latest-B02 code full repo regression and latest-postchange real isolated PostgreSQL contention.
- Independently supervised render worker, durable heartbeat, authenticated stop/termination evidence or safe automated orphan deletion.
- Real FFmpeg process crash/restart and real MinIO object-byte verification.
- Full production-equivalent R5, R6 economics, real providers, rights-cleared external publishing.
- All B03 priced-currency atomic budget limits and complete B04 evidence-based provider ranking.
- A02 multi-workstream provenance consolidation; any Git commit or clean staging.
- Full A–B formal closure; total count remains the previously recorded 27/57, not a newly verified audit count.

### 10.3 Last known running jobs / side effects

- Last poll showed final test job `6711db43c5387cc91fe3f7761461371d` **SUCCEEDED** and final status job `5e7c7a982b31de2b591d50fd191ee2de` **SUCCEEDED**. **No running job was reported at the end of the prior working turn.**
- No shared DB migrations, live provider requests, MinIO cleanup, external publishing, git staging, or commits were performed in the last turn.

---

## 11. Compact transfer contract for the next assistant

**Goal:** Implement and validate A–B audit remediation on PLM. **Run:** `a784bde74dea41449f95a58ede3dcab1`. **Root:** `C:\Users\riper\Downloads\PLM`, workspace `main`. **Authority:** `docs/reviews/2026-10-28-audit-remediation-register.md`, `docs/STATUS.md`, `docs/P0_master_integration_implementation_plan.md`, `docs/reviews/2026-10-13-system-review-and-remediation-plan.md`. **Current work:** B02 retry queue/claim/finish fencing hardened; latest 81 targeted tests and Ruff/mypy 465/diff PASS; B02 not closed. **Last Git:** 265 changed/untracked worktree entries, zero staged. **Critical gap:** real supervised worker evidence/heartbeat, actual FFmpeg and MinIO reconciliation, isolated PostgreSQL concurrency, R5. **Constraints:** no fake rights/money/receipt/production proof, no unapproved external writes, DB migrations, or mixed Git staging. Batch implementation before verification. **Handover creation caveat:** companion files are downloadable artifacts only while connected PC bridge is down. **Next:** reconnect PC, confirm paths/status, locate actual manifest(s), implement and test the next substantive safe B02/B03 batch; update register without claiming closure.

**Nexus continuation marker (historical intent, not a claim of completion):** `NEXUS_CONTINUE a784bde74dea41449f95a58ede3dcab1`.
