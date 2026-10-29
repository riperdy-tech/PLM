# PLM capability status

**This file is the source of truth for what the system actually does.** Plan checkboxes,
commit messages and the dashboard record intent or progress; this file records verified
capability. Update it in the same change that alters a capability.

- Last verified: 2026-10-15, branch `review/r5-full-chain` (R5 of the remediation plan;
  R4, R3, R2, R1 and R0 on their own `review/*` branches).
- Evidence: the review in
  [`reviews/2026-10-13-system-review-and-remediation-plan.md`](reviews/2026-10-13-system-review-and-remediation-plan.md),
  which has file:line references for every finding.

## A–B original-audit remediation restart (2026-10-28)

A finding-by-finding register now covers original systemic causes RC1–RC6, every
critical/high/medium audit bullet, and later R5/content-quality defects:
`docs/reviews/2026-10-28-audit-remediation-register.md`. **The A–B program
is in progress, not complete.** Findings are triaged but not independently
closed against a consolidated Git revision. The working tree still contains
substantial pre-existing modified/untracked work; no reviewable consolidation,
new full Compose R5 run or release commit has yet been demonstrated in this
restart. Existing 2026-10-27 local Alembic 0039 migration is a completed
subtask, not completion of the original audit.

A03 safety prerequisite (2026-10-28, **code-only, no R5 runtime
pass yet**): the destructive full-chain test
`tests/integration/test_r5_full_chain.py` now has an autouse pre-write
guard. It skips unless `PLM_R5_ISOLATED_STACK=1` and fails an
explicitly requested run unless PostgreSQL is on loopback with a
`plm_r5_` disposable DB name and all phase API endpoints are
loopback. The suite otherwise resets P2 posterior/exposure data and
alters policy/channel/QA state; mock provider mode alone does **not**
make the normal development database safe. The isolated
P1-only PostgreSQL test does not prove that the R5 Compose gate ran.
The guard's 12 pure Python tests **passed** for missing opt-in,
bad/shared/remote DB targets, bad service URL, empty endpoint set and
valid isolated addresses (job
`6aaf638df51fa7bb6dd6a2741ea0fae3`). The 8 R5 cases
selected without the `full_chain` marker were skipped by the
marker itself; **separately** selecting the actual R5 integration case
with `-m full_chain` and clearing opt-in verified the real autouse
fixture **SKIPPED before database or phase work** (1 skipped,
7 deselected, job `c99343cf6308a30b8678d1c36fb9c877`).
Ruff/mypy of **464 files** and Git diff-check passed after the
last isolation-test import correction (job
`88c70ffdd70a11d451eab331452d522e`).
The full non-integration suite after this change **PASSED
3,166 tests, 1 skipped, 87 integration tests deselected, 16 warnings**
in 293.06s (exit 0, job `8bae8962fde225374f380967b17698a6`,
request `plm-AB-20261028-a03-r5-guard-final-full-nonintegration-01`).
The explicit opt-in **invalid-DB path has unit evidence**, not a
live destructive test (unsafe to use a shared DB as a negative test).
No isolated R5 full chain or double-run has passed yet.

A02 intake: on branch `review/r6-live-adapters` at HEAD `4a35ce2`,
the inherited working tree had **62 tracked modified paths and 134
untracked paths**; many changes precede this task. Existing `.bridge/`
scratch was excluded from Git (the `data/` backup path was already
ignored), and two pre-existing trailing-blank-line defects were
corrected. **No bulk staging, commit or destructive cleanup** was done.
A new read-only inventory after A/B edits found **96 tracked modified,
156 untracked, 252 total paths** on the same branch/HEAD
(job `2efbea2561e0310b98d7b2eef9ced65b`).
**0 staged paths** were independently confirmed (job
`df9fd54bb24cc4453bf21948cce548eb`); unstaged and staged
`git diff --check` both passed. These are inherited mixed source
changes plus the current task, **not** completed or committed audit
findings. No bulk staging/cleanup was performed.
See `docs/reviews/2026-10-28-working-tree-consolidation.md`.

B01 partial resolution of the R5 observation-identity bug: C1 still
stores/exports raw observation IDs; the read-only
`GET /market/v1/observations/{id}` endpoint now accepts a raw ID when it
uniquely identifies one normalized observation, returns the actual
normalized ID plus raw provenance, and rejects ambiguous source links
with HTTP 409 instead of choosing the first. The original normalized-ID
lookup remains supported; malformed and unknown IDs return 404.
ADR-0037 records this narrow backward-compatible API policy.
**19 focused P1 API and pipeline tests passed**. A fresh read-only
REPEATABLE READ local PostgreSQL query sampled the 100 most recent P1
score rows: **124 distinct raw evidence IDs, 124 with exactly one
normalized observation, zero orphaned, zero ambiguous** (no write).
This checks their database join, **not yet a deployed HTTP API/Compose
R5 regression**.
B01 measured-duration taxonomy **code-only partial remediation**
(ADR-0042): P1 now classifies `duration_bucket` from stored observed
`video_duration_seconds`, never from guessed phrases in a video's
title or description. Six controlled intervals have tested exact
boundaries; absent duration remains unclassified and invalid numeric
values are refused. Cache fingerprints include measured duration
buckets, preserving equal-text content distinctions. Default taxonomy
and rule classifier advance to v2; v2 arm fingerprints and readable
IDs are prefixed with their taxonomy namespace so reclassification
**does not modify existing v1 arm support/evidence**. P1 recomputation
expands a single cached classification to all actual observations in
that class at `as_of` and excludes future evidence. It also carries
versioned confidence keys into scoring. SQLite persisted-path
tests passed **49 focused tests** (job
`c6bd2e2871fe2d7bd2c038c541f807e7`).
A **disposable local PostgreSQL** stored-observation → cached
classification → versioned arm → repeat-recompute test passed
**1 integration case** (job `92bf7d8b84e173986f91ce7fbde7b2c1`).
P1 unit and C1/C2/C3 cross-phase tests **376 passed** (job
`23546d27b56e2b04a8c184c8152db140`).
Whole-repository Ruff, mypy (**464 files**) and Git diff check
passed (job `d889bbe444fa1a388dd536c0af9756bb`).
B01 code-change non-integration regression passed **3,153 tests,
1 skipped, 86 integration tests deselected, 16 warnings** in 303.89s
(exit 0; job `78b45c7411055c4a96bec2f82e77bcc1`).
A subsequent R5 test-configuration correction preserves the original
v1 mock outcome keys and adds the **actually produced** v2 CI corpus
IDs (`p1_taxonomy_v2_lofi_study_focus_45_75m` and
`p1_taxonomy_v2_rnb_45_75m`). The first focused check exposed a
guessed R&B use-case suffix, corrected from actual fixture evidence.
After correction, P1 R5 fixture, classification/arm and mock-model
focused suite **42 passed** (job
`aab78a8fd856393d3f0f2e48322269c5`).
The 3,153-test run precedes the final R5 fixture/config changes.
Same-revision repository Ruff, mypy (464 files) and Git diff-check
**PASSED** (job `97cf1138f7c3a814b44336167b55e00b`).
R5 alias-change full non-integration regression **PASSED 3,153 tests,
1 skipped, 86 integration tests deselected, 16 warnings** in 307.35s
(exit 0, job `455367cc54b406114acf54702147cc20`).
A follow-up B01 collision guard now makes v2 score recomputation
**reject** a human-readable arm slug already held by a different
taxonomy fingerprint, rather than mistakenly attributing new scores
to the incumbent (previous legacy repository callers retain their
behavior). Test case proves two otherwise-identical lo-fi arms
differing only in mood cannot be silently merged. **62 P1/C1/C2
focused tests passed** (job `4bb3d611c85641bb9d4a930d07a65f50`),
**2 opt-in disposable PostgreSQL integration cases passed** including
the collision safety case (job `788d1639f82e7cd16b19180f023af033`).
After collision guard, Ruff, mypy (**464 files**) and diff-check
**PASSED** (job `5b5e2516b7ae6bb35f3b3ea17d90c735`).
The 3,153-test result predates this additional collision guard.
Full post-collision non-integration gate **PASSED 3,154 tests,
1 skipped, 87 integration tests deselected, 16 warnings** in 305.07s
(exit 0, job `6bd04662436448e18a12d724278bcbe0`,
request `plm-AB-20261028-b01-arm-identity-guard-full-nonintegration-01`).
The subsequent A03 isolation test suite adds 12 tests and is being
revalidated in its own current full run (see A03 status above).
**Limit for existing v2:** a collision is a fail-closed error, not
automatic slug reassignment. Historical cross-version mapping remains
necessary.

B01 **optional v3 ID design, code-only, NOT enabled** (2026-10-28):
`generate_candidate_arms(..., taxonomy_version="p1-taxonomy-v3")`
now appends a collision-resistant SHA-256 digest of the versioned
canonical 13-dimension fingerprint to a readable slug. Its initial
full 64-hex digest made v3 IDs **too long (up to ~124 characters)** for
downstream P5/P6/P7/P8/P9 `VARCHAR(64)` arm-ID columns, a real
cross-phase defect missed by the earlier P1-only SQLite/PostgreSQL
tests. **Corrected before any v3 activation:** the v3 ID now uses
the first 160 digest bits (40 hex characters) and a truncated readable
slug, with a **hard 64-character maximum**. It remains stable across
independent days, batch sizes, ordering and hidden mood dimensions.
A finite digest is collision-resistant, not mathematically unique.
Both unnamespaced legacy IDs and existing **v2 IDs stay unchanged**;
configured P1 still defaults to v2, and R5 mock outcome keys are
unchanged. No change to C1/C2 wire schema or P2 state.
The first focused command used a nonexistent test path and collected
no tests (job `0aec50457e0af5d6a6ea0f74955dd1ee`).
The corrected P1 focused suite **30 passed** (job
`8fccc3fe3c2745fc71e251aaef927c22`).
Disposable isolated PostgreSQL **3 passed** (job
`dd2783271c6c871e12238d87be209e1f`), testing v2 replay,
fail-closed v2 collision and independent-batch v3 coexistence.
Related P1/C1/C2/C3 unit and contract tests **103 passed**,
Ruff all passed, mypy **464 source files** passed and Git diff-check
passed (job `2386ae982e5931547cdc57bbb2d895c1`).
No full post-change `pytest -m "not integration"`, no isolated
full-chain R5 and no rollout or migration have been performed.

B01 **64-character v3 ID consumer-width correction** (2026-10-28):
the earlier full-digest v3 implementation violated downstream
`VARCHAR(64)` arm-ID column limits. The corrected v3 ID reserves
40 hex characters (160 SHA-256 bits), a shortened readable slug and
the v3 prefix, and never exceeds 64 characters.
New `tests/unit/market/test_arm_id_storage_compatibility.py`
inspects actual P3/P5/P6/P7/P8/P9 SQLAlchemy models to catch
downstream width regressions before enabling any new taxonomy version.
P1 identity, duration and consumer-column focused **10 passed**
(job `10e082a9d241c6b88a1a76e597765b8b`);
**3 disposable isolated PostgreSQL tests passed**
(job `8de6f2bbe11237746f8c34f55207a073`), including v3
collision/batch stability and 64-character persisted IDs. The
consumer-width regression, P1, C1/C2/C3 contracts ran **104 passed**
(job `41234d88ea9b8fce3733e64d9592d485`); Ruff initially
found only the new test module's import order (one error, no test
failures), corrected without changing runtime behavior. Ruff,
mypy **464 source files** and diff-check then **PASSED**
(job `0713e945b4178aedc404223a1e973d9c`).
A further actual PostgreSQL `VARCHAR(64)` storage probe was added
to the throwaway market database: **3 integration cases passed**
(job `8dee3fa18177adab5e783cfbad95660f`, including
insert/readback of two distinct v3 IDs into an enforced 64-char
column), followed by a clean Git diff-check.
The previous v3 tests and docs were updated; no existing v1/v2
mapping or configured default changed. Current full non-integration
and R5 full chain still await independent verification.

B01 is **not fully closed**: no historical v1→v2 arm/score migration
or approval for P2 allocation, no production-scale lineage or
cross-phase full-chain Compose proof. This code did not migrate or
overwrite the configured shared database.

B02 partial improvement: `PipelineOptions`' **default** Temporal activity
start-to-close limit changed from 600s to **3600s**, matching the previously
tested R5 full-chain override; the P6 assemble HTTP request cap increased from
570s to **3300s** to keep 300s headroom for one render. Explicit shorter
activity overrides remain unchanged. This does **not** complete B02: synchronous
P6 rendering, sequential multi-package assembly, worker crash/timeout
recovery and real provider resources still need durable render jobs and full
integration tests. Focused assemble tests: **11 passed**; repository Ruff
and mypy of **463 source files** passed. Latest non-integration regression
on the B01/B02 implementation: **3,027 passed, 1 skipped, 80 integration
tests deselected, 15 warnings** in **297.53 seconds** (exit code 0, job
`d68b313134655345dc37ba73f1dc9751`). Whole-tree
`git diff --check` passed. **R5 full-chain Compose, FFmpeg media and
production-length failure/restart testing were not rerun** in this pass.
The source-based B02 assessment in
`docs/reviews/2026-10-28-b02-render-recovery-assessment.md` confirms
that the current process-local render lock and post-render job insertion
cannot provide durable pre-render ownership or multi-worker fencing.
That assessment defines required PostgreSQL/FFmpeg crash and restart
gates; it is **not an implemented recovery protocol or a passing gate**.
B02 **code-only storage foundation (2026-10-28)** adds atomic
`QUEUED→RUNNING` ownership claims and owner/input/timestamp-fenced
terminal updates on the existing `render_jobs` table, refusing
unverified completed checksums and missing failure reasons.
The independent `queue_render_job` helper additionally validates
a deterministic job ID and SHA256 input identity and rejects changed
identity during an otherwise idempotent queue retry. None of these
helpers are **called by the synchronous P6 render path**; a committed
pre-render queue writer, worker leases/heartbeats, crash reclamation,
blob reconciliation and PostgreSQL/FFmpeg failure gates remain open.
Pre-queue foundation focused suite **43 passed, 11 warnings** (job
`ed0f302b96e736c0e75e75aba025dad1`) and full non-integration
**3,123 passed, 1 skipped, 80 deselected, 16 warnings** in 327.84s
(job `7ed2c2a8d7d5d5eaa7bf77f9ed665f6c`).
After adding queued identity tests the focused P6 suite passed
**45 tests, 11 warnings** (job `98f926540e7d431a321334a59d511bda`).
Whole-repo Ruff, mypy (464 source files) and `git diff --check`
passed after export ordering corrections. The *post-queue-change*
non-integration regression passed **3,125 tests, 1 skipped, 80
deselected, 16 warnings** in 309.07s (job
`2b5bedd83ce47f1ecf34ea31351580a4`).
Further B02 **transaction atomicity fix**: a new outer-rollback test
first failed (1 failed, 10 passed, job
`dfff30a1807deb9de412d645e9328dae`) because the SQLite legacy
transaction mode committed a nested SAVEPOINT independently. The
`queue_render_job` implementation now uses PostgreSQL/SQLite
`ON CONFLICT DO NOTHING ... RETURNING` in the **caller transaction**,
preserving the idempotency and input-conflict checks without an inner
savepoint. Post-fix P6 focused suite passed **46 tests, 11 warnings**
(job `68bd89b5ab9529167b71e363fb6f0d87`), repository Ruff passed,
and mypy passed in **464 source files** (job
`83849ce7aaf5f72cc0c28104f87a700c`).
This proves the SQLite regression; PostgreSQL runtime transactions
and multi-worker failure injection are still unverified. Full
regression *after the rollback fix* **passed 3,126 tests, 1 skipped,
80 deselected, 16 warnings**, exit code 0 (job
`2cb3f52d0694f5045f398b3ae952a1f3`), before the next code change.

B02 follow-up **storage-only stale-work quarantine**:
`quarantine_stale_render_job` atomically moves a RUNNING job older
than an explicit caller-supplied cutoff to QUARANTINED with a
`render_run_stale` reason. It does not reset the job or claim that
the previous FFmpeg process has stopped; the worker's original
fenced completion is denied after quarantine. It cannot overwrite
an already completed job. Two new SQLite tests cover stale owner,
input/cutoff fencing, replay and invalid parameters. Focused P6
storage/API suite: **48 passed, 11 warnings** (job
`1eab11504bcfaf12746c52fb1df56314`). Repository Ruff,
mypy (**464 files**) and `git diff --check` passed (job
`7c9388347863a43ab7832da06cc343e2`).
**Post-change full non-integration** passed **3,128 tests, 1 skipped,
80 deselected, 16 warnings** in 310.04s, exit 0
(job `bbf43ad51d275348e69a7b605ca9011e`).
The opt-in disposable localhost PostgreSQL suite
(`tests/integration/test_media_render_claims_postgres.py`) passed
**4 integration tests in 17.51s**, exit 0
(job `127223819e0de2e75c68fcd5446a3a81`): caller
transaction rollback, concurrent duplicate registration,
two-worker single-claim contention, and mutually exclusive
completion/quarantine. That tests actual PostgreSQL transactional
fencing, **not** a process kill or artifact publication. After the
new integration file, repository Ruff, mypy (**464 files**) and
`git diff --check` passed again (job
`8cf748c6d9879e11c268a0d18499d6ca`).
The quarantine helper is not connected to any recovery supervisor,
has no heartbeat/lease deadline, and cannot reconcile blobs or stop
a still-running FFmpeg process. Same-revision Compose R5 and actual
worker-failure tests remain open.

B02 further **bounded stale-job candidate discovery**: the new read-only
`list_stale_render_job_candidates` returns ordered RUNNING job IDs whose
start time is before an injected cutoff, with a strict 1–500 result limit.
It excludes QUEUED/completed/quarantined rows and does **not** infer
worker death, grant a retry or mutate state. Recovery orchestration must
first verify worker liveness and blob state, then use the fenced
quarantine transition. Post-change P6 storage/API focused suite:
**49 passed, 11 warnings** (job `ab33805d72cfa93be720bb8016b239f6`).
Disposable local PostgreSQL integration **4 passed** with stale-scan
checks added to the two-worker test (job
`f15f8c7ac8728a2898ce2ec496edea3a`). Ruff, mypy (**464 files**),
Git diff checks passed (job `41ece8f3886c5e75e0672dd708094675`).
Full non-integration suite after the stale-scan change **passed
3,129 tests, 1 skipped, 84 integration tests deselected, 16 warnings**
in 320.83s (exit 0, job
`812d587615b7ca7f50fc1f0a0dad0372`, request
`plm-AB-20261028-b02-stale-scan-full-nonintegration-01`).
The scanner remains a **partial storage helper**; no full worker
lease, heartbeat or FFmpeg crash/restart proof exists.

B02 **HTTP/CLI pre-render committed intent and owner fencing** (2026-10-28):
`/media/packages/assemble` and the P6 `assemble` CLI now use
a separately committed DB transaction for the input-manifest-bound
QUEUED intent and another to atomically claim it RUNNING **before rendering or output-blob creation**. The
manifest hash is computed from the renderer's identical metadata input
without additional full recording fetches. On success, the worker
identity, start time and input hash fence its terminal update in the
same transaction as package/C6 persistence. An existing RUNNING or
terminal job with no assembled package returns **409**
(`render_job_reconciliation_required`) instead of rerendering;
a claim quarantined while output is being produced returns **409**
(`render_ownership_lost`) and cannot commit a publishable package.
A pre-render worker exception leaves a durable RUNNING claim for
reconciliation. Direct in-process callers **without** the independent
session factory still use legacy insertion; the HTTP and CLI edges
supply both session and unique worker-token factories.
Focused SQLite P6 storage/API suite **51 tests passed** (job
`9184f4810d03be9293540ca5e570d1a2`), including simulated
interruption and quarantine race; the first full postchange regression
found **one genuine failure**: ambient `uuid4()` below the edge
(3,132 passed, 1 failed; job `c7f082c3be6ef62fd58e74e747c72ba0`).
Worker-token generation was moved to the HTTP/CLI edges, with the
service receiving an injected callback. The corrected focused
P6 + determinism suite **53 passed** (job
`61c04370674a9cf13d655f298675dfb6`). Disposable localhost
PostgreSQL **5 integration tests passed** (job
`e6cf6200fe311ad5325adca4162d7ac9`), including the actual HTTP
assembly/claim/finalization/replay path with a stand-in media toolchain.
Repository Ruff, mypy (**464 files**) and `git diff --check`
passed after all changes (job `4f4c60efb90e976eea293c69a48a7773`).
Latest full non-integration regression **passed 3,133 tests,
1 skipped, 85 integration tests deselected, 16 warnings** in
299.94s, exit 0 (job `64af3a961ed60503c96b5d19a5503ba8`,
request `plm-AB-20261028-b02-http-cli-edge-full-nonintegration-01`).
**Not complete B02:** synchronous HTTP still performs FFmpeg; no
separate worker/heartbeat/lease/reclaim, no blob staging or automatic
reconciliation. The injected exceptions are not evidence of a killed
real FFmpeg process; real multiworker process death, representative
resource load and same-revision Compose R5 runs remain unverified.

B02 **read-only operator render history** follow-up (2026-10-28):
`plm media render-jobs --package-id <exact-id> [--limit 20]`
reads bounded (1–100), ordered P6 `render_jobs` records for one exact
media package. It reports stored status, profile, attempt, start/end and
reason code; it does **not** launch/render/retry/reclaim, modify the DB,
contact blob storage, or imply that a RUNNING worker is alive.
An empty result is **not evidence that no FFmpeg work occurred**.
Changes: `storage/render_claims.py`, P6 CLI and platform CLI wrapper;
new `tests/unit/test_media_render_job_history.py`.
The initial focused run exposed missing top-level command registration
(21 passed, 2 failed, job `7bd2d5dae2a74ddec2470d7adac9ca4d`).
After registering the real `plm media` command, **23 focused tests passed**
(job `075698279eb56133bffb546c169e5ada`).
The initial static check exposed an `__all__` ordering error, then
Ruff, mypy (**464 files**) and `git diff --check` **passed**
(job `02e420653a207d0d54427a77c42264bb`).
**No new full non-integration, disposable PostgreSQL, live FFmpeg, process
restart or Compose gate has been run for this small read-only change**;
the previously observed 3,166-test full pass predates it. B02 durable
render recovery remains **OPEN**. No deployed or shared data was modified.

B02 **worker-identity/termination precondition for stale quarantine**
(2026-10-28): `quarantine_stale_render_job` now **requires**
`expected_worker_id` and `worker_termination_confirmed=True`, and
its atomic SQL transition matches the exact incumbent worker identity,
manifest hash, status and start cutoff. Missing or false termination
confirmation raises without updating a job; a wrong worker identity
cannot quarantine even a sufficiently old job. Age is **never**
treated as termination evidence. The caller's assertion is **not**
independent liveness proof; a future supervisor must obtain verifiable
external termination evidence and reconcile blobs before any retry.
Unit P6/API/runtime-import tests **52 passed** (job
`edce0251124f336d849b801c3921bcf9`), Ruff/mypy (**464 files**)
and diff-check **passed** (job `a6860bba3009f5d7303307bfa3ab9a22`);
disposable isolated PostgreSQL **5 passed** (job
`b108177d47a8eb140d0866b95b9a45eb`), including cross-worker and
completion-vs-quarantine races with simulated termination assertions.
No actual FFmpeg worker was killed or externally verified; no shared
database, package or rendered output was modified. The old **3,166**
non-integration run predates this change; current full suite **not
rerun** to avoid potential contention with user video creation.

B02 **quarantine execution-token fencing** (2026-10-28): the
stale-job quarantine operation now additionally **requires**
`expected_started_at` (timezone-aware) and atomically compares it
against the persisted RUNNING claim. The exact worker **and its
original run-start instant** must match, just like the terminal
completion fence. A same-worker replay with an old run timestamp
cannot quarantine another run. The cutoff alone is not ownership.
Tests cover mismatched run timestamps and naive datetime refusal;
SQLite strips timezone metadata on reads, so the API fixture verifies
its stored instant against the injected UTC clock and passes the
original aware timestamp rather than weakening the storage contract.
The first focused run uncovered that fixture mismatch (**51 passed,
1 failed**, job `6bf3df2c12794c9bcf629d2164f1849d`); after
correction, **52 focused tests passed**, 11 warnings (job
`f72b26d3bfe3deee3108c610e7adfb57`).
Ruff, mypy **464 files**, and Git diff-check **passed** (job
`f6f60202595985718044b044ef3a1a21`).
**5 disposable isolated PostgreSQL cases passed**, 2 warnings
(job `2e842b5e6a284ce167b3dbca3e2f3db2`).
This is not a worker lease or proof of death: the supervisor
must still verify real process termination, reconcile artifacts and
obtain restart/recovery evidence. No live FFmpeg, shared DB write,
deployment, full current-revision unit sweep or full R5 run occurred.

B02 **per-claim render artifact isolation (2026-10-29, PARTIAL)**:
The HTTP/CLI committed owner claim now derives a 64-character SHA256
namespace from the persisted job ID, worker ID, manifest hash and UTC
execution-start instant, using a JSON-encoded unambiguous identity.
Master video/audio, render background and thumbnails are stored beneath
separate `claims/<namespace>/` prefixes; C6 uses the resulting actual
content-addressed URIs/checksums without changing frozen contracts.
Old direct in-process calls without an owner claim preserve legacy paths.
An invalid namespace fails before any render output is written, and the
raw worker token is not exposed in storage URLs. The pure namespace
helper is recomputable from an aware persisted claim identity for
read-only reconciliation; it does not prove worker termination or authorize
artifact deletion/retry. Initial P6 storage/API/render/history bundle
**86 tests passed**, 11 dependency warnings (job
`474072a48d2928966b05b79e30cc3abe`; subsequent Ruff failed on
string-join style). After extracting the reproducible identity helper,
**21 focused tests passed** (34 deselected; job
`a993023e9fd590046cc686b4ee80b616`; Ruff failed on test dict style).
Both style issues were corrected; **2 final focused/API tests passed**, Ruff,
mypy **464 source files** and Git diff-check passed (job
`27c819733e24ae8a2bb19d5d7956b809`). Tests used SQLite and stand-in
media: no actual FFmpeg crash/restart, MinIO artifact reconciliation,
full regression, dedicated PostgreSQL race recheck or R5 run this turn.
B02 remains partial: no autonomous worker, heartbeat, supervised reclaim,
or external proof that old workers cannot publish.

B02 **read-only persisted-claim artifact inventory (2026-10-29, PARTIAL)**:
The new `plm media render-inspect` command (`--job-id`,
`--brand-id`, `--limit 20`) connects an exact persisted render job to its
SHA256 execution namespace and performs a bounded **MinIO LIST-only**
inventory of render and thumbnail object names (1..100 entries).
It never downloads or hashes object bytes, performs writes, deletes,
retries or changes job state. Object-key SHA256 is labeled as a *pin*,
not verified content. If a media package exists, its brand and any
stored namespace must match; without a package, the supplied brand is
explicitly an unverified hint. A missing claim or timezone-ambiguous
stored start prevents listing rather than inferring UTC. Object counts
and truncation are explicit; no list or empty list proves no orphaned
objects, a dead worker or recoverability. Implementation includes
`render_reconciliation.py`, the scoped MinIO metadata reader and both
P6/platform CLI registrations. Initial bundle **29 passed, 1 failed**
for missing platform wrapper (job `d0b541b7b6befd9bfa8aa0d579dbc093`);
after registration **30 tests passed** (job
`7c65c31f08f8adfd8aeded6b473f24e6`, Ruff then reported an
`__all__` ordering issue); corrected Ruff, mypy **465 files**, and
Git diff-check passed (job `57345ddb424518027ad9668630b4c765`).
All listing checks used a fake MinIO and SQLite; no real live MinIO,
actual crash/restart, worker liveness or full-system gate was tested.
B02 remains partial and no production recovery action is authorized.

B02 **supervisor-gated retry intent and fenced attempt-2 execution
(2026-10-29, PARTIAL)**: `queue_reconciled_render_retry` now stages
ONLY a separate QUEUED attempt via an atomic PostgreSQL/SQLite
`INSERT ... SELECT ... ON CONFLICT DO NOTHING`. SQL rechecks the exact
QUARANTINED predecessor, `render_run_stale` reason, owner, original
start, unchanged input SHA256, quarantine finish, absence of any later
attempt, and absence of a persisted package. Attempt IDs are derived
from the package/profile/attempt number; a bounded `max_attempts`
prevents unlimited retries. The primitive requires explicit supervisor
attestations of terminated process, orphan artifact reconciliation and
fresh input verification. **These booleans are not independently checked
or persisted evidence**; no CLI/API exposes automatic recovery, and a
real supervisor with durable proof is still missing. No worker is
launched by queueing the retry.

The internal `assemble_and_persist(..., recovery_job_id=...)` execution
path accepts only an independently staged QUEUED attempt greater than 1,
recomputes its input and render version, atomically claims its original
job ID, then runs the renderer with the matching attempt number. Its
existing worker/input/start completion fence controls package insertion.
Normal public P6 API/CLI calls still use attempt 1; neither receives
an implicit retry flag. A new failure test uncovered that the render
manifest omitted the **playlist concept** even though thumbnails and
metadata depend on it. P6's pre-render and completion hash now include
the supplied concept consistently, blocking changed-presentation replays.

Initial P6 batch after implementation **103 passed, 1 failed** (changed
concept unexpectedly accepted; job `32e35f24fcd10f1257b2e092e52799e9`).
After the systemic fingerprint fix **16 retry tests passed**, with Ruff
import order then corrected (job `cba3f271cd2dfb386a7f0a888a89a467`).
Expanded batch **106 passed**; Ruff passed, mypy identified two typing
errors (job `2f75fa6b64363600b2aa6d7615a4587b`). After correcting
types, **18 retry-focused tests**, Ruff, mypy **465 sources**, and
Git diff check passed (job `ca74387187a099eff83f7ad576bf430f`).
All test evidence uses fake renderer/SQLite. PostgreSQL race evidence,
real FFmpeg kill/restart, external worker termination/rights evidence,
real MinIO reconciliation, full R5 and complete A/B gates are **not
verified**. No shared database, real output or deployment was modified.

B02 **recovery-claim and completion fence hardening (2026-10-29,
PARTIAL)**: Found an unintended alternate path where the generic
`queue_render_job` accepted a caller-constructed attempt 2 without
prior quarantine or supervisor reconciliation. It now accepts only
attempt 1; the dedicated atomic reconciled retry function remains the
only normal way to stage later attempts. `claim_queued_render_job` now
atomically rechecks that no already persisted package or later attempt
exists, and requires an exact adjacent QUARANTINED `render_run_stale`
predecessor with matching input hash and render version for attempts
above 1. `finish_claimed_render_job` also rejects a late finish if a
package or later attempt appeared after the worker claimed the job.
This guards the worker's transition as well as retry creation; legacy
or manually inserted malformed rows cannot become authority just by
having QUEUED status. The history fixture continues to include legacy
rows through explicit test-only insertion, not runtime queue bypass.

**81 focused P6/API/claims/retry/history tests passed**, 11 third-party
warnings; changed-file Ruff, mypy **465 source files** and
`git diff --check` passed (job `6711db43c5387cc91fe3f7761461371d`).
No real PG race, live MinIO, FFmpeg termination, full nonintegration
sweep or R5 run occurred; the supervisor's termination and rights
attestations are still unverified inputs, so B02 is not closed.

B03 **code-only partial fix** (2026-10-28): removed P4's invented
`actual_cost=cost_credits * 0.05` conversion. New generation attempts and
provider cost records retain credits separately and set *actual currency
amount, estimate, currency and receipt reference* to **NULL**, marked
`money_basis='unpriced'`. Domain validation rejects numerical zero or any
currency/receipt fields incorrectly marked unpriced; provider-reported money
requires a priced currency and source receipt reference. Historical amounts
are neither deleted nor treated as verified: the separately staged phase-owned
`0040_p4_unpriced_costs` Alembic revision relaxes only their NULL constraints,
adds provenance fields, labels existing records `legacy_unverified`, and
removes the backfill-only default. Guarded downgrade refuses losing new NULL
or provenance values. Two database CHECK constraints now enforce the
unpriced/provider-reported invariants against direct SQL writes too.
**Code only: the connected local development DB remains at 0039; 0040 has
NOT been applied to it.** A **real isolated PostgreSQL 16 source-copy
restore and 0039→0040 migration PASSED**, preserving all **341 generation
attempts and 341 provider cost rows** by full old-column hashes; R5
snapshots/security remained unchanged, all historical costs marked
`legacy_unverified`, schema/nullability/defaults correct, re-upgrade
idempotent. Fresh read-only source check reconfirmed **0039 and 341+341**;
test container cleaned (job `149d222c7d5b32885970d8b1a4ac0228`,
exit 0). Migration/guard focused suite **75 passed, 1 skipped**;
DB constraint/direct-write tests **21 passed**. Final
post-constraint non-integration regression **3,044 passed, 1 skipped,
80 integration tests deselected, 15 warnings** (exit 0, 292.89s,
job `bfc7d9569b015a0a803b2cfeb71d96aa`). Ruff passed; mypy
passed on **463 source files**. Compose R5 full chain and real
production-length crash/restart tests were not rerun. Future DB deployment
still requires retained backup, independent restore and separate
environment-specific approval. The separately documented later B04
code-only change removes the previously hardcoded Suno/ElevenLabs credit
figures; independent real rate/receipt reconciliation, Decimal/NUMERIC
settlement and enforced plan-level credit caps remain open. See
`docs/adr/0038-p4-unpriced-provider-costs.md` and
`docs/reviews/2026-10-28-p4-0040-isolated-postgresql-rehearsal.md`.

B04 **code-only partial fix (2026-10-28):** Suno success no longer invents
`cost_credits=10`; ElevenLabs configured tokens no longer imply an
invented 1,000-credit quota. A real provider with no referenced credit
usage records `None` and `credit_basis='unknown'`, never a fabricated 0.
Simulated mock usage is separately tagged `mock_simulated`; historical
amounts are labelled `legacy_unverified`. Observability counts unknown
attempts separately and does not add simulated usage to reported spend.
The new **0041_p4_credit_provenance** code-only revision follows 0040,
relaxes unknown credit column nullability, and adds credit basis/receipt
fields and DB CHECK constraints; neither revision has been applied to
the connected local DB. An independently restored disposable PostgreSQL
16 copy **successfully upgraded 0039→0040→0041**, preserved 341 P4
attempts + 341 provider cost rows byte-for-byte by old-column hashes,
the original R5 fingerprints, backfill labels, constraints, and
idempotence (job `f355a0b4ef0d12cce5174afb9344b647`, exit 0).
Fresh read-only source verification returned **0039, 341+341**.
Focused checks: **96 passed, 1 skipped**; Ruff and mypy
**463 source files** passed. Full non-integration regression on the new
0041 code: **3,056 passed, 1 skipped, 80 integration tests deselected,
15 warnings** (exit 0, **296.81s**, job
`2299a4a8ac57c3697d91ef174fadfb4d`). Full Compose R5, FFmpeg
production-length and provider-account tests were not rerun.
This does **not** establish authentic paid-provider usage receipts,
price/account rights, plan budget caps, or ranked candidate selection.
B03/B04 remain **PARTIAL**; schema rollout requires separate approval,
retained backup and restoration evidence. See
`docs/adr/0039-p4-credit-unknown-provenance.md`.

B03 **additional narrow plan-limit hard stop (2026-10-28)**: on
the *real-provider* P4 path, an explicit `budget_limit == 0` or
`credit_limit == 0` now denies generation **before** reserving
the shared ledger budget or invoking the provider, with reason
`plan_money_budget_exhausted` / `plan_credit_budget_exhausted`.
`ExternalWriteGuard` records caller preflight refusals without allowing
them to override normal policies, and P4 rejects NaN/infinite plan limits.
Mocks remain simulated. Focused engine/guard/domain gate: **53 passed**
(first pass), **52 passed** after six nonfinite-value tests; Ruff PASS,
mypy PASS for **463 source files**. Evidence:
`docs/reviews/2026-10-28-p4-zero-plan-limit-safety.md`.
**This is NOT positive per-plan cap enforcement**: cumulative money/credit
headroom under concurrency, actual provider receipts, budget currency,
idempotent plan-level reservations and full Compose regression remain open.
The shared development DB and previously code-only 0040/0041 revisions
were not touched in this step. The updated full non-integration suite
completed with **3,066 passed, 1 skipped, 80 integration tests excluded,
15 warnings** (296.54s, exit 0, job
`95d51b52c938093ad31e415a728d3274`).
Full R5 Compose integration remains untested in this work package.

B03 follow-up **paid-credit history safety brake (2026-10-28)**:
P4 now examines both persisted and same-run SUCCEEDED attempts before
another real-provider call. If earlier paid credit usage lacks a
provider-referenced amount, the next write receives
`plan_credit_usage_reconciliation_required`. If verified reported
credits already sum to or exceed the positive plan credit ceiling,
the next write receives `plan_credit_budget_exhausted`. The checks
run **before** the shared ledger reserve and provider call, using
decimal arithmetic; a replay preserves prior successes without a
second paid call. Mock simulation is not charged to paid credit
headroom. Fake-provider in-process tests exercised both refusal
paths and replay: **43 focused passed, 2 warnings** (job
`a22ddd9d0550fdaae79d16e8a2d685c5`). Ruff, mypy
(**464 files**) and diff check passed (job
`9fa27ba3c13a8a0f5ee95debc65ed286`). Latest full
non-integration regression **passed 3,131 tests, 1 skipped,
84 integration tests deselected, 16 warnings** (exit 0, 296.65s;
job `6f0a43d8ada5f489b85042d12603e335`, request
`plm-AB-20261028-b03-prior-credit-usage-nonintegration-01`).
**Still open:** no verified per-call credit estimate/reservation,
atomic cross-worker plan-level ceiling, provider reconciliation,
or positive currency-aware plan money ceiling. A positive residual
allowance does NOT prove the next paid call fits. This partial guard
must not be presented as complete B03 protection.

B03 **persisted unknown live-attempt replay refusal (2026-10-28)**:
Before considering another REAL-provider write, P4 now treats any
stored SUBMITTED, PROCESSING or CANCELLED attempt as unresolved even
when its provider_job_id is blank. A stored SUCCEEDED attempt without
its candidate, including one with a missing provider job ID, and a
FAILED attempt with an accepted provider job ID also require explicit
reconciliation. A clearly rejected FAILED attempt with no job ID
remains eligible for its normal guarded retry; MOCK simulation
retains its established behavior. This closes an unsafe case where
incomplete persisted attempts were previously skipped merely because
no provider job ID was recorded. **It does not assume a missing ID
means a free or refunded call.**
In-process fake-provider/SQLite focused run **44 passed, 2 library
warnings** (job `292b099a3de34f9380043358db5ac4df`), including
six unresolved status/ID combinations, same-session replay and
committed SUBMITTED record read through a newly created session and
guard. Ruff check passed, mypy **464 files** passed, and Git
diff-check passed in that job. The earlier run before the durable
replay test had **43 passed** (job
`c8b8cbc7cc0b34f3d72bffa1115c470f`). Wider P4 unit,
C3/C4 contract tests **246 passed, 3 library warnings**
(job `37c499df1b9e418d13f1bdbaeab6e1b9`; this wider run
predates only the additional durable-replay test).
**Not yet verified:** full repo regression after this edit, live
paid provider receipts, real process crash/restart, per-plan positive
money/credit reservations and currency-aware concurrency caps. No
real provider, shared DB write, rendering or deployment performed.

B03 **accepted-paid-but-audio-missing replay fence** (2026-10-28):
The guard correctly committed a REAL provider call returning SUCCEEDED
even if it produced no audio; however the saved failure had a blank
provider_job_id and lost its accepted status. A later run with a fresh
guard could treat it as a no-charge FAILED response and pay again.
P4 now durably writes failure_code
`PROVIDER_ACCEPTED_OUTPUT_MISSING` for that case, and refuses further
REAL writes on replay until reconciliation, even with no provider
job ID. A FAILED attempt without a job ID but with provider-reported
or legacy-unverified money/credit evidence also requires reconciliation;
billing evidence never becomes proof of no charge merely because a
provider job ID was absent. Confirmed pre-job refusals remain retryable
under the normal global write guard. Focused fake-provider SQLite
tests **50 passed, 2 third-party warnings**, and Ruff, mypy **464
files** and Git diff check all passed (job
`6df7f98a4ffd5cdd13a6c3b40efd9761`). Two test cases prove
accepted/no-audio output with blank/present job IDs cannot be paid
again after a committed DB session and a newly constructed guard; four
cases cover explicit and legacy monetary/credit evidence on FAILED
attempts without a job ID. A prior focused subset before those four
billing-evidence tests passed **46** (job
`de0beaac4901555525757ec5a55d12cf`).
Wider P4 unit + C3/C4 contract tests on the updated code
**253 passed, 3 third-party warnings**
(job `93db22a1e40bca178253ed4684eae574`).
**Not closed:** the global ops ledger still does not atomically
enforce positive P4 per-plan money and credit ceilings, and no
live-provider invoice, full-repository regression, or isolated R5
end-to-end proof exists for this code revision. No connected DB,
paid provider, render, deploy or commit was touched.

B03 **explicit no-charge attestation instead of blank-job inference**
(2026-10-28): a FAILED REAL response with an empty provider job ID
is **not** proof that the supplier did not accept or bill the job.
The old automatic release was unsafe on lost responses/timeouts.
The adapter's optional `GenerationResponse.confirmed_unsubmitted`
now defaults to False; its validation forbids claiming a confirmed
pre-submission refusal together with successful status, job ID,
audio, credit amount or receipt. When the signal is explicitly true,
the guard can release its preflight-funded reservation and persists
`PROVIDER_CONFIRMED_NO_SUBMISSION` for an ordinary guarded retry.
Otherwise an ambiguous REAL failure preserves the **RESERVED**
ops-ledger row, stores `PROVIDER_OUTCOME_UNKNOWN`, and halts all new
REAL attempts/replays pending charge reconciliation. Old FAILED rows
without the new safe marker are also held as unresolved rather than
grandfathered as free. Empty-key Suno/ElevenLabs and hermetic
ElevenLabs-disabled submissions assert the safe signal because their
code never sends a request; the latter no longer fabricates a job ID.
No change to MOCK response/retry semantics.

**73 focused tests passed**, 2 dependency warnings, covering
adapter, response model validation, durable ledger reservation
and crash/replay with an ambiguous no-ID supplier response.
Ruff, mypy **464 files** and diff-check all passed (job
`23fb2e78e4abff5075f0f9619c00c7eb`).
Wider P4 unit and C3/C4 contract tests **260 passed, 3
third-party warnings** (job
`a9b83a13bb72be59f1018f150cdafe86`).
This is a safer refusal policy, not an automated refund or
independent provider-billing reconciliation mechanism. Per-plan
positive currency/credit ceilings and multi-worker atomic plans are
still open; real-provider receipts and a post-change full R5 gate
were not tested. No connected DB, live payment, render or deploy.

B04/B03 **Suno provider-receipt identity correction** (2026-10-28):
The REAL Suno adapter previously fabricated a provider job ID
(`suno_err_<track-spec>`) on any SunoClientError, including a
submission timeout that may or may not have created a charge.
That identifier is **not evidence of a provider-accepted job** and
must never be supplied as provenance. The adapter now returns an
empty ID if submission failed or returned no clip ID, explicitly
keeps `confirmed_unsubmitted=False`, and retains the genuine
provider clip ID only when the submit call actually returned it.
Failures during later polling/downloading retain that real clip ID
instead of inventing another one. The P4 guard's prior B03
unknown-outcome rules keep the external reservation held and
refuse future paid calls until reconciliation.
Four isolated fake-client failure-stage regressions (submit exception,
empty receipt, polling exception, download exception) and one
Suno-adapter/P4-guard/committed DB replay test were added.
Focused provider/guard/engine/runtime **67 passed**, 2 dependency
warnings, Ruff, mypy **464 files** and diff-check passed (job
`bd17d4b4ae8bf98fdd35018459d6fe0c`).
Wider P4 unit + C3/C4 contract gate **265 passed**, 3 dependency
warnings (job `d947d36a2a0cb499a08721d118de0023`).
Actual Suno requests, billing receipt verification, full repository
non-integration and R5 live service chains remain **unverified**;
no paid request, shared DB write, render or deployment occurred.

B04/P5 **Suno generated-lyrics and model-version provenance correction**
(2026-10-28): the REAL Suno adapter had been using the *prompt*
as the reported lyrics for a vocal track, even though its
client returned no generated lyric transcript. This could make P5
screen the prompt while the words actually sung were unexamined.
The adapter now returns `lyrics_text=None` for both vocal and
instrumental output; P5's existing detector treats missing
lyrics on a vocal as `UNKNOWN` (instrumental `NOT_APPLICABLE`).
It also no longer substitutes the requested model ID when the
provider's actual model name is blank: `model_version=None` is
kept so P28/P5 provenance remains incomplete rather than invented.
Adapter tests cover instrumental, vocal, and blank model-report
cases. P5 lyric-detector tests are included in the focused gate.
**33 targeted tests passed**, Ruff and mypy **464 files** passed,
diff-check passed (job `f325301df42ff759914509b50bb9a818`);
wider P4 unit + C3/C4 contract + P5 lyrics tests **272 passed**,
3 third-party warnings (job
`480f0b13a06e0c3c2c25f8bc48129d73`).
After adding a direct P5 regression asserting that `lyrics_text=None`
on a vocal yields `UNKNOWN / vocal_track_without_lyrics_text`,
the targeted provider + lyric-detector suite **27 passed**, and
Ruff, mypy **464 files**, staged/unstaged diff checks passed
(job `78fbb4e008cf375a38acb9889a990e61`; staged paths 0).
No real generated-lyric retrieval is implemented or verified; vocal
tracks with no transcript must remain blocked from claiming
complete lyric screening. No live Suno call, shared DB write,
real render or deployed release was performed.

B04 **accepted paid audio without verified provider job identity**
(2026-10-28): P4 previously classified a REAL provider response as
SUCCEEDED if audio existed, even with `provider_job_id=""`. It could
then create a GeneratedCandidate, audio artifact and rights snapshot
without verifiable paid-provider origin. The engine now rejects such
real output (including whitespace-only job IDs), writes a FAILED
attempt with `PROVIDER_ACCEPTED_JOB_ID_MISSING` and empty persisted
provider job ID, and marks `provider_job_reconciliation_required`.
The already-accepted paid reservation remains committed, **not
refunded or treated as a no-charge failure**. No candidate, artifact
or rights snapshot is created from that response; after a committed
session, a fresh write guard will not fund another call. MOCK
simulations retain their established behaviour. Two focused missing
and whitespace job-ID cases are part of **49 passed**, 2 dependency
warnings; Ruff, mypy **464 source files** and diff-check passed (job
`85ace36b4204feb56537322f9f94ccdc`).
Wider P4 unit + C3/C4 contract suite **269 passed**,
3 third-party deprecation warnings (job
`ca5b0a4de0e0bc19fd6f5337e12b3a51`).
This only protects the returned identity field. It does not
independently validate an ID with the supplier or prove commercial
rights; no real provider/account or full R5 run was exercised.

B03/B04 **historical REAL success missing provider job ID replay fence**
(2026-10-29): previously persisted SUCCEEDED P4 attempts could have
an already-created candidate but a blank/whitespace provider job ID.
The replay check treated that candidate as sufficient evidence and
could fund another live generation. The engine now treats this
historical state as unresolved and refuses the entire plan before
any guard reservation or provider call, without changing MOCK
simulation. Two tests reproduced the unintended extra paid attempt
before the fix (2 failed, job `c1217804a6a9dba14d977cfadf68b490`),
then passed after it (45 P4 guard tests passed, job
`b2ba49f37f951f30954ba7dc656552d9`). Wider P4/C3/C4 suite
**271 passed, 3 dependency warnings** (job
`2bdabd9094351732bdd9b1ccd79379e7`); Ruff, mypy **464 files**
and `git diff --check` passed (job
`398f630c096974c31029f5fbfa8f2180`). These are SQLite/fake-provider
checks, not supplier receipt verification, real charges, full repository
regression, or isolated R5. B03/B04 remain partial.

B04 **C4 export and rights-detail historical job-ID cross-gate**
(2026-10-29): a historical SUCCEEDED attempt with artifact and rights
but a blank/whitespace `provider_job_id` could still be exported to P5
and shown by the candidate license endpoint. Two C4 and two rights-
detail tests each reproduced the unsafe path before their fixes
(jobs `b0fa00c8884a2243cc035995a07e6a52` and
`b3589f9283636b3b3db7bb5e6c31b442`). C4 now raises
`provider_job_id_missing` before building a transfer event, and
candidate detail withholds a rights snapshot for this invalid origin.
No stored candidate, invoice or rights row is modified. C4-only **45
passed** (job `9f4205acbc0012941eebfebc7c8a3a45`), and post-fix
P4/C3/C4 **275 passed**, 3 dependency warnings (job
`5885ae525ec0a670040399faa28cb4bc`); Ruff, mypy **464 source
files**, and diff-check passed (job `cff578a05ef1651c4f1281c9704aa410`).
A nonempty identifier still requires external supplier verification;
no real provider, production DB, full repository or R5 chain was tested.

B03 **atomic per-plan cap prerequisite assessment** (2026-10-28):
`docs/reviews/2026-10-28-b03-plan-budget-atomicity-boundary.md`
records the actual schema and contract gap. C3 v2.0 and P4 store
`budget_limit` as a float **without currency**, while the
global/capability ops ledger has explicit-currency Money but no
`plan_id` binding. Treating the C3 float as USD or parsing a
plan ID from the idempotency key would be unsafe. A versioned
contract/ADR, durable scoped reservation, stable multiworker lock
order, native-unit credit estimate and disposable PostgreSQL race
gate are required before live positive plan ceilings can be
claimed. This is **design only**, not implementation, migration,
approval or closure of B03.

B04/B05 **candidate-specific rights endpoint correction (2026-10-28)**:
`GET /music/candidates/{candidate_id}/license` previously returned the
rights snapshot belonging to the plan's **first** generation attempt, even
for a different candidate. It now resolves the candidate's exact persisted
`source_attempt_id` via the candidate detail accessor, requires that the
attempt belongs to that plan, and returns 404 for legacy, dangling or
rights-less provenance instead of borrowing another attempt's license.
The bank accessor also refuses snapshots for nonexistent/out-of-plan attempts.
A multi-candidate HTTP regression covers both distinct attempt IDs and
missing link/snapshot cases: **5 focused P4 API/bank tests passed, 2
third-party deprecation warnings** (job `61fa2f5b18f0c6bbbaaa7629b7edc266`).
Ruff passed for the three changed Python files, mypy passed on 463 source
files, and `git diff --check` passed. All edits were reread; syntax
validation succeeded. The 3,066-test run above started **before** this
rights fix. A subsequent complete non-integration run **after that fix**
passed **3,067 tests, 1 skipped, 80 integration deselected, 15 warnings**
(294.95s, exit 0, job `9764244398fb5044e178b809ae7918fa`).

B04/B05 **C4 provenance consistency follow-up (2026-10-28)**:
the P4 C4 export builder now refuses candidates whose track/experiment
identity differs from their persisted plan or C3 spec, whose producing
attempt belongs to another plan, did not succeed, or has a mismatched
provider/model. It also refuses a rights snapshot with an inconsistent
attempt/provider identity. The candidate detail/license accessor withholds
unverified rights for those inconsistencies. A focused HTTP regression
corrupts each recorded join in turn, verifies **409 C4 refusal** and
**404 license lookup**, restores it and proves normal export still works.
The relevant P4 API/bank/acceptance suites passed **16 tests** with
2 third-party deprecation warnings (job `c9b2d153df391286d4a05cd9a217adb6`);
changed Python files passed syntax validation, Ruff, and mypy
(**463 source files**); `git diff --check` passed. These are *code-only*
controls: no database migration, live provider call or production release
was performed. The 3,067-test run preceded this follow-up and is not its
full post-change regression. Real provider commercial-use entitlement,
P5 similarity clearance, ranked candidate selection and full Compose
chain remain unverified. A subsequent non-integration regression **after**
the C4 provenance checks (before the later immutable-plan change below)
passed **3,068 tests, 1 skipped, 80 integration deselected, 15 warnings**
(294.63s, exit 0, job `32c4fe0bf5e98b58a6a3d40a64d7f332`).

B03/B04 **immutable P4 plan/C3 execution binding (2026-10-28)**:
the generation engine now rejects a replacement C3 brief under an existing
plan before updating status, reserving shared budget or calling any provider.
It also checks a persisted plan's identity, target, attempt bounds, money/
credit limits and policy version against the supplied plan, and refuses
injected or changed creative variants on existing plans. The HTTP execute
route answers **409 `generation_plan_spec_mismatch`** for these contradictions.
Normal plan execution/replay remains supported. In-process engine, API,
acceptance and C4 contract focused tests passed **75** (first run) and
**65** (after persisted-variant tightening), both with 2 third-party
deprecation warnings; jobs `02a8d27dc772761d69e2c6d948e61814` and
`20f974c9596927a5bbc77a8a140e8221`. Whole-repo Ruff passed; mypy
passed on **463 files**, and `git diff --check` passed. All changed
Python files passed syntax checks and were reread after editing.
This protects the recorded plan's integrity; it does **not** implement
concurrent positive per-plan spend/credit ceilings or verify real provider
receipts. No shared DB schema was applied, no paid provider called, and
no production media/backfill changed. The complete non-integration
regression **after the immutable-plan binding** passed **3,070 tests,
1 skipped, 80 integration deselected, 15 warnings** (297.59s, exit 0,
job `4a6c9872ccc99950f8d24eae52f3277b`).

B03/B04 **duplicate deterministic plan ID / complete variant retry safety
(2026-10-28)**: `POST /music/plans` now reuses the existing record, including
its current status and persisted variants, when called again **without**
an idempotency key but with the same immutable C3 brief and computed
plan policy. A different brief, target, budget or persisted creative
variant now returns **409 `generation_plan_identity_conflict`**, rather
than exposing a raw UNIQUE constraint failure or rewriting the plan.
A nested savepoint permits reading the winning plan after a racing
unique-ID insertion; actual parallel PostgreSQL requests have **not**
been independently load-tested. The engine also refuses partial variant
sets for an existing plan before status updates or provider calls.
Focused engine/API tests passed **23** and, after the complete variant
set check, API/engine/C4 contract tests passed **66**, each with two
third-party deprecation warnings (jobs
`61602db6d9fcc3f747cd79114826343e`,
`24e50c878921f56405b7884a9e8f6c32`).
Whole-repository Ruff passed; mypy passed **463 source files**;
`git diff --check` and Python syntax checks passed. These are
SQLite/mock-backed code checks, not real billing, external-provider,
shared-DB rollout or real multiworker race evidence. The 3,070-test
regression above started before this newer duplicate-plan change.
The post-duplicate-plan full non-integration run then passed **3,071
tests, 1 skipped, 80 integration deselected, 15 warnings** (295.62s,
exit 0, job `244df4d851350763e1ccf625c2b1e541`).

B04 **P3 provider allowlist and bounded candidate count (2026-10-28)**:
P4 previously omitted C3 `allowed_providers` checks before REAL paid
calls, and generated at most 3 creative variants even when P3 requested
4 or more candidates. REAL calls to an excluded provider now fail
closed via the shared guard with `plan_provider_not_allowed` *before*
reservation or provider invocation. Explicit MOCK execution remains
`SIMULATE`, does not reserve paid funds and records mock provenance,
including when P3 declares only real providers; simulated providers
are not evidence of a real provider's commercial eligibility.
The first overly broad REAL+MOCK refusal passed 69 focused tests but
**broke** the mock-first end-to-end test: the full run (job
`c9f52ba639189c5aae359ee3ed720c8f`) failed **1 test**,
with 3,073 passed, 1 skipped, 80 integration deselected. The
mock/real distinction was corrected and that exact E2E case passed
within a **36-test** focused suite (job
`b56b3503cf1ebe41fdbc82139486e6e2`). Never count the failed
full regression as a PASS.
P4 now builds distinct deterministic variants for 4–10 requested
candidates and refuses 11+ with typed HTTP 422
`generation_plan_capacity_exceeded`, rather than silently producing
fewer than requested. Existing v1–v3 plans and prompt interpretations
remain unchanged. This is bounded planning support, not a provider
quota or real rights/quality confirmation. A new real P4 engine
test verifies **four distinct persisted mock candidates and replay
without paying/generating twice**. Focused P4 planner/engine/API
and mock-first E2E tests **37 passed, 3 warnings** (job
`0e156f6472f21acab0a9d6f925fe35fc`); Ruff whole repository
PASS, mypy **463 source files PASS**, Python syntax valid and
`git diff --check` PASS. The complete post-capacity/mock-compat
non-integration run passed **3,080 tests, 1 skipped, 80 integration
deselected, 16 warnings** (290.49s, exit 0,
job `1d6acf6383961307c68f2429cca9a343`).

B04 **truthful P4 candidate target completion (2026-10-28)**:
a P4 plan previously transitioned to COMPLETED whenever just one
candidate succeeded, even if its immutable C3 candidate_target asked
for more. It now only completes if the number of candidate artifacts
actually produced/replayed meets the target; otherwise it records
FAILED with `candidate_target_not_met: actual/target` while preserving
any successful candidates and historical attempts. Re-executing
a shortfall does not repeat completed attempts. A new engine test
and HTTP API regression prove one of two candidates does **not**
make a plan appear complete: **40 focused tests passed, 3 warnings**
(job `8a1b79cd2a8abc06886ee23b91673f90`). Whole-repository
Ruff PASS, mypy PASS for **463 source files**, and
`git diff --check` PASS. The HTTP endpoint still returns its
normal 200 response with `status: failed` and any preserved candidates.
The full post-shortfall non-integration regression passed **3,082 tests,
1 skipped, 80 integration deselected, 16 warnings** (315.79s, exit 0,
job `2d3224747d3779fdee2175dfe488e2f8`).

B04 **pipeline P4 failure handoff refused (2026-10-28)**:
the HTTP generate activity previously ignored a P4 execution's
`status`, so a plan that returned HTTP 200 with `status: failed`
and one partial candidate was exported to P5 as though its required
generation was finished. The HTTP activity now requires each P4
execution to report `completed` before collecting and exporting
candidates. `failed` becomes a non-retryable creative rejection,
and missing/other statuses become an explicit validation failure.
C4 exports are staged until **all** P4 plans report completion:
a late failure means **zero** exports in that activity. P4 still
retains its previous candidates, provider calls and paid-spend ledger
evidence; the pipeline must not mislabel the effects as absent.
A 61-test P4 API/pipeline HTTP/QA/assemble focused suite passed
(3 dependency deprecation warnings, job
`66fc288c80583ade8361edfc499100e2`), including late
failure, missing status, unexpected status, and two successful
plans with export ordering. Whole-repo Ruff PASS; mypy PASS on
463 source files; Python syntax checks and `git diff --check`
PASS. The complete post-pipeline-change non-integration suite passed
**3,086 tests, 1 skipped, 80 integration deselected, 16 warnings**
(299.38s, exit 0, job `0d04aeeea087deea656b575c4c1e0af4`).

B04 **standalone C4 export cannot bypass P4 completion (2026-10-28)**:
the direct candidate export endpoint and `build_candidate_ready_event`
previously checked candidate/attempt/rights/artifact provenance but
not the producing generation plan's terminal status. A partial or
cancelled plan therefore still allowed its individual candidate to
be exported to P5 by direct API request, bypassing the pipeline
fail-closed check. The C4 event producer now requires the persisted
plan to be `completed`; `planned`, `running`, `failed`, and
`cancelled` yield typed 409 `generation_plan_not_completed`,
without transitioning the candidate to exported. The normal completed
C4 and rights event path is unaffected. API/C4 contract/generation
acceptance/guard **78 focused tests passed** (3 warnings, job
`7bb2dea9e00d59ce967d43691cbb47b8`);
whole-repo Ruff PASS; mypy **463 files PASS**; Python syntax and
`git diff --check` PASS. The post-direct-export whole non-integration
regression passed **3,086 tests, 1 skipped, 80 integration deselected,
16 warnings** in 293.73s (exit 0, job
`13df595f6b7ea66fe587e67de23f0219`).

B04 **per-plan candidate answer validation in the P4→P5 HTTP pipeline
(2026-10-28)**: P4's `completed` status can no longer hide a plan with
zero candidates behind a different successful plan. Its candidate
collection must be a list; each candidate must carry a nonempty string
ID unique within the generation step. Missing, malformed or duplicate
IDs abort all pending C4 exports. Seven adversarial late-plan cases
were added to the pipeline suite; **33 related tests passed**
(job `6f0e6bb3c10586c5ccfbef14b6742fe2`); whole-tree Ruff passed,
mypy passed for **463 files**, Python syntax and `git diff --check`
passed. The post-answer-validation full non-integration suite passed
**3,093 tests, 1 skipped, 80 integration deselected, 16 warnings**
in 305.06s (exit 0, job `77bbc090a0cbaa60a334ad834627cae2`).
Compose R5 still requires a fresh run.

B03 **C3 nonfinite budget rejection (2026-10-28)**: the shared
`TrackSpecPayload.budget_limit` and P4's `AdaptedTrackSpec`
now reject NaN, positive infinity and negative infinity, in addition to
negative budgets. The formerly accepted positive infinity could cause a
later `GenerationPlan` model error rather than an early typed C3 refusal.
No C3 field or version changes; ADR-0040 records the safety narrowing.
P3 producer/P4 consumer contract and P4 API focused tests:
**61 passed, 3 warnings** (job `d0b0907bc9f66876af541283b2e07fb8`);
Ruff whole-tree passed; mypy passed on **463 files**; Python syntax
and `git diff --check` passed. Post-C3-change full
non-integration regression **3,096 passed, 1 skipped, 80 integration
deselected, 16 warnings** in 302.54s (exit 0, job
`c1a3dc8e2baca041ebb3e564a2d534c8`). Compose R5 remains pending.

B03 **P4 missing/malformed external-write receipts (2026-10-28)**:
the HTTP pipeline previously treated an explicit empty
`external_writes: []` as zero reported writes and silently discarded
non-dictionary list entries. It now appends an `unreported` synthetic
receipt whenever P4 omits receipts, returns an empty list, or provides
any malformed/incomplete receipt. Valid reported entries are preserved;
the platform cost reader already treats `unreported` as UNKNOWN, not
simulated zero. Seven missing/malformed/mixed-receipt cases were added;
**72 focused HTTP, platform-cost and R5 pipeline tests passed**
(job `0837f41f11dbb43fc8ae3d94e38767b6`). Whole-repo Ruff,
mypy **463 files**, Python syntax and `git diff --check` passed.
Post-receipt-change full non-integration regression **3,103 passed, 1 skipped, 80 integration deselected, 16 warnings** in 293.17s (exit 0, job `0b004feaffa8bb214c1a97f806ed8620`). Compose R5 remains pending. This does not
verify that a claimed complete receipt list includes every real
provider attempt. Positive per-plan cumulative spend/credits,
currency pricing, real receipts and concurrent PostgreSQL reservations
remain unresolved. In particular, P4 planner's derived credit allowance
is not evidence of a verified provider credit budget. No development
DB migration, paid provider write or production release was performed.

B03 **live provider accepted-job replay safety (2026-10-28)**:
a REAL P4 adapter can return a failed response carrying a provider
job ID, which may already have consumed funds even though no candidate
exists. The engine now stops additional paid variants/retries after
that response, and on re-execution refuses to call the live provider
when an earlier plan attempt has a provider job ID but no completed,
durably linked candidate. It returns a FAILED plan and explicit
`provider_job_reconciliation_required` error without claiming the
original write did not occur. Mock simulation retry behaviour is
unchanged. A saved succeeded attempt missing its candidate is also
treated as unresolved. The generation guard/API suite passes
**32 focused tests, 3 warnings** (job
`85336906b7759ef2750a490cb5c17b4f`); whole-repository Ruff,
mypy (463 files), Python syntax and `git diff --check` passed.
The full post-change non-integration suite passed **3,105 tests,
1 skipped, 80 integration deselected, 16 warnings** in 314.57s
(job `0584afe64837fa0a10cb42de72008712`). Compose R5 was not rerun.
**This does not yet
cover a process death before any attempt record is persisted, two
workers concurrently using a plan, an external reconciliation process
or positive plan-level monetary/credit budgets.** No live provider
writes, production migrations, or deploys were performed.

B03 **P4 attempt-count to guard-receipt reconciliation (2026-10-28)**:
the P4 HTTP execute response reports `attempts_count` and a list of
`external_writes` independently. Pipeline generation now de-duplicates
repeated guard receipt keys and checks that the number of distinct
receipts is at least the reported attempt count. A shortfall, a
duplicate, or an invalid count causes an additional `unreported`
receipt. The pipeline's outcome cost gate therefore retains UNKNOWN
rather than treating the partial list as all spending. Five focused
normal and adversarial cases were added; the pipeline/cost suites
pass **77 tests** (job `611c1781d0c70930989465ab752500b6`).
Ruff, mypy (463 source files), Python syntax and `git diff --check`
also passed. The run started after the receipt change finished:
**3,110 passed, 1 skipped, 80 integration deselected, 16 warnings**
in 326.78s (job `54177e94447b78b76882fe2f592c1a25`).
It overlapped later budget-guard edits, so it is **not** a completed
full-regression gate for those later changes. Compose R5 was not rerun.
Exact monetary costs and omitted provider calls still require external
evidence: count agreement does not authenticate
provider billing.

B03 **persisted budget reservation replay does not reauthorize an external write
(2026-10-28)**: `BudgetLedger.reserve` correctly returns the existing
funding entry with `replayed=True`, but its verdict used to lose that
fact before reaching `ExternalWriteGuard`, which could answer EXECUTE
a second time for a real provider despite a previously reserved or
committed write. `BudgetVerdict` now preserves the replay flag, and the
guard returns DENY/`budget_reservation_replayed` without repeating the
provider call; the pipeline maps this refusal to a budget hold.
ADR-0041 records this internal authorization-safety decision. A
confirmed no-effect release may be re-reserved using the same key.
**81 focused ledger, execution-policy, pipeline and P4 guard tests
passed** (job `22c68c2ea7d119cbdf48ba941eeb12a1`).
After correcting three test-lint decimal constructors, whole-tree Ruff,
mypy (463 source files), and `git diff --check` passed; Python syntax
checks also passed for four changed files. The repository-side Python
syntax helper cannot parse the pre-existing Python 3.12 `type CostEstimate`
statement in `execution.py`; project tests run under `.venv`.
The post-guard-change full non-integration run completed with **3,113
passed, 1 skipped, 80 integration deselected, 16 warnings** in
353.04s, exit 0 (job `7dc36faac5bca358c7a2bd8de67f3021`).
Two additional regression cases were then added: a P4 engine
provider-call exception with rolled-back music storage but a separate
held `ops` reservation, and two SQLite workers concurrently
authorizing the same paid idempotency key. Both fail closed on a
replayed reservation. The subsequent combined focused suite passed
**83 tests, 2 warnings** (job `7c1e865b4589575238d680dc40694d2c`).
After correcting a test-only Ruff SIM117 issue, Ruff, mypy (463 files)
and `git diff --check` passed. The full non-integration suite **after
both added test cases** passed **3,115 tests, 1 skipped,
80 integration deselected, 16 warnings** in 332.46s,
exit 0 (job `b4f85acb60e7aac13a33366e5e09dfbb`).
Compose R5 remains unverified. SQLite and simulated
rollback are not PostgreSQL multi-process crash proof; actual
provider idempotency and settlement reconciliation still require
separate evidence; this does **not** solve per-P4-plan positive cumulative
limits or authenticate invoices. No live paid calls or shared DB
migration were performed.

## Program status in one paragraph

The P0 §1 success sentence is **true of the compose stack on mock providers and a mock
outcome model**, and not yet of a live deployment.

- Gate R5 (`tests/integration/test_r5_full_chain.py`, marker `full_chain`): a P1
  observation (the `ENV=ci` fixture connector) becomes scored opportunities, then a P2
  allocation over HTTP, then one `PipelineWorkflow` per directive with the real HTTP
  steps (P3 → P4 → P5 → P6 → P7 → P8 → P9 → outbox → P2) in Temporal's time-skipping
  server. The 28-day outcome timer passes in skipped time, the reward reaches P2, and
  the learning step's second cycle moves toward the rewarded arm. A lineage query joins
  every hop on stored identifiers. Verified by two consecutive passing runs (8 of 8 checks
  green each) on the compose stack.
- What it does not prove: the P4, P7 and P8 providers and the P7/P8 outcome sources are
  mocks (R6), and the reward is a bounded proxy, not economic value (economics gate).
  Videos are 360p previews on the compose stack.
- The dashboard reads the database and the phase APIs (demo data only behind `--demo`).

Closing the remaining gaps is stage R6 and the economics gate of the remediation plan. Each stage is approved
separately against its own evidence gate.

## Legend

| Mark | Meaning |
|---|---|
| **real** | Implemented and exercised against the thing it claims to handle. |
| **mock** | Works only against an in-process simulation. |
| **stub** | Code exists but returns placeholder, hardcoded or invented values, or is never called. |
| **absent** | No implementation. |
| **partial** | Real for part of the stated scope; the gap is named. |

## Platform (`packages/`)

| Capability | Status | Where / gap |
|---|---|---|
| Layered config, redacting secrets | real | `plm_platform/config.py`, `secrets.py` |
| Service-token auth (R15), fail-closed | real | One implementation in `plm_platform/auth.py`; every service and the dashboard use it |
| Postgres engine, schema-per-phase, migrations | real | `plm_platform/db.py` (5 s connect timeout), `migrations/` |
| MinIO content-addressed storage | real | `plm_platform/storage.py`; integration tests need compose |
| Temporal helpers (retry policy, ids, idempotency key) | real | `plm_platform/temporal.py` |
| Provider mode × execution policy | real | `plm_contracts/execution.py`, `plm_platform/execution.py`; defaults are mock + dry_run |
| External-write guard | real | P7 upload/thumbnail/playlist and P8 submit/takedown pass through it |
| Write authorizations | partial | Guard checks them, but they are only supplied in code. No persisted store or operator path yet (R3). |
| Budget ceiling | partial | `plm_platform/ledger.py` (ops, migration 0027): ceilings per scope and period with a currency, an operator rate card, entries reserved → committed/released, idempotent per key, serialised on the ceiling row (FOR UPDATE; proven by `tests/integration/test_ledger_postgres.py`). Fails closed on no ceiling, exhaustion, currency mismatch, unknown cost. P7/P8 use it when `PLM_BUDGET_LEDGER_CURRENCY` is set, otherwise every live write is refused. P4 does not reserve yet. |
| Pipeline workflow (PART XIV lifecycle) | real | `plm_platform/pipeline/workflow.py`: one Temporal run per directive through the PART XIV states plus HELD; every transition persisted in `ops` (migration 0030, append-only) with attempts, errors, provider ids, costs (Money with basis) and inputs/outputs. Paid steps reserve budget first; a refusal holds without calling the provider. Holds resume only through `authorize_resume` (signals `resume`, `external_resolution`); ANALYZING waits on a durable timer to the declared horizon. Gate R3 evidence (`tests/integration/pipeline/`) uses controlled mock steps against compose Temporal and Postgres; the Gate R5 test runs it with the real HTTP steps in Temporal's time-skipping test server, so the 28-day timer passes in skipped time and every instant the phases see is `workflow.now()`. |
| Pipeline HTTP steps | real | Every step calls its phase over HTTP with the service token and an Idempotency-Key (`pipeline/http.py`, `http_release.py`, `http_outcome.py`): P2 directive → P3 design/validate/materialize → P4 generation → P5 evaluation → P6 assembly → P7 publish → P8 release → P7 processing → P9 ingest and measure → P9 outbox → P2 update and next cycle. Verified through LEARNING on the compose stack by the Gate R5 test (`tests/integration/test_r5_full_chain.py`), on mock providers and the mock outcome model. |
| Pipeline/guard reservation keys | real | The pipeline reserves nothing; each phase's write guard is the single reservation at the write boundary (R5; `test_r5_workflow_supply_and_budget.py`) |
| Runtime in compose | real | Nine phase APIs, the P1 worker and the pipeline worker run in compose, health-checked (`/readyz` through each service's own database session; worker pollers). `plm doctor` checks all of them. A MinIO volume-owner init makes upgraded dev volumes writable. Verified locally with `docker compose up -d --wait`. |
| Outbox / cross-service delivery | real | `plm_platform/outbox.py`: per-schema outbox, enqueue in the producer's transaction, Dispatcher: DELIVERED only on acknowledgement, refusal FAILED, transient backoff (deterministic), timeout UNCERTAIN → reconciled by asking the receiver. In-process tests against P2's real app; real-HTTP proof is part of the R3 gate. Stuck rows raise no alert yet. |
| Exception queue with hold classes (PART XVIII) | partial | `plm_platform/exceptions.py` (ops, migration 0028) with policy data `policy/hold_classes.json`: hard stops (copyright, platform policy, KYC, account security, payment change, every P29 category, unmapped codes) never resume on approval; reviewable holds resume only for the policy's reviewer role with every evidence item. P5 raises holds and routes overrides through it. Not yet: P7/P8 holds, an HTTP route for external resolution, the dashboard view, real reviewer identity (static mapping). |
| Decision log (PART XXI) | real | `plm_platform/decision_log.py` (ops, migration 0029): every P2 allocation is logged with inputs, model, policy and action (confidence left empty; P2 states none); P9 attaches mature/closed outcomes per window through the lineage, and refuses an outcome whose directive was never logged. |
| Seam payloads C1–C8 | real | One shared, versioned model per seam in `plm_contracts/seams/` (ADR-0025). Producers serialise and consumers parse with it; missing, unknown, placeholder and future-major payloads are refused with a reason code. |
| Experiment lineage | real | `ExperimentLineage` (ADR-0026) travels from P3 through C3–C7 to P9's ISRC links and P2's reward. Placeholder ids are refused. |
| Injected time and derived seeds | real | `plm_platform/clock.py`: `Clock`/`SystemClock`/`FixedClock`, `stable_seed` (sha256). P3–P9 domain and engine code take `now` or a `Clock`; only API routes, CLIs, smoke scripts and connectors read the wall clock. The external-write guard takes `now` per decision. Enforced by `tests/unit/test_no_ambient_nondeterminism.py` (static) and `tests/unit/test_cross_process_determinism.py` (two interpreters with different `PYTHONHASHSEED` produce identical P2 draws, P3 experiments/track specs, and P4 plans, seeds, attempt/candidate ids and artifact digests). |
| Fail-open regression guard | real | `tests/contract/test_no_fail_open_defaults.py`, a narrow static scan that supplements the behavioural seam tests |
| Dashboard & Desktop Control Deck | real | `plm_platform/dashboard/`: the six PART XVII views read the database (read-only) and the phase APIs with reviewer authorization (`config/dev/reviewers.json`). Desktop GUI Control Deck (`scripts/plm_desktop_control.py`, `PLM_Control_Deck.bat` and Desktop shortcut `PLM Control Deck.lnk`) provides single-click orchestration: automatic Docker Desktop boot check, web deck launcher, manual cycle trigger, live console/audit progress, and human Go/No-Go gate (video, title, thumbnail review). |

## Phases (`services/`)

### P1 Market intelligence
| Capability | Status | Where / gap |
|---|---|---|
| YouTube Data API search + video details | real | `connectors/youtube.py` |
| Channel statistics (subscribers) | real | `collection/collectors.py` `fetch_channel_subscribers`: one `channels.list` call per hydration batch; subscribers and `channel_id` are stamped on video observations; a hidden or failed channel is unknown. Proven against recorded fixtures only; the live smoke does not exercise it. |
| Spotify, Trends connectors | stub | Real clients with no callers, so cross-platform (10%) is always unknown (R6) |
| Browser connector | stub | Always raises |
| Opportunity score, 7 weights summing to 1 | real | `MarketSettings.score_weights`, validated at load (finite, ≥ 0, sum 1). Not learnable. |
| Growth windows | real | `features/growth.py`: named windows d0_30, d30_60, d30_90, d90_180; median of in-window view deltas; an empty window is unknown. Scoring uses d0_30 vs d30_60. |
| Velocity 24h / 72h | real | `ArmFeatures.median_velocity_24h/72h` from timestamped sightings relative to an injected `now`; too few sightings is unknown |
| Competition concentration | real | HHI over channel activity, one vote per video; any active video without a channel makes it unknown, and the competition component is then unknown. Rows stored before migration 0021 have no channel. |
| 13-dimension taxonomy | partial | Keyword classifier only; the LLM path is absent |
| Daily/weekly/backfill Temporal jobs | real | `jobs/`; the only deployed worker |
| Quota-aware query rescheduling | real | `collection/scheduler.py` now preserves each query's stored priority, group and last-run timestamp when quota blocks collection. Previously a synthetic priority=100/never-run state promoted unrelated long-tail queries into a high-frequency retry cadence, reducing source diversity and wasting quota. Unknown query identifiers are refused; unit tests confirm the 168h long-tail retry and unchanged last-run fact. This does not increase source coverage or prove freshness of a live deployment. |
| `ENV=ci` fixture connector | mock | `cli.py`. It reads fixtures from `tests/fixtures`, so it needs a source checkout. Its corpus is the six-video multi-profile one (R5): the two-video corpus formed no arm at the support floor (2), so a CI run scored nothing. It forms two arms (`rnb`, `lofi_study_focus`). |

### P2 Autonomous A&R
| Capability | Status | Where / gap |
|---|---|---|
| Normal–Normal posterior, Thompson sampling | real | `modeling/reward_model.py` |
| Prior from P1 score/confidence/staleness | real | `modeling/prior.py` |
| P1 intake over HTTP | real | `ingestion/p1_adapter.py`; parses the shared C1 page, and a malformed page is `MarketUnavailable` |
| `POST /ar/results` storage, idempotent | real | Parses the shared C7 leg-2 model. One stored fact per (experiment, content, window, maturity); the old (experiment, window) key dropped a mature result that followed a provisional one and collided across contents (ADR-0031). Only stores; posteriors change on `POST /ar/update` |
| Posterior update | real | One effective outcome per (experiment, content): the mature result at the policy's `outcome_horizon` (default 28d). Other results are recorded as `provisional`, `off_horizon` or `skipped_duplicate` and never move the posterior (ADR-0031). The posterior is recomputed from the prior and the applied outcomes at the draw's `as_of`, so decay is measured from the draw (`modeling/evidence.py`). A revision protocol for a corrected outcome is absent; a second mature fact is refused as a duplicate. |
| Policy activation | partial | Stored and activated through the repository (`insert_policy` / `activate_policy`); there is no operator route or CLI. The Gate R5 test activates `p2-v1` this way and says so. |
| Policy persistence | real | The whole validated config is stored (`config_json`); rows from before ADR-0031 are refused on load instead of being completed from code defaults |
| Outcome horizon on C2 | real | `outcome_horizon` (C2 schema 2.0) tells P3 the window P2 learns from |
| Exploration split 70/20/10 | real | Configurable; exploit + adjacent + radical must sum to 1.0 (ADR-0023 #3, enforced since R2) |
| Budget allocation (PART XI) | stub | Equal split per slot (later stage) |
| Reward as expected economic value | absent | Uses P9's proxy as-is (economics gate) |

### P3 Experiments
| Capability | Status | Where / gap |
|---|---|---|
| Content genome (21 dimensions) | real | Templates are data (`genome/templates/genome_templates.json`, versioned and validated on load) for rnb, lofi, city_pop, jazz, ambient and house, each with the genre's ontology of alternatives. An unknown or missing genre is refused (`unknown_genre` / `genre_unspecified`); no genre borrows another's template. Template values are seed configuration awaiting creative review. |
| Treatment generation | real | Registry-driven (`experiments/values.py`): bpm, energy and categorical dimensions from the genre ontology, inside the exploration level's creative latitude. Unknown, hard-constraint, out-of-latitude or exhausted variables are refused with a reason code. One factor at a time by default; multi-factor needs declared factors and a stated design (`one_factor_per_treatment` or `full_factorial`). Treatments are pairwise distinct; fewer are designed when values run out, with the reason recorded. Seeded via `stable_seed`. Portfolio/probe/replication/follow-up types use the single-factor design (no replication lineage yet). |
| Validation | real | Design returns `draft`; only `promote_to_validated` (a passing report for the same experiment) sets `validated`. Materialisation refuses anything not validated. Contamination and treatments identical to control fail validation. |
| Directive parsing (C2) | real | `ingestion/p2_adapter.py` parses the shared model with no defaults (R1) |
| Directive transport from P2 | absent | No HTTP client; `GET /ar/directives` is never consumed (R3) |
| `budget_limit` from P2 | real | Stored on the experiment as the ceiling; materialisation uses it when no budget is requested, accepts a lower request and refuses a higher one. Amount is a float with no currency (C2 wire). |
| Measurement plan | real | The experiment records P2's `outcome_horizon` and `maturity_rule=publication_plus_horizon` at design time (ADR-0031, migration 0024); legacy rows without a plan are refused on load. The primary metric is still fixed at `retention_30s`. |
| Lineage origination | real | `experiment_family_id` (P3 §12) and `content_genome_id` are minted and carried on every track spec (ADR-0026) |
| Playlist duration feasibility | real | Owner decision 2026-10-15 (ADR-0036): a playlist is a declared range, 45–75 min; the target is only the planning midpoint. P3 plans the track count from the range minimum plus declared spares, the playlist spec declares the range, P6 lands inside it, and a short supply tops up or holds. The owner's range is the single policy source (`playlist_duration_policy` in the template data, R5): an open brief gets it, a declared bucket outside it (`10_45m`) is refused `playlist_duration_outside_policy`, and a template range outside it refuses to load. Until R5 an open brief inherited the genre template's range (lofi and jazz 60–120, ambient 60–180 min). |
| Track spec derivation | real | BPM and duration are the genome target or range midpoint (R5 rule); the declared duration range travels on C3. `materialization/arrangements.py` provides `p3-album-contrast-v2` deterministic, position-specific rhythm/melody/harmony/lead/structure directions: independently rotated axes prevent the former eight-track brief loop and give unique rhythm+melody pairings across 64 successive positions; extension tracks continue the sequence without changing controlled experiment factors. **This is text/brief diversity only, not evidence of audible diversity:** provider adherence, actual audio similarity and listening QA remain unproven. The provider allow-list is still fixed (R6). |

### P4 Music factory
| Capability | Status | Where / gap |
|---|---|---|
| Generation engine, planner, candidate bank | partial | At most 3 variants; no funnel or ranking; plan marked complete when short. `generation/prompts.py` passes C3 key, instrumentation, hook, structure, production style, energy, track arrangements, and declared vocal character/lyric density when applicable (never vocal instructions for an instrumental) to the prompt. New P4 plans use `p4-v2` IDs, separating them from previously persisted `p4-v1` attempts; exploratory variants now keep P3's declared BPM/genre/key/instrument/structure constraints and emphasize either phrase-level rhythmic space or arrangement dynamics, anchored to each track's P3 arrangement when present, rather than uniformly adding lofi/tape tags and shifting BPM ±4. The provider request metadata matches the compiled prompt. Deterministic hashes and protected-artist screening remain. 2026-10-24 verification: 22 focused prompt/planner/engine/contract tests passed; full non-integration regression 2,293 passed, 1 skipped, 73 deselected, 15 warnings; Ruff and mypy (451 source files) passed. This remains text/request-level evidence, not an audio similarity detector or evidence of real-provider adherence. |
| Mock provider | mock | Deterministic audio of exactly the requested duration at about −14.6 LUFS (44.1 kHz stereo): a seeded loop of decaying three-note chords (`mock-chord-synth-2`, R5), so distinct seeds are distinct recordings to P5's fingerprint. `mock-sine-synth-1` (one sine at 440 + seed % 100 Hz) was about ten recordings to P5, and after a few releases every mock candidate matched the internal catalog. Deterministic placeholder lyrics for vocal requests (ADR-0032, ADR-0035) |
| Suno / ElevenLabs adapters | partial | `SunoAdapter` implemented (`services/music_factory/providers/suno_client.py`, `adapters.py`) with authenticated session API, video-stream audio extraction (0-download quota usage), and FFmpeg demux to broadcast-standard 44.1 kHz PCM WAV. Its license lookup now **returns unknown** without verified account-level entitlement instead of inventing Pro rights from an API key. Billing lookup no longer invents 500 credits when missing/unavailable: `quota_known=False`, credits and exhausted state unknown. ElevenLabs remains stub; no live rights/credit proof yet. |
| Seed determinism | real | `stable_seed("p4.generation", track_spec_id, variant_code)`; identical across processes (R2) |
| Artifact storage via API | real | Content-addressed MinIO through `plm_platform.storage` (`MinioArtifactStorage`); unreachable storage fails closed (503, nothing committed); no in-memory fallback in a configured service |
| Rights snapshot | real | Records the terms the provider adapter declares (`license_terms()`, ADR-0027). The mock declares PLM-owned synthesis; Suno and ElevenLabs declare nothing (unknown), including when a Suno API key is configured. An API key is not evidence of account tier or commercial license. P5 must hold absent entitlement facts. |
| C4 event | real | Built only from stored facts (plan spec, the candidate's own attempt, its single full-mix artifact); refused with 409 when a fact is missing. Carries the spec's `requested_duration_range`; schema 2.0 (ADR-0032). |
| Provider selection | partial | The engine ignores the spec's `allowed_providers` and uses its default (mock). That is the intended mock-mode behaviour, but it is implicit; real provider selection is R6. |
| Cost tracking / budget enforcement | stub | `credits*0.05` is invented; `budget_limit` is not enforced |
| Protected-artist prompt blocking | real | `generation/prompts.py` screens with the shared `plm_platform/policy/protected_artists.json` before any provider call; the plan ends FAILED and each P29 reason opens a hard-stop hold on the plan. The list is a screening list: single-word names over-match, non-Latin names need a transliteration. |

### P5 Quality gate
| Capability | Status | Where / gap |
|---|---|---|
| WAV integrity, decode, duration, silence, clipping, stereo | real | `quality_gate/audio/`: WAV via libsndfile, other containers via FFmpeg (a decode failure blocks). Duration against the C4 range (ADR-0032). Clipping (≥ 3 samples at ≥ −0.01 dBFS) blocks; leading/trailing silence (−60 dBFS, 2 s / 3 s) warns; total silence blocks; stereo correlation warns below 0 and blocks at ≤ −0.5. Thresholds are data (`audio_qa_policy.json`, `p5-audio-qa-v1`). No dropout detection or windowed correlation yet. |
| Loudness (LUFS), true peak | real | BS.1770-4 integrated loudness (pyloudnorm) against −14 ±6 LU (warning); true peak with 4× polyphase oversampling against −1 dBTP (warning). Unmeasurable (under 400 ms, below the gate, more than 5 channels) is a blocking UNKNOWN. |
| Audio / lyric similarity, voice cloning | partial | Landmark fingerprint (numpy/scipy, `p5-landmark-v1`) against the internal catalog and the registered reference set; shingled lyric similarity against the registered corpus (hashes only); thresholds as data (ADR-0034). The route reads both from the qa schema: empty or unreachable is UNKNOWN and quarantines, so a deployment holds everything until references are loaded. No acoustic voice-clone detector. |
| Rights / provenance (P28) | real | All 12 P28 fields required; C4 3.0 carries the four that were missing (model version, generation time, source inputs, human contribution) from the producing attempt (ADR-0035). Anything unknown quarantines with `INCOMPLETE_P28_PROVENANCE`. Real providers must report a model version and lyrics before their candidates can clear (R6). |
| Detector throughput | partial | The internal catalog is every released fingerprint, matched in full for each candidate. R5: released fingerprints are cached per process (only new blobs are read) and the offset tally is vectorised (about 13 s → 1.2 s per candidate at 700 released recordings). Matching still scans the whole catalog, so cost grows linearly with releases; an indexed hash store is later work. |
| Intake | real | `POST /qa/evaluations` takes only the C4 event: P5 fetches the audio by `artifact_uri` from MinIO and verifies its sha256 on read; tampered, missing or unreachable artifacts are refused with a reason code and nothing is evaluated. A replayed event returns the stored evaluation, also when the same evaluation was committed by a concurrent request meanwhile (R5: that race was a 500). The `plm qa evaluate --audio` CLI still reads a local path (operator tool). |
| Override | real | Recorded separately from the automated disposition (ADR-0028) and authorized by the exception queue: hard stops are refused for every role; reviewable holds need the reviewer role and evidence (409 with the refusal code otherwise); a resolved case cannot be resolved again. Reviewer identity is a static mapping, and `auditor_id` is self-asserted. |
| Rights classification | real | A conditional or expired declared license is `COMMERCIAL_LICENSE_UNCERTAIN` (hard stop); a missing or unknown declaration is `UNKNOWN_COMMERCIAL_LICENSE` (reviewable with evidence) |

### P6 Media factory
| Capability | Status | Where / gap |
|---|---|---|
| Playlist manifest assembly with clearance check | real | `POST /media/packages/assemble` takes candidate ids; P6 fetches each manifest from P5 and checks the exception queue for open holds; source audio is fetched from MinIO and checksum-verified |
| Sequencing (BPM/key/energy) | real | Beam search `seq-beam-v2` (width 16) uses audio-measured key, BPM, energy arc/flow and unknown vocal density (worst-case rather than zero). Uses the declared P3 playlist duration range, not a separate ±5% policy. P6 features upgraded to `p6-features-v2` to extract deterministic four-position spectral texture signatures without any extra provider call. |
| Within-album near-copy audio hold | partial | `playlist/diversity.py` compares selected cleared tracks' four-part gain-invariant spectral signatures before the video render. At conservative score ≥0.998, assembly stops with `album_near_duplicate_audio`, avoiding renderer spend. Successful reports record pair count/max similarity; missing measurements are held rather than treated as zero. Unit/API tests use synthetic WAVs, including gain-changed near copies. No real catalog threshold calibration, melody/structure embedding or human listening gate yet; unavailable current signatures stop assembly with album_texture_unmeasurable. |
| Offline rhythm/pitch-class/energy contrast diagnostics | partial | `audio/contrast_probe.py` and `scripts/probe_album_audio_contrast.py --track A=local.wav --track B=local.wav` compute deterministic pairwise onset-timing, section pitch-class distribution and energy-arc proxy scores from locally supplied audio (2–20 named tracks), with optional `--sha256 NAME=HEX` verification against a caller-supplied original digest. The current diagnostic policy `p6-offline-contrast-v3` retains 10-second PCM and 128-frame FFT batches and one-file-at-a-time CLI measurement, and now rejects invalid/nonfinite/negative/wrong-length or non-unit-normalized vectors as explicit unknown instead of a misleading near-0/1 similarity. It also returns unknown for nonfinite resampling and extreme out-of-range float PCM. New read-only `audio/listening_review.py` and `scripts/validate_album_listening_reviews.py --report contrast.json --reviews reviews.csv` validate separately supplied human-label CSV against track aliases/SHA256 and the current policy version, reject duplicate reviewers or malformed inputs, preserve per-axis unknown/disagreement, and report available measurement coverage without echoing reviewer identities or notes. Report validation now cross-checks every numeric/null pair metric with both source recordings' explicit unknown reasons; inconsistent measurements, malformed unknown metadata and control-character source aliases are refused. The CLI rejects duplicate JSON keys, nonfinite JSON constants and malformed quoted CSV instead of silently accepting ambiguous input. The read-only review validator now additionally emits `data_quality` per-axis pair counts (missing, one-reviewer, uncertain, disagreement, unanimous similar/different), non-null metric coverage, declared-checksum pair coverage, and a decisive-only unanimity fraction with its explicit denominator or `null` when no decisive pair exists. More reviewers on the same pair cannot inflate the denominator. The review summary now distinguishes identical SHA256 across aliases from different-byte sources, reports checksum-pair reuse, and exposes a conservative unanimity fraction that omits repeated alias-comparisons of the same source-byte pair; zero eligible comparisons yield null. Distinct hashes are not proof of different songs, and even unique source pairs may share an individual song. No kappa, chance correction, provenance authentication, real listener labels or calibration is claimed. No labels are generated, no release/rights decision is made, and caller-supplied matching digests are **not** independently verified provenance. `docs/runbooks/offline-audio-contrast-review.md` gives the CSV schema, independent rights/SHA256 checks, blind human listening protocol and holdout design before any threshold could be considered. Each measurement exposes unavailable data as `null` with a reason; output includes policy version, source SHA256 and explicit no-clearance limitations. The tool is read-only, does not query P4/P5/P6 stores or providers, and is **not** wired into P5 rights, P6 near-duplicate blocking or release approval. It does **not** measure melodic plagiarism, musical structure semantics or calibrated distinctiveness. The local existing `output_media/albums/20261021-1456-lofi-study-focus` WAVs were analysed read-only: five selected tracks/ten pairs showed onset scores 0.956–0.981, section pitch-class scores 0.703–0.983 and energy-arc scores 0.956–0.983, all uncalibrated. Self-computed hashes are available but independent original SHA256 baselines, account commercial-rights evidence and listening labels were not verified; these scores do not establish originality or similarity. Verified 2026-10-24: 61 focused listening/audio unit tests passed; full non-integration pytest 2,354 passed, 1 skipped, 73 deselected, 15 warnings; Ruff passed and mypy passed on 453 source files. |
| Cross-album near-copy archive gate | partial | `playlist/catalog.py` serializes each selected track's measured 192-bin texture evidence and original SHA256 into the P6 package's existing `render_report.album_texture.evidence` (no C6 seam change). `storage/repositories.py:list_catalog_albums` reads all READY/PUBLISHED packages for the same brand (up to a fail-closed 100-package ceiling), rejects legacy/missing/partial evidence and compares candidate identity, audio checksums and very-high texture similarity before rendering the next release. Same-release alternate-render profiles are excluded from cross-album comparisons. A new album with different candidate IDs but identical music receives HTTP 422 `album_catalog_near_duplicate` and no second render; an exact previously assembled package replay returns its stored package before the catalog read, even when historical lookup would fail (in-memory SQLite / synthetic WAV proof only). Old packages require verified fingerprint backfill before further same-brand releases; saved evidence must declare the matching texture-policy and feature-extractor versions or the catalog blocks with `album_catalog_version_incompatible` (no silent legacy compatibility); >100 records block with `album_catalog_scope_incomplete`. No sound-alike melody embeddings, cross-brand policy or calibrated real-catalog thresholds yet. |
| Legacy album measured-texture backfill | partial | `texture_backfill.py` provides single-package operator-controlled remeasurement from P6's already-persisted original per-track URI and SHA256. `plm media backfill-album-texture --package-id ...` is **dry-run by default**; `--apply` changes only `media_packages.render_report_json` and its `updated_at` timestamp after validating stored playlist/tracklist identities, checksum-verified source bytes, measurable texture and within-album near-copy rules. The conditional UPDATE compares the original report and package tracklist plus the playlist identity/version, exact source-track records and track count. On PostgreSQL it now also takes transaction-scoped SHARE locks on the two P6 source tables **after** expensive measurement and **before** the final compare-and-set, preventing late source INSERT/UPDATE/DELETE until commit. This briefly blocks writes to these tables across all playlists (not a per-album lock), so production contention remains unmeasured. Five actual two-connection PostgreSQL cases passed in a freshly created and destroyed isolated test database: source mutation before locking is refused, and track UPDATE/INSERT/DELETE and playlist UPDATE wait during the backfill transaction; valid existing evidence is idempotent only when its ordered song/checksum records match the original playlist, while swapped evidence, noncontiguous track positions, mismatched experiment/arm lineage and other conflicts are held without writes. Saved signature evidence now includes both policy and extractor versions; mismatched or missing versions are held, never silently considered valid or overwritten. `plm media backfill-album-textures -p ID1 -p ID2` explicitly selects up to 20 packages: dry-run by default, optional `--apply`, an isolated transaction per package, and nonzero CLI exit with individual refusal codes for partial failures. Successful `--apply` commits are retained even if later packages fail (not an all-or-nothing batch). Latest 2026-10-24 checks: 34 focused P6 unit tests passed; isolated PostgreSQL integration tests: 5 passed; full non-integration pytest: 2,291 passed, 1 skipped, 73 deselected (including the 5 new integration cases), 15 warnings; `ruff check .` and mypy (451 source files) passed. Earlier CLI help was checked; no real historical database or MinIO backfill, automatic bulk discovery, operator approval workflow or past-catalog threshold calibration was exercised. Inaccessible/missing originals remain held. |
| Spectral evidence integrity validation | partial | The P6 within-album check, cross-album catalog and archived backfill now accept texture signatures only when all four 48-bin spectral windows are finite, nonnegative and independently unit-normalized (within numeric tolerance), with the current extractor version; catalog SHA256 values must be hexadecimal. Corrupt or zero/scaled evidence is held, including previously saved signatures presented for idempotent backfill, rather than producing an artificially low/high similarity. 2026-10-25 verified: 47 focused texture/backfill tests passed; full non-integration suite 2,484 passed, 1 skipped, 80 deselected, 15 warnings; Ruff passed and mypy passed on 454 sources. No Docker/production archive calibration or listening validation was performed. |
| Read-only historic album texture inventory | partial | `plm media inspect-album-textures --brand-id BRAND --limit 20 [--after-id ID]` reads only P6 READY/PUBLISHED package metadata for one explicit brand, in stable package-ID order, bounded to 100 records per query with explicit pagination. It classifies missing, malformed, incompatible-version or structurally valid stored texture evidence and prints the next cursor without fetching MinIO audio, modifying DB data, or claiming original checksum/rights verification. Pages are not a transactional snapshot, and this operator inspection does not bypass the separate 100-package catalog safety ceiling. SQLite synthetic inventory/CLI tests cover pagination, brand/status filtering, corrupt JSON, refusal codes and report immutability; the catalog loader and inventory now share strict saved-track validation (contiguous 1-based integer positions, nonempty source IDs, unique song IDs and exact signature order); the inventory additionally compares stored P6 playlist identity and original song/artifact/SHA256 metadata, reporting missing or conflicting sources as `source_metadata_unverifiable`. Focused catalog/inventory/backfill tests: 53 passed; Ruff and mypy (455 files) passed. The previous completed non-integration baseline had 2,492 passed, 1 skipped and 80 deselected; the new full regression finished with 2,498 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). The new-album catalog loader now also checks stored original playlist/session and per-track source metadata using the same read-only verification as the inventory; missing, deleted or changed sources can no longer be assumed usable archive fingerprints. Focused tests: 59 passed, Ruff passed, mypy passed on 456 files; full non-integration regression passed (2,504 passed, 1 skipped, 80 deselected, 15 warnings; exit 0). This is multi-query SQL metadata checking, not a concurrent-write snapshot. Actual production inspection and original audio/rights verification remain open. See `docs/runbooks/p6-album-texture-inventory.md`. |
| Live local P6 archive readiness (read-only, 2026-10-25) | partial | On the connected **local development** PostgreSQL, `neon_seoul` has 8 READY/PUBLISHED P6 packages and all 8 currently report `missing_evidence` (no measured archive fingerprints). Separate PostgreSQL SELECTs run with `SET TRANSACTION READ ONLY` found all 8 original playlist headers and a combined 168 source-track rows (four 19-track, four 23-track packages); every source row has a nonempty stored URI and a 64-character checksum string. A single MinIO HEAD succeeded for one source object (33,516,044 bytes), and its content-addressed key digest matched the saved checksum **string**. No audio-byte SHA256 or rights were validated by these metadata reads. The read-only `plm doctor` reported 15/16 checks passing, with a migration mismatch between DB `0038_release_reviews` and code head `0038_youtube_candidate_selection`; no migration was applied. All 8 explicitly selected P6 packages completed real-source backfill dry runs with no `--apply`: all 168 original tracks were fetched, their audio bytes SHA256 verified and measured textures accepted; each package returned `status=validated_dry_run`, `stored=False` (four 19-track albums and four 23-track albums). A subsequent inventory of all 8 still returned `missing_evidence`, confirming no archived fingerprint report was persisted by these commands. Cross-album near-copy calibration, independent rights checks and actual `--apply` are outstanding. No DB or MinIO write, paid generation or publication occurred. |
| P6 **bounded time-offset alignment diagnostic** (read-only, 2026-10-25) | partial | Added `services/media_factory/audio/texture_alignment.py` as a diagnostic-only candidate shift search using the existing four-window `p6-features-v2` signature over a fixed +/-0.75s grid, and extended `scripts/diagnose_p6_texture_robustness.py` with the baseline/best-shift score and number of valid offsets. Two SHA256-verified real development recordings with a known 0.5s leading-silence transformation improved from 0.392217/0.364477 to **0.999998073/0.999999740** at shift +0.5s; changing playback speed and pitch by 5% remained **0.828515/0.885356**, so time shifting does **not** solve all derivative detection. This is a known-positive diagnostic, not proof of false-positive safety or calibrated accuracy. The live 0.998 release gate and original extractor remain unchanged; human-labelled negatives and validation of alignment-search false-positive rates are needed before considering integration. Actual DB metadata READ ONLY, SHA256-verified MinIO reads, variants in memory, no writes. Focused alignment/robustness/album tests 30 passed; Ruff passed, mypy passed on 458 source files, and full non-integration regression 2,622 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). See `docs/reviews/2026-10-25-p6-time-offset-alignment.md`. |
| P6 **opt-in playback-rate + time-offset diagnostic** (read-only, 2026-10-25) | partial / known-positive real-source tested | `audio/texture_alignment.py` supports opt-in 0.95/1.0/1.05 playback-rate compensation plus +/-0.75s shifts (up to 21 score combinations), preserving the default seven-shift-only search. Read-only SHA256-verified archived recordings from two eligible P6 packages produced speed/pitch-changed positive-variant scores **0.828515 -> 0.999973303** and **0.885356 -> 0.999982548**, both selecting playback rate 1.05 and shift 0.0s; previously fixed 0.5s delay positives remain recovered by shift +0.5s. Focused alignment tests 25 passed, robustness tests 9 passed, repository-wide Ruff passed, mypy 458 source files passed, full non-integration regression **2,634 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). This does not establish false-positive safety, human listening quality, rights, or score calibration. Active 0.998 release gate and extractor untouched; no DB/MinIO writes, fingerprint persistence or publication. See `docs/reviews/2026-10-25-p6-playback-rate-alignment.md`. |
| P6 **real archive loopback listening smoke** (2026-10-26, READ ONLY) | partial / all selected source routes reverified, no human hearing | Added `--smoke-only` mode to `scripts/serve_p6_texture_listening.py` and focused tests. The mode binds the queue to original P6 metadata under REPEATABLE READ READ ONLY, checks the saved queue raw SHA256, temporarily serves an A/B listening page on 127.0.0.1, then issues same-PC HTTP 206 WAV byte-range GETs. Each audio request first runs the complete original MinIO SHA256-verified fetch; no files or CSVs are written and the private-token server shuts down afterward. **Actual 8-album archived six-pair queue: 12/12 verified original WAV HTTP responses, six pairs, exit 0, no reviewer labels, no persisted server**. SHA256-pinned queue was `20a3a30691d4e7ec00ae59ed1e13ad0f8ce6e62ba8d756e0e644785d0efdd0f4`. Synthetic smoke/listening focused tests 14 passed; repository-wide Ruff passed, mypy 462 source files passed; full non-integration regression **2,702 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). This proves source-route reachability, **not** a human browser audio experience, copyright clearance, identity independent reviews or calibrated false-positive safety. Details: `docs/reviews/2026-10-26-p6-local-listening-live-smoke.md`. |
| P6 **score-blinded localhost listening review** (2026-10-26, explicitly started operator tool) | partial / software tested, **no human adjudication** | Added `audio/texture_listening_review.py`, `scripts/serve_p6_texture_listening.py`, and listening unit tests for a temporary **127.0.0.1-only** review screen. It requires a previously verified report SHA256, explicitly selected P6 package IDs bound to READ ONLY archive metadata, a private per-session URL token and strict Host/path checks. Browser A/B original WAV playback is fetched on demand with source SHA256 verification, no-store/security headers, bounded WAV size and single-range HTTP 206; no scores, album identities or predictions shown. A human manually chooses same/different/uncertain, optionally skips pairs, and actively downloads a personal CSV with required evidence for decisive labels. Server performs no audio/review/database/MinIO writes; browser CSV saving is the operator's explicit action. Updated `scripts/evaluate_p6_texture_labels.py` to accept repeatable separate `--reviews` CSV inputs, preserving unknown/disputed label handling. Synthetic localhost/request/form/checksum and evaluator focused tests **38 passed**, repository-wide Ruff passed, mypy **462 source files passed**, full non-integration regression **2,701 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0); standalone CLI `--help` succeeded, while real human browser evaluation and live-provider integration were not run. **No real human heard or reviewed tracks**; this does not establish rights, independent reviewer identity, music originality or a calibrated threshold. Details: `docs/reviews/2026-10-26-p6-local-listening-workflow.md`. |
| P6 **saved review queue live integrity replay** (2026-10-26, READ ONLY) | partial / actual source scores reverified | Added `services/media_factory/audio/texture_review_integrity.py`, `scripts/verify_p6_texture_review_queue.py` and dedicated unit tests to refuse altered pair IDs/labels/counts/scores, nonfinite/duplicate-key inputs and stale archive mappings; bound queue sources to explicit P6 metadata under REPEATABLE READ READ ONLY. Direct-run import-path failure was corrected by centralizing bidirectional `score_pair` in the service module, shared by the original queue preparer and verifier. Actual 8-album saved queue was checked against archived originals: **16 SHA256-verified source WAVs remeasured**, relative high/middle/low selection/order re-created, **6 cross-release pair scores recomputed**, total **28 checksum-verified original fetches**, all successful (exit 0); saved JSON SHA256 `20a3a30691d4e7ec00ae59ed1e13ad0f8ce6e62ba8d756e0e644785d0efdd0f4`. No human review labels, rights clearance, production accuracy or upload readiness was established. Focused queue/integrity tests **28 passed**, repository-wide Ruff passed, mypy **461 source files passed**, full non-integration regression **2,688 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0); integration and live provider checks were not run. No DB/MinIO writes, fingerprint persistence or gate change. See `docs/reviews/2026-10-26-p6-review-queue-integrity.md`. |
| P6 **eight-album human-listening review queue** (read-only, 2026-10-26) | partial / no human adjudication | Added `audio/texture_review_selection.py` and `scripts/prepare_p6_texture_review_queue.py` for deterministic bounded cross-release pair selection across 2..8 explicitly named P6 packages, 2..3 original samples/album and up to 12 pairs. The score-ranked selected sources are diversified across relative high/middle/low four-window spectral thirds and reused tracks/release pairs are avoided where possible; this is **not a random or truth-labelled accuracy sample**. Local READ ONLY and SHA256-verified 8-album run: 168 archived tracks, **16 sampled source WAVs measured**, 6 human-review pairs (2 per relative stratum), 12 distinct original recordings, all 8 albums represented. Highest fixed score among chosen pairs 0.6454168320; highest time+rate score 0.6531942745. Saved the 6 measured pairs, SHA/source IDs, and unlabelled evaluator input to `docs/reviews/2026-10-26-p6-eight-album-review-queue.json`; pending-review CSV contains two blank reviewer rows per pair, **not actual labels**. The existing evaluator succeeded with 6 unadjudicated pairs, 0 reviewed and `null` positive/false-positive fractions. Six focused tests, repository-wide Ruff and mypy (460 source files) passed; full non-integration regression **2,666 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). Integration and production checks were not run. No production gate, DB/MinIO artifact, migration, publish or rights change. See `docs/reviews/2026-10-26-p6-eight-album-review-selection.md`. |
| P6 **separate human-labelled rate/offset evaluation** (read-only, 2026-10-26) | partial / human labels absent | Added `audio/texture_label_evaluation.py` plus `scripts/evaluate_p6_texture_labels.py` for bounded 1..200 source-pair score reports and optionally supplied reviewer-labelled CSV. Require at least two distinct reviewer pseudonyms and unanimous decisive labels per source pair; unknown, missing, single-review or disagreement remain unadjudicated. Produce descriptive, per-method TP/FN/FP/TN and sample fractions only with denominators; missing labels yield `null`, never an assumed false-positive rate or clearance. The source robustness CLI now also scores two SHA256-verified archive originals bidirectionally and emits a separate unlabelled pair score report. On two archived tracks with different checksums, fixed/offset maximum **0.50022448** and 21-candidate rate+offset maximum **0.52451513**; this does not establish the tracks as musically different. Saved one unlabelled pair in `docs/reviews/2026-10-26-p6-unlabelled-cross-source-scores.json`; the evaluator returned 1 unadjudicated, 0 human-reviewed, and `null` accuracy fractions. Combined label/alignment/robustness focused suite **60 passed**, mypy passed **459** source files, repository-wide Ruff passed, and full non-integration tests **2,660 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). Integration and live-provider tests were not run. No operating threshold change, DB/MinIO writes or external publication. See `docs/reviews/2026-10-26-p6-rate-offset-label-audit.md`. |
| P6 **known transformation false negatives** in spectral near-copy gate (read-only, 2026-10-25) | blocked for originality clearance | A new diagnostic `scripts/diagnose_p6_texture_robustness.py` fetched SHA256-verified source audio for the first track in each of two explicit `neon_seoul` archived packages; modified WAVs were created **only in memory**, not stored. The existing four-window extractor scored gain reduction to 65% at 0.999999999956/0.999999999943 (above 0.998); playback 5% faster with pitch change scored **0.828515/0.885356**, and 0.5-second inserted leading silence with equal tail truncation scored **0.392217/0.364477** (all four **below** 0.998 despite known same-source relationship). This proves a reproducible tested scenario where the present near-copy gate misses simple modified copies; the full 168-source, 12,332-cross-release-pair zero-threshold-hit audit cannot certify melodic or derivative originality. Operator human-listening labels, calibrated true-positive/false-positive evaluation and an alignment-aware fingerprint comparison are required before changing production thresholds; the active 0.998 gate is **unchanged**. Two real source reads were metadata READ ONLY and MinIO SHA256 verified, no writes; synthetic robustness tests and original album texture focused tests 33 passed, Ruff and mypy 457 source files passed; full non-integration regression 2,608 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). See `docs/reviews/2026-10-25-p6-texture-robustness.md`. |
| P6 bounded **full-source** spectral cross-album audit (read-only, 2026-10-25) | partial | Added explicit `--all-tracks` to the existing P6 archive diagnostic, with a hard 2..8-package limit, 30-track per-album cap and 200-track total cap. A full read-only analysis completed across all **168 original SHA256-verified audio recordings** in the eight eligible `neon_seoul` packages, comparing every **12,332 cross-release track pair** (no tracks unsampled). Maximum four-window spectral similarity **0.7296809346608583**, zero pairs at/above the existing uncalibrated near-copy threshold **0.998**, and zero same-SHA256 cross-release pairs. Does not prove melodic originality, other-brand coverage, music rights, or human-listening acceptability. No DB/MinIO write, historical fingerprint persistence or publishing. Focused full-mode tests 17 passed; Ruff passed; mypy passed on 457 source files; full non-integration regression 2,600 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). See `docs/reviews/2026-10-25-p6-cross-album-full.md`. |
| P6 real-source cross-album **sample** texture audit (read-only, 2026-10-25) | partial | Added `scripts/diagnose_p6_album_texture_samples.py` and `services/media_factory/texture_sample_audit.py` for operator-selected (2..8 packages, 1..3 tracks each) bounded cross-release four-window spectral comparisons. All original playlist/track metadata is verified under one READ ONLY, REPEATABLE READ DB transaction; selected audio is fetched from MinIO with actual source SHA256 verification. Live `neon_seoul` measurement covered **16 of 168 tracks** (first and last of each of eight eligible albums), **112 cross-release sampled pairs**, highest 0.6454168320, **zero >=0.998 near-copy candidates**, **zero exact checksum reuse pairs**; 152 tracks were **not** similarity-measured in this sample. Four-song/two-album subset max 0.5002244816 (four pair comparisons). Focused synthetic/read-only tests 14 passed; Ruff passed and mypy succeeded on 457 source files; full non-integration regression 2,597 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). This does not validate all 168 tracks for sound-alikes, rights, human listening or musical originality. No DB/MinIO writes, fingerprint persistence or publication. Evidence and limits: `docs/reviews/2026-10-25-p6-cross-album-sample.md`. |
| Local P6 exact-source reuse audit (read-only, 2026-10-25) | partial | A `SET TRANSACTION READ ONLY` SQL inspection of **all eight currently READY/PUBLISHED** `neon_seoul` packages joined to their original P6 playlist tracks found **168 track rows and 168 distinct recorded SHA256 strings**, with zero SHA256 collisions and zero repeated song IDs across different release-manifest IDs. This rules out observed **byte-identical recorded-source reuse in this brand/scope**, not remixes, near-copy melodies, new file bytes of the same music, other brands, missing archives or legal rights. The audio bytes for all 168 track objects were SHA256-verified separately in the earlier dry-run backfill checks. No DB/object writes occurred. |
| Alembic 0039 **approved local development database upgrade** (2026-10-27) | **local DB migrated and preserved** / other environments NOT approved | After new user approval for actual DB migration, positively verified `ENV=local`, `plm-postgres-1` PostgreSQL **16.15** on loopback `5432/plm` and existing `0038_release_reviews`. Created and retained full custom-format PostgreSQL backup (11,438,027 bytes) under Git-ignored `data/migration_backups/0039_local_plm_20261027T141743Z_c3e08a9cd61f/`, SHA256 `4934842cf5e0554c080f4f204bd7601e1e428452dbeeea4abe62092d33277310`. Verified archive by restoring into independent PostgreSQL 16 test container, exact read-only source fingerprint equality, applying active 0039 branch merge, three-way data/schema/index/security/storage preservation and idempotence; then applied only missing YouTube 0038 sibling + no-op 0039 to **connected local development DB** in guarded single transaction with bounded locks (exit **0**, job `55bbcad91d67665dfa2113cb922e6958`). Real post-migration snapshot exactly matched the independently merged restore. Fresh DB read returned **one `0039_merge_0038_branches` row**, **2 review rows, 4 events, 8 manifests**, nullable `youtube.publish_manifests.selection_json`. `plm doctor` **16 PASS, 0 FAIL**. Backup digest reverified and isolated test container deleted. Focused tests **61 passed**, Ruff and mypy **463 files** passed; full non-integration regression **3,022 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 307.46s). Backup is **host-only, not offsite**; independent whole-PC/WAL/role-global disaster recovery and staging/production inventories remain unverified. No Git commit, MinIO write, external publishing or other DB deployment. See `docs/reviews/2026-10-27-0039-approved-local-dev-upgrade.md`. |
| Alembic 0039 **direct Alembic code-only hold** (2026-10-27) | safety guard implemented / real DB rollout NOT approved | Closed the raw Alembic bypass after the code-only 0039 installation: `migrations/env.py` now checks the installed revision graph and refuses ordinary **online** direct Alembic commands against any non-disposable DB before creating an engine. An eligible isolated test target must have an exact `plm_migration_disposable_*` name on loopback/non-default port, and the actual connected `current_database()` must match before migration SQL. The guard remains active even after a later code head. A read-only `python -m alembic current` on the shared configured DB returned the expected refusal (exit 1, no engine connection). Focused unit/layout tests **50 passed**; Ruff passed, mypy **463 files** passed; full non-integration regression **3,012 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 289.14s). A **new real PostgreSQL 16 disposable-only rehearsal under the updated Alembic guard** passed both sibling merge paths, data/DDL/security/storage fingerprint preservation, backup/restore and idempotence (exit 0, job `5561a138f2db043cba91ca8b5d6e9419`); test container cleaned. Shared DB post-check remained `0038_release_reviews` with **2 reviews, 4 events, 8 manifests**; no real DB migration. No shared DB/MinIO write, schema change or Git commit. Direct scripts using other Alembic environments are outside this guard. See `docs/reviews/2026-10-27-0039-direct-alembic-code-only-guard.md`. |
| Alembic 0039 **approved CODE-ONLY merge installation** (2026-10-27) | active graph converged / real database not migrated | Following explicit **code-only** approval, installed the exact SHA256-pinned historical `plm_0038_release_reviews.py` (SHA256 `58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6`) and no-op `plm_0039_merge_0038_branches.py` under `migrations/versions/plm/`. The installed Alembic graph now has exactly **one `0039_merge_0038_branches` head**, retaining both original 0038 parent IDs and SQL. Updated the CLI: `plm doctor` warns of a pending code-only rollout; `plm migrate` refuses pending 0039 changes even on a recognized DB revision, while raw Alembic was still unguarded at that time; the separate direct-Alembic guard was added later, as noted in the newer entry above. Real read-only preflight on the connected development DB returned the expected refusal (exit 1, no migration). Focused graph/DDL/CLI safety tests **73 passed, 1 skipped** (exit 0), Ruff passed, mypy **463 files** passed; full non-integration regression **2,995 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 384.75s). Prior isolated PostgreSQL 16 rehearsal succeeded on both historical starting states; no new real SQL migration on shared data or post-installation test DB was performed. The ADR remains a proposed decision; separate backup/restore and environment-specific approval required before DB upgrade. No DB/MinIO writes or Git commit. See `docs/reviews/2026-10-27-0039-code-only-installation.md`. |
| Alembic 0038 **branch-merge architecture decision draft** (2026-10-27) | proposed / **owner acceptance and deployment approval pending** | Following the two successful isolated PostgreSQL 16 migration paths, prepared `docs/proposals/adr-0038-sibling-merge-exception-draft.md` to review a one-time exception to strictly linear Alembic ancestry while retaining the two historical 0038 migrations and a single 0039 head. It specifies target-environment inventory, immutable historical revision checks, durable off-target backup and independent restore, fresh three-way preservation evidence, writer freeze, post-upgrade checks, and independently approved code installation versus each real DB migration. The proposal was **not** moved into accepted `docs/adr/`. At the time of drafting, an independent read-only graph check showed only `0038_youtube_candidate_selection` as the code head and 0039 uninstalled; **subsequently, code-only installation was separately approved and completed** as reported in the newer entry above. No shared DB write occurred. See `docs/proposals/README.md` and `docs/reviews/2026-10-27-0038-isolated-postgresql-merge-rehearsal.md`. |
| Alembic 0038 **approved isolated PostgreSQL merge rehearsal** (2026-10-27) | passed on disposable copies / shared rollout NOT approved | Created a new PostgreSQL 16 Docker container with separate loopback port and no shared DB volume. The existing `plm` DB was read-only; consistent `pg_dump` and its pre/post fingerprints captured **2 release reviews, 4 events and 8 manifests** (11,438,027-byte backup). Real Alembic upgrades from `0038_release_reviews` and independently reconstructed `0038_youtube_candidate_selection` both reached one `0039_merge_0038_branches` row. Each branch passed source/clone-before/clone-after typed schema/security/row/storage preservation; an existing manifest was given a synthetic non-NULL selection only on the YouTube-first disposable source and preserved through the merge. Both paths passed re-upgrade idempotence; separate YouTube backup (11,435,521 bytes) was restored and compared. The disposable container and archives were removed. Post-test read-only shared DB still at `0038_release_reviews`, counts 2/4/8. Focused guard/offline tests **64 passed**. Repository Ruff passed, mypy **463 files** passed, full non-integration tests **2,993 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 307.15s). No shared DB or MinIO write, production deployment or Git commit. This does NOT test a discovered real YouTube-first environment, persistent off-site recovery or deployment approval/ADR. See `docs/reviews/2026-10-27-0038-isolated-postgresql-merge-rehearsal.md`. |
| Alembic 0038 **disposable clone read-only preflight before migration** (2026-10-26) | partial / isolated merge not approved or run | Added `tests/support/migration_clone_preflight.py` and unit tests. Explicit target and historical starting revision are required; the resolved configured database must exactly match `plm_migration_disposable_*` **before a connection is opened**. The same settings object is then used for READ ONLY, REPEATABLE READ PostgreSQL evidence. Independent validation checks both 0038 starting-state schemas, manifest security/storage, review-table evidence, selection column and row hashes. A passing preflight always emits `authorizes_migration=false`; it does not create a clone, install 0039 or authorize migration. Focused tests **307 passed**, Ruff passed, mypy **463 files** passed. Under the current shared `plm` configuration, actual invocation failed closed as intended (exit 1, no database connection). Full non-integration regression **2,984 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 293.94s). See `docs/reviews/2026-10-26-0038-disposable-clone-readonly-preflight.md`. |
| Alembic 0038 **release-review history table durability evidence** (2026-10-26, READ ONLY) | partial / isolated merge not run | Added mandatory `release_review_storage` for both `ops.release_reviews` and `ops.release_review_events` in the source/clone-before/clone-after PostgreSQL snapshot. The shared storage contract checks relation kind, permanence, replica identity, partition status and rewrite rules: both tables must be permanent ordinary relations (r/p), default replica identity (d), nonpartitioned with zero rewrite rules. YouTube-first snapshots must have no review tables or storage evidence before the sibling upgrade. Focused snapshot/comparator/offline DDL tests **272 passed** (exit 0), Ruff passed, mypy **463 files** passed; full non-integration regression **2,943 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 296.62s). Development PostgreSQL READ ONLY query attested the expected state for both existing tables at `0038_release_reviews`. No real 0039 migration, restoration or crash recovery verified. See `docs/reviews/2026-10-26-0038-review-history-storage.md`. |
| Alembic 0038 **YouTube manifest storage/replication/rewrite preservation** (2026-10-26, READ ONLY evidence) | partial / isolated sibling merge not run | Added mandatory `youtube_manifest_storage` to three-way evidence: PostgreSQL relation kind, persistence class, replica identity, partition flag and rewrite rule count. The comparator now requires the historical normal permanent table (r/p), default replica identity (d), nonpartition and zero rewrite rules, refusing unsafe or changed metadata even if row/index digests are identical. Focused tests **255 passed** (exit 0), Ruff passed, mypy **463 files** passed, all four changed Python files passed syntax validation; full non-integration regression **2,926 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 293.33s). Actual development PostgreSQL READ ONLY evidence matched `0038_release_reviews`, 8 manifests and all expected storage fields. Old snapshots must be recaptured; actual crash durability, WAL recovery, either sibling upgrade and backup/restore remain unverified. See `docs/reviews/2026-10-26-0038-manifest-storage-preservation.md`. |
| Alembic 0038 **historical YouTube schema/index reference attestation** (2026-10-26, READ ONLY) | partial / isolated sibling merge not executed | Closed a same-bad-source/clone/after false-pass: the comparator previously permitted any well-formed 1–100-column manifest schema with `manifest_id` PK and only required the six named index DDLs to match each other. It now requires the exact **29 original pre-0038 columns** (migration 0013 plus 0033 tracks_json; types, nullability, defaults, timezone, computed/identity flags, PK) and fixed reference SQL for all **six existing indexes** (including idempotency UNIQUE). Identically corrupted evidence on both historical 0038 branches now fails closed. Focused tests **232 passed**, Ruff passed, mypy **463 files** passed; full non-integration regression **2,903 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 292.58s). Connected PostgreSQL READ ONLY evidence matched **29/29 columns, 6/6 indexes**, revision `0038_release_reviews`, 8 existing manifests. PostgreSQL DDL formatting/version differences intentionally fail closed; old evidence needs recapture. No actual sibling upgrade, clone merge or restore verified. See `docs/reviews/2026-10-26-0038-manifest-historical-reference-attestation.md`. |
| Alembic 0038 **YouTube manifest security preservation** (2026-10-26, READ ONLY evidence) | partial / isolated sibling merge not executed | Added mandatory `youtube_manifest_security` to original/clone-before/clone-after snapshots: hashed table and schema owner identities, hashed table/schema/column/default ACL values (no raw grants emitted), RLS flags, user-defined trigger and policy counts. Exact typed metadata is required; changes in rights or ownership now fail closed even when row and index digests are unchanged. Focused snapshot/comparator/offline DDL tests **203 passed** (exit 0), all four changed Python files passed syntax validation, Ruff passed, mypy passed **463 source files**; full non-integration regression **2,874 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 307.34s); connected PostgreSQL READ ONLY query succeeded (`0038_release_reviews`, 2 reviews, 4 events, 8 manifests, zero user triggers/policies, RLS off). The previous three-way evidence must be recaptured; actual isolated migration, backup/restore and effective inherited-role privileges remain unverified. See `docs/reviews/2026-10-26-0038-manifest-security-preservation.md`. |
| Alembic 0038 **YouTube manifest index/constraint preservation** (2026-10-26, READ ONLY evidence) | partial / isolated sibling merge not executed | Extended required three-way evidence with `youtube_manifest_integrity`: `pg_get_indexdef` captures all six historical `youtube.publish_manifests` index DDLs including primary index, requires valid/ready/live metadata and reflection agreement for five non-primary indexes, and attests empty CHECK/foreign-key/separately declared UNIQUE constraints. Unexpected changes now fail closed even when existing row SHA256 matches. Focused tests **176 passed**; full non-integration regression **2,847 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 297.38s); Ruff passed and mypy **463 source files passed**; actual connected PostgreSQL READ ONLY snapshot matched (`0038_release_reviews`, 6 indexes, 0 added constraints, reviews 2, events 4, manifests 8). Old JSON evidence must be recaptured. Actual Alembic merge, backup/restore and isolated clone tests are unverified. See `docs/reviews/2026-10-26-0038-manifest-index-integrity-preservation.md`. |
| Alembic 0038 **YouTube existing manifest common schema preservation** (2026-10-26, READ ONLY evidence) | partial / isolated PostgreSQL sibling merge not executed | Added mandatory `youtube_manifest_schema` to source/clone-before/clone-after digest-only evidence. It covers all **29 existing** `youtube.publish_manifests` columns except the separately checked `selection_json`: column name/type/nullability/default, timestamp timezone, computed/identity state and manifest_id PK. Malformed or changed structure now blocks comparator even when old record SHA256 digests match. Focused tests **161 passed**; full non-integration regression **2,832 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 297.75s); Ruff passed, mypy passed on **463 source files**, change-scoped whitespace check passed. Actual development DB READ ONLY reflection passed (`0038_release_reviews`, 2 reviews, 4 events, 8 manifests, selection column absent). Snapshot files must be recaptured; an isolated clone upgrade and restoration remain unverified. See `docs/reviews/2026-10-26-0038-manifest-common-schema-preservation.md`. |
| Alembic 0038 **YouTube selection default/generated-column preservation** (2026-10-26, READ ONLY evidence) | partial / isolated PostgreSQL merge not tested | Strengthened the digest-only `youtube_selection_column_contract` to require `server_default=null`, `has_computed=false` and `has_identity=false` in addition to JSON/nullable, matching the original YouTube 0038 sibling's unassigned-NULL semantics. Unapproved server defaults or generation properties and malformed/missing fields fail closed even when every source/clone row hash matches. Focused snapshot/comparator/PostgreSQL offline DDL tests **148 passed**; full non-integration regression **2,819 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 296.73s); Ruff passed, mypy **463 source files passed**, changed-file whitespace check passed. Live READ ONLY snapshot confirmed `0038_release_reviews`, absent selection column and valid reviews/security/access (2 reviews, 4 events, 8 manifests). Existing snapshots need recapture; no actual branch upgrade or database writes. See `docs/reviews/2026-10-26-0038-selection-default-generation.md`. |
| Alembic **shared read-only migration target inventory** (2026-10-26) | partial / 0038 branch reconciliation unverified | Root-cause fix for divergent safety checks: new `plm_platform.migration_safety.user_object_count` is used both by `plm migrate` on unversioned databases and by the explicitly opted-in destructive migration integration guard. It detects any non-system user schema and public relations/routines/user-defined types/extensions; missing, invalid or negative catalog evidence fails closed. Disposable name/opt-in and `current_database()` checks remain mandatory. Focused CLI/safety/offline graph suite **75 passed, 1 skipped** (exit 0), read-only development DB catalog check returned valid nonzero evidence, Ruff passed and mypy passed on **463 source files**; full non-integration regression **2,805 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 295.07s). Destructive integration round-trip and actual 0038 sibling merge were **not run**; no DB/MinIO writes or publication. See `docs/reviews/2026-10-26-shared-migration-catalog-inventory.md`. |
| Alembic **unversioned database preflight guard** (2026-10-26, READ ONLY) | partial / isolated 0038 merge unverified | Hardened `plm migrate` so missing/empty `alembic_version` no longer implies an empty PostgreSQL database. It counts all non-system user schemas, plus public relations, routines, user-defined types and extensions before any Alembic upgrade; nonempty, uncertain or failed inspection refuses migration, while the original blank database path remains permitted. CLI `doctor` now warns to confirm empty target. Focused CLI/safety/offline graph suite **66 passed, 1 skipped** (exit 0), including catalog-query failure refusal; repository Ruff passed, mypy passed on **462 source files**, and changed-file whitespace check passed; live development DB catalog query was read-only and the existing foreign `0038_release_reviews` branch was refused. Full non-integration regression later ran successfully: **2,796 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0; 296.98s). No DB/MinIO write, migration or external publication. See `docs/reviews/2026-10-26-unversioned-migration-preflight-guard.md`. |
| Alembic 0038 **review schema and column ACL evidence** (2026-10-26, READ ONLY) | partial / isolated PostgreSQL merge still unverified | Added mandatory `release_review_access` field for source/clone-before/clone-after review-table snapshots. The fixed read-only PostgreSQL catalog query verifies default ACL of the `ops` schema, zero schema-local default table ACL entries, zero explicit column ACLs and exactly two review tables. New evidence fails closed on missing or wrong-typed catalog facts, grants and clone drift; the YouTube-first branch without review tables records `null`. Focused tests **134 passed**, full non-integration regression **2,789 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0); repository-wide Ruff passed, mypy **462 source files passed**, changed-file whitespace check passed; live development DB READ ONLY check found `0038_release_reviews`, unchanged rows (**2** reviews, **4** events, **8** YouTube manifests), and valid schema/security/access evidence. Existing snapshots need recapture. Inherited role permissions, global default ACLs, effective service access and unrelated objects remain unverified. No isolated DB merge or DB/MinIO writes. See `docs/reviews/2026-10-26-0038-review-schema-column-acl.md`. |
| Alembic 0038 **review-table owner, ACL, trigger and RLS preservation** (2026-10-26, READ ONLY) | partial / isolated PostgreSQL merge not executed | Added mandatory `release_review_security` evidence from one REPEATABLE READ READ ONLY PostgreSQL transaction: SHA256 owner digests (raw roles not printed), default table ACL state, RLS flags, user-defined trigger count and RLS-policy count for both `ops` review tables. The offline comparator rejects unexpected grants, different/changed owners, triggers, RLS or missing/malformed metadata even when existing row and index digests agree. Focused tests **118 passed** and the actual development DB read-only structure/security check passed (`0038_release_reviews`, 2 reviews, 4 events, 8 manifests); repository-wide Ruff passed, mypy **462 source files passed**, full non-integration regression **2,773 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0), and changed-file whitespace check passed. Old evidence requires recapture. Effective inherited/column/schema privileges and unrelated objects remain outside scope; no approved clone, migration, DB/MinIO write or publishing. See `docs/reviews/2026-10-26-0038-review-table-security-preservation.md`. |
| Alembic 0038 **complete non-PK review-index DDL preservation** (2026-10-26, READ ONLY) | partial / isolated PostgreSQL merge not executed | The `release_review_schema` snapshot now captures `pg_get_indexdef()` DDL for every non-PK index on both `ops` review tables inside the existing READ ONLY, REPEATABLE READ transaction. Index catalog names must match SQLAlchemy reflection; invalid, unready or non-live indexes refuse collection. The comparator requires the exact historical B-tree event lookup index definition and rejects changes in method, DESC sorting, included columns, predicates, expressions and opclasses even if row hashes agree. Focused tests **98 passed**; full non-integration regression **2,753 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0); repository-wide Ruff passed, mypy passed on **462 source files**, and changed-file whitespace check passed. Live connected READ ONLY schema comparison passed at `0038_release_reviews` (reviews **2**, events **4**, YouTube manifests **8**). Old snapshots must be recaptured; index DDL formatting changes may fail closed across PostgreSQL versions. No DB/object writes or merge were performed. See `docs/reviews/2026-10-26-0038-full-index-ddl-preservation.md`. |
| Alembic 0038 **server-default and integrity-constraint preservation** (2026-10-26, READ ONLY) | partial / isolated PostgreSQL merge still unverified | Extended `release_review_schema` extraction and the historical 0038 reference check to include server-side column defaults plus CHECK, foreign-key and UNIQUE constraints on both `ops` review tables. Unexpected alterations are rejected even if source/clone row fingerprints agree. Focused schema/comparator/offline DDL tests **90 passed**; full non-integration regression **2,745 passed, 1 skipped, 80 deselected, 15 warnings** (all exit 0). Repository-wide Ruff and mypy (**462 source files**) passed. Changed-file whitespace check passed; whole-worktree `git diff --check` separately found two unrelated existing trailing blank lines. Live READ ONLY PostgreSQL inspection matched the reference (`0038_release_reviews`, reviews **2**, events **4**, YouTube manifests **8**; exit 0). Existing source/clone evidence needs recapture with these new fields. No DB migration/write or approved isolated clone. Index predicate/expression, triggers, privileges/ownership and backup/restore remain outside this evidence. See `docs/reviews/2026-10-26-0038-review-constraints-and-defaults.md`. |
| Alembic 0038 **review-table structural preservation** (2026-10-26, READ ONLY) | partial / isolated PostgreSQL upgrade not run | Augmented `tests/support/migration_review_snapshot.py` with `release_review_schema` evidence for both `ops` review tables (all column types/nullability/timezone flags, PK columns, event indexes). Hardened `migration_review_compare.py` to require reference-exact structural agreement for source and clone-before, preservation on the review-first path and creation on the YouTube-first path, with JSON type-sensitive comparisons to reject numeric/bool spoofing. Synthetic negative tests cover missing/wrong index, PK, column, nullable flag and SQL type. Existing shared development DB's READ ONLY snapshot showed `0038_release_reviews`, 2 reviews, 4 events, 8 YouTube manifests; **actual schema matched SHA256-pinned historical 0038 reference** in a separate exit-0 check. Focused schema/DDL/comparator tests **84 passed**. Repository-wide Ruff passed, mypy **462 source files passed**, full non-integration regression **2,739 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0); focused 84 tests rerun after the final style-only change. Old evidence JSON files require recapture; no migration, clone write, DB/MinIO side effect or publishing was performed. Structural coverage is intentionally bounded, not all foreign keys/checks/privileges. See `docs/reviews/2026-10-26-0038-review-table-structure-preservation.md`. |
| Alembic 0038 **nullable JSON column schema contract** (2026-10-26, READ ONLY) | partial / live merged clone not tested | Closed a schema-attestation gap: existing digest-only clone comparator accepted data-preserving snapshots without confirming the newly added YouTube `selection_json` column has **PostgreSQL JSON type and `nullable=True`**. `migration_review_snapshot.py` now captures `youtube_selection_column_contract` using the database column inspector, `null` if absent or exact `{"type":"JSON","nullable":true}` when present. `migration_review_compare.py` requires this across source/clone-before/clone-after with strict type checks, and fails on TEXT/JSONB, NOT NULL, missing fields or numeric-boolean impostors. Review-first development DB READ ONLY recapture confirmed revision `0038_release_reviews`, unchanged records (reviews **2**, events **4**, YouTube manifests **8**), and absent selection column/contract. New targeted comparator/snapshot/PostgreSQL offline DDL suite **73 passed**, zero failures; repository-wide Ruff passed, mypy **462 sources passed**, full non-integration regression **2,728 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). Prior snapshots must be recaptured using new schema; no clone migration, Alembic change, DB/MinIO write, rights clearance or approval. See `docs/reviews/2026-10-26-0038-json-column-schema-contract.md`. |
| Alembic 0038 **all-NULL preservation proof hardening** (2026-10-26, READ ONLY) | partial / isolated upgrade not run | Identified a real false-pass possibility in the digest-only clone comparator: the post-upgrade newly nullable YouTube `selection_json` column could claim **zero non-NULL** rows yet supply a different well-formed full-row hash. `migration_review_snapshot.py` now computes independently expected `youtube_all_null_selection` SHA256 from actual manifest IDs for every branch, even when the column is absent; `migration_review_compare.py` requires this field in source, clone-before and clone-after, checks stability and on review-first branch demands exact digest equality after column creation. CLI evidence parser now limits each JSON report to 64 KB and rejects duplicate keys and nonfinite numbers. Connected dev DB REPEATABLE READ READ ONLY recapture: review count **2**, events **4**, YouTube rows **8**, historical fingerprints unchanged, new expected-all-NULL digest `ac7f2a22637f35cccde6df83001a4ededc09282fea7a660982db3ab142ea1ac8`. Focused proof/DDL tests **62 passed** (final comparator/snapshot subset rerun: **56 passed**), repository-wide Ruff passed, mypy **462 source files passed**, and full non-integration regression **2,717 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). Legacy evidence files need recapture with the expanded schema and may not be fabricated. No clone migration, production change, DB/MinIO write or approval; actual PostgreSQL data preservation remains unverified. See `docs/reviews/2026-10-26-0038-all-null-proof-hardening.md` and `docs/runbooks/reconcile-alembic-0038.md`. |
| Alembic 0038 PostgreSQL **offline SQL check + live READ ONLY baseline** (2026-10-26) | partial / schema upgrade still blocked | Existing sibling 0038 graph/merge is **review-only**, not installed. New `tests/unit/test_migration_reconciliation_postgresql_ddl.py` compiles both missing-parent upgrade operations using PostgreSQL Alembic dialect **in memory, no database connection**: `ops` release-review tables ×2 and index ×1, nullable YouTube `selection_json` column ×1, merge node generates no SQL; parent downgrades produce destructive DROP statements. Six new DDL tests and 68 combined graph/layout/snapshot/comparator tests passed. Repeated the connected development DB fingerprint under one REPEATABLE READ READ ONLY transaction: `0038_release_reviews`, review rows **2**, events **4**, common YouTube manifest rows **8**, all full-row SHA256 digests exactly match 2026-10-25 baseline, and `selection_json` remains absent. **No schema migration, isolated clone, approval, stamp or DB writes**; actual branch upgrade/data preservation in PostgreSQL remains unverified and requires a separately authorized disposable clone and restore evidence. Details: `docs/reviews/2026-10-26-alembic-0038-offline-postgresql-ddl.md`, `docs/runbooks/reconcile-alembic-0038.md`. Whole-repository Ruff passed, mypy passed on **462 source files**, and full non-integration tests **2,708 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). |
| Local Alembic migration-history divergence (2026-10-25) | blocked | Read-only inspection confirmed the connected development DB's `alembic_version=0038_release_reviews`, plus existing `ops.release_reviews` and `ops.release_review_events`. The sibling `0038_release_reviews` migration exists under `.claude/worktrees/program-interface-dashboard-9c6dc4/` and descends from `0037_dashboard_views`. This checkout instead ends at the **different sibling** `0038_youtube_candidate_selection` (same parent); the new `youtube.publish_manifests.selection_json` column is absent in DB. `plm doctor` now explicitly warns that this database revision is not in this checkout's history and **not** to run `plm migrate` as an ordinary upgrade. A reviewed single-chain reconciliation, isolated migration test, backup/rollback plan and owner approval are required. The `plm migrate` CLI now runs a read-only database revision preflight before calling Alembic, refusing an unknown migration branch or unreadable revision instead of depending only on `plm doctor`. Mocked unit cases verify no upgrade call in the conflicting-branch/error paths; calling only the preflight against the connected development DB raised the expected refusal for `0038_release_reviews` without a schema change. No migration, stamp, downgrade or manual schema change was executed. Verified: migration/CLI focused tests 36 passed, 1 skipped; Ruff passed; mypy passed on 456 sources; full non-integration regression 2,506 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). For this additional CLI preflight hardening, 41 targeted CLI/migration tests passed, 1 skipped; Ruff and mypy (456 sources) passed. Final full non-integration regression after the CLI preflight hardening: 2,511 passed, 1 skipped, 80 deselected, 15 warnings; exit 0. A review-only two-branch reconciliation and isolated-DB plan is documented in `docs/runbooks/reconcile-alembic-0038.md`; the destructive `test_migrations.py` round-trip now skips unless an explicitly opted-in disposable database name and actual PostgreSQL `current_database()` both match; a real isolated migration run is still unverified. New pure safety tests cover missing opt-in, ordinary shared DBs, wrong connected DB, nonempty previously used DBs, and a valid disposable target; the test now also checks for absent PLM/ops schemas and public tables before any Alembic action. Combined targeted CLI/migration tests after this extra empty-DB hardening: 58 passed, 1 skipped. Full non-integration regression after empty-DB enforcement: 2,528 passed, 1 skipped, 80 deselected, 15 warnings, exit 0. Ruff passed and mypy passed on 456 source files. No destructive integration was executed. 2026-10-25 read-only schema audit confirmed 2 saved release-review rows and 4 review-event rows with table definitions matching the original worktree revision; the YouTube selection column remains absent. Created a SHA256-pinned reference copy and UNAPPROVED 0039 two-parent merge draft under `docs/proposals/` (outside the active migration chain). Offline Alembic graph resolution from either 0038 start to one 0039 head passed; exact step checks confirmed only the missing sibling operation then the 0039 merge, never a repeat of the applied sibling. Mock-intercepted upgrade methods contained only expected ops-table/index or nullable YouTube-column DDL calls, and the merge node had none (15 offline graph/layout tests passed; no SQL executed). Combined prior offline/migration/CLI suite 63 passed, 1 skipped. Latest non-integration suite after exact Alembic step and mocked-operations checks: 2,536 passed, 1 skipped, 80 deselected, 15 warnings (exit 0); Ruff passed, mypy found no issues in 456 source files. A repeatable-read, read-only snapshot tool now hashes all stored release review/event rows and common YouTube publish manifest fields, plus selection metadata if present, without printing row values (`tests/support/migration_review_snapshot.py`). The connected development DB yielded 2 review records, 4 review events, and 8 YouTube manifests, all with complete stable SHA256 baselines documented in `docs/reviews/2026-10-25-migration-preservation-baseline.md`; the selection column remains absent. Synthetic snapshot-focused tests passed 34; Ruff passed, mypy passed on 456 source files; final non-integration regression after the snapshot improvement: 2,545 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). Offline three-snapshot comparator now also rejects internally contradictory selection/manifest counts, noncanonical empty digests and missing mandatory keys (72 focused tests passed). Read-only archive SQL now fetches at most 10,001 ordered rows per table and refuses a partial fingerprint above 10,000; confirmed the 2 review, 4 review-event and 8 YouTube manifest digests on the live development DB remain unchanged. Ruff passed, mypy found no issues in 456 source files; final full non-integration regression 2,583 passed, 1 skipped, 80 deselected, 15 warnings (exit 0). Actual isolated upgrades remain unverified. No PostgreSQL schema or data merge was performed; isolated clone upgrade and data preservation remain unverified. Never run raw Alembic downgrade on the shared DB. See `migrations/README.md`. |
| Render profile | real | A request that names none renders with the deployment's `PLM_MEDIA_RENDER_PROFILE` (P6 setting, checked at boot; unset: `yt_longform_1080p_v1`, the production profile). **The compose stack renders at `preview_360p_v1` (640×360, 15 fps)**, so every local and CI pipeline video, including the Gate R5 run, is a preview. Measured on the dev machine: a 53-min playlist took about 23 min at 1080p and about 4 min at 360p. The same recordings under another profile share the stored sequence and visual plan (they used to collide, a 500). |
| Video render, thumbnails | real | FFmpeg 7: deterministic mastered audio, wave visualiser, H.264/AAC MP4; stored artifacts have checksums. Pillow thumbnail A (`thumb-layout-v3`) renders a night-window illustrated hero scene; B/C remain typography/gradient alternatives. All variants now cap title lines and width without shrinking below a source-size mobile legibility floor, ellipsize unusually long titles, and use dark backing plates to protect title contrast under pale palettes; accent-label ink adapts to accent luminance. The A variant's recorded text payload now correctly names the playlist concept, not only its brand. Pixel-level, font-geometry, blank-brand compatibility, PNG, checksum and deterministic tests verify mechanics; final project-wide non-integration pytest: 2,143 passed, 1 skipped, 68 deselected (15 warnings); `ruff check .` and mypy (449 source files) passed. **These tests do not establish actual mobile-device readability, editorial quality or CTR uplift**. Rendering remains synchronous in HTTP, with no frozen-frame checks or durable background render job. |
| Metadata, chapters, tags | real | Chapters validated against YouTube rules (`yt-chapters-v1`: first at 0:00, at least 3, ascending) and embedded natively into QuickTime/MP4 video container as FFMETADATA1 chapter atoms. Verified on live 50.8-min album render (Video `lPYmaPHPQr4`). Tags still hardcoded; no cover art produced for P8. |

| Shorts | stub | Clip descriptors only |

### P7 YouTube publishing
| Capability | Status | Where / gap |
|---|---|---|
| Publish state machine, idempotency | real | `engine.py`; timestamps come from the injected clock and are read back as UTC (stored timestamps used to come back naive) |
| YouTube client | real | `client/real.py` (`RealYouTubeClient`): OAuth 2.0 refresh token rotation, resumable chunked video uploads, custom thumbnails, and playlist creation. Tested live against Rhodes & Rain Records (`UC3Ps0F0z8Mw9ftakIcZ1cWA`). `client/mock.py` preserved for deterministic simulations. |
| Real-provider post-upload verification gate | partial | `youtube_publishing/verification.py` is now called by `engine.py` for REAL providers after videos.list and before any thumbnail/playlist write or Hop-2 publication event. Requires processing succeeded, matching uploaded identity, approved description and plausible duration, plus valid description chapter metadata. Pending processing remains retryable and preserves the uploaded video ID; invalid metadata is permanent failure requiring operator repair, not a successful VERIFIED receipt. Mock adapter still simulates its former states. **VERIFIED does not mean clickable player chapters:** channel eligibility/watch-page controls remain unverified. Unit tests use a test-only real-mode fake and mock guard, no live writes. |
| Post-upload chapter metadata diagnostics | partial | `services/youtube_publishing/diagnostics.py` and `scripts/diagnose_youtube_chapters.py` inspect the read-only YouTube API description, actual duration, processing status and optional saved album track timeline. The result never claims clickable player chapters; verifying the watch-page controls and the channel's Advanced features eligibility remains a separate operator step. Tests cover invalid and successful metadata with mock API responses only. |
| Legacy album producer direct writes | stub | `scripts/produce_full_playlist.py` is disabled at entry before paid Suno generation; the exported direct-upload function raises instead of calling the real YouTube API. This archived script cannot be used as a live production path. All actual publishing must use P5 rights clearance and P7's ExternalWriteGuard. |
| External-write guard, dry-run enforcement | real | A real client under dry_run/shadow ends `INTENT_RECORDED`; live writes need an authorization |
| Synthetic-media disclosure field | real | Comes from P5's finding carried on C6 (no hardcoded `True`); so far sent only to the mock |
| Publish manifest and candidate assignment | partial | Derived only from validated C6 candidates and the configured channel (ADR-0029). P7 API and `plm youtube publish` now accept explicit `metadata_variant_id`/`thumbnail_id`; default remains first-declared for compatibility, **not a performance ranking**. `CandidateSelection` freezes assignment method/policy, selected IDs and contemporaneous eligible candidate IDs in `youtube.publish_manifests.selection_json` (migration 0038, NULL on historical rows). Selected metadata and thumbnail are preflighted, response/attempt status expose the stored choice, and retries with changed candidate or other manifest facts refuse `candidate_selection_conflict` (HTTP 409, CLI exit 2), rather than silently switching or inventing exposure. Missing/duplicate candidates refuse `candidate_selection_invalid` (422). No C6 seam change or automated winner selection. Final whole-project non-integration regression: 2,147 passed, 1 skipped, 68 deselected (15 warnings); Ruff and mypy (449 files) passed, CLI help and Alembic single head `0038_youtube_candidate_selection` verified. No measured CTR attribution, randomized assignment, statistical significance or actual YouTube A/B exposure capture yet; mock/repository tests only. Migration application to a live database and integration tests remain unverified. The hop-2 event retains the original lineage. |
| Media bytes | partial | Placeholder bytes exist only on the mock client; a real client with no bytes fails before sending. Fetching real media is R4. |
| Channel profile / OAuth | real | Configured by an operator through `PUT /youtube/channels/{channel_id}` (R5; service token; `default_made_for_kids` and credential reference required). Real OAuth 2.0 loopback authorization flow in `scripts/authorize_youtube.py` with refresh token exchange and live channel verification. |

### P8 Distribution
| Capability | Status | Where / gap |
|---|---|---|
| Release state machine, fingerprint idempotency | real | `engine.py` |
| Distributor client | mock | `client/mock.py` only (R6) |
| External-write guard | real | Submit/takedown are refused before anything persists (HTTP 409) |
| ISRC allocation | real | Once per recording from a persisted sequence (`identifiers/allocator.py`, ADR-0029); the registrant code is development configuration (P0 §10) |
| Release compilation | real | Only from C5 manifests + the C6 package (+ a matching C6 event); release metadata comes from the caller; lineage is stored and read back |
| One recording on several releases | absent | `tracks.isrc` is unique, so a recording cannot yet appear on two releases (R6) |
| UPC, cross-platform catalog (P20) | absent | R6 |
| Raw report references (R10) | real | `reports/ingest.py` |

### P9 Analytics / economics
| Capability | Status | Where / gap |
|---|---|---|
| Unit economics calculator | real | `economics/calculator.py`: `Money` in the reporting currency; estimated (YouTube) revenue kept apart from realized (reported/final) revenue; net contribution on realized revenue only, `None` with a reason when revenue or cost is unknown or unreconciled; undefined ratios are `None` (ADR-0031) |
| Economics endpoint aggregation | real | `economics/ledger.py`: latest snapshot of one window (default `lifetime`) for views and estimated revenue; `sum_incremental` for statement lines and costs. Gate R2 tests `test_gate_r2_*` (in-process SQLite) |
| YouTube Analytics ingestion | real | Real OAuth 2.0 client (`RealYouTubeAnalyticsClient` in `services/analytics/ingestion/youtube_client.py`) calling YouTube Analytics API v2 for overall video/channel metrics. Existing aggregate client previously live-tested; `fetch_video_retention` now requests per-video `elapsedVideoTimeRatio`, `audienceWatchRatio` and `relativeRetentionPerformance` with strict validation and unavailable-as-empty semantics. The retention curve extension has **mock HTTP tests only**, is read-only and is not yet connected to the stored outcomes or automatic allocation. Latest extension: `fetch_video_traffic_sources` now reads YouTube Analytics `insightTrafficSourceType` with views/watch time (mock HTTP coverage). `reports.query` does **not** support thumbnail impressions/CTR; official Reporting API `channel_reach_basic_a1` CSV can be supplied manually via strict `ingestion/youtube_reach.py` parser, with an explicitly declared numeric CTR unit and impression-weighted aggregation over observed days. Authenticated single-report reach downloading is now available as an operator diagnostic (see row below), but report scheduling, automated discovery/backfill reconciliation, historical storage, experiment attribution and P2 learning integration remain absent. |
| P7 candidate assignment to P9 snapshot diagnostic | partial | New authenticated, read-only `GET /analytics/candidate-performance/{attempt_id}?window=28d`: P9 fetches P7’s stored attempt/selection/C6 publication through the P7 HTTP API (not a cross-service source import or an unaudited user-supplied assignment), checks attempt/video/channel/metadata identities, then joins only the latest P9 stored cumulative snapshot for that video and requested window. Returns candidate and eligible IDs, P7 upload-flag status, experiment arm, metric source (`synthetic` is explicit), observed timestamp, views/watch time if known, and **null impressions/CTR**; no inferred A/B winner or reward/P2 update. Unknown/legacy selection remains unknown; unverified publication, unsupported assignment, mismatched source facts and unavailable P7 service fail closed. Focused unit tests: 22 passed; full non-integration regression: 2,160 passed, 1 skipped, 68 deselected, 15 warnings; Ruff and mypy (450 source files) passed. Local mock-HTTP/SQLite tests only; actual authenticated deployment, Reporting API reach/CTR and real exposure attribution remain unverified. |
| Reach CSV preview with published candidate context | partial | Authenticated `POST /analytics/candidate-performance/{attempt_id}/reach-preview` accepts a bounded operator-supplied `channel_reach_basic_a1` CSV, declared CTR units, source reference and explicit completed-day range. It independently retrieves and validates the P7 publication, then uses the strict existing Reporting CSV parser (daily ID/CTR/duplicate checks) and impression-weighted video reach summary. Preview returns a CSV content SHA-256, self-declared unverified source reference, actual observed-day coverage, and separate `video_reach.video_thumbnail_impressions`/`video_thumbnail_ctr_percent`; candidate-specific CTR/impressions **remain null**. Mismatched channels, publication-day-inclusive or future periods, invalid reports and unsupported units are refused. The CSV format/hash does not prove authentic YouTube provenance or continuous thumbnail exposure; no DB write, reward update or A/B inference. Focused CSV/attribution tests: 36 passed; final whole-project non-integration regression: 2,171 passed, 1 skipped, 68 deselected (15 warnings). Ruff passed and mypy passed on 450 source files. Actual Reporting API fetch, provenance validation, cross-timezone reporting alignment and real provider verification remain unverified. |
| Authenticated YouTube Reporting reach download | partial | New read-only `ingestion/youtube_reporting.py` and `scripts/poll_live_analytics.py --reach-job-id JOB --reach-report-id REPORT --reach-ctr-unit ratio|percent --channel-id CHANNEL` support one **explicit** existing Reporting job/report (including backfilled revisions), reusing configured Analytics OAuth. The job's `reportTypeId` must be `channel_reach_basic_a1`, report ID/job ID/aware metadata times must match, and downloads use bearer-authenticated GET restricted to the Google Reporting API `/v1/media/<id>` HTTPS origin (no redirects/SSRF); decompressed CSV is capped at 4 MiB and validated with the existing strict reach parser. CLI checks all rows against the requested channel and logs job/report IDs, report period, createTime and content SHA256. This gives an auditable **authenticated Google API transfer**, not independently verified channel ownership, reporting timezone, per-candidate exposure, or A/B lift. Raw downloads are not persisted, no reporting jobs are created, no platform writes/P2 updates are made. Focused downloader/CLI/reach tests: 57 passed. Final full non-integration regression: 2,200 passed, 1 skipped, 68 deselected (15 warnings). Ruff passed and mypy passed on 451 source files. Mock HTTP tests only; real Google OAuth scope, report availability and cross-timezone alignment are unverified. |
| Read-only Reporting job/report catalog and revisions | partial | `scripts/poll_live_analytics.py --list-reach-jobs` and `--list-reach-reports JOB_ID` enumerate accessible Google Reporting jobs/reports using existing Analytics OAuth, without requesting YouTube Data API or downloading a report. `jobs.list` uses `includeSystemManaged=true`, filters exactly `channel_reach_basic_a1`, and `jobs.reports.list` fetches all pages within a fixed budget (10 × 100); cyclic/malformed/incomplete pagination, duplicate IDs, ambiguous same-interval/same-createTime revisions, invalid metadata or unsafe report URLs **refuse instead of returning incomplete or invented results**. Per exact UTC-normalized (startTime,endTime), each report ID is preserved and annotated `current` (newest createTime) or `superseded` with superseding ID; no revision is auto-downloaded, auto-merged or treated as an A/B winner. No accessible jobs/reports is an explicit no-result exit. Focused catalog/downloader/CLI tests: 62 passed. Final whole-project non-integration regression: 2,217 passed, 1 skipped, 68 deselected (15 warnings); Ruff and mypy (451 source files) passed. Mock HTTP+CLI tests only. Real Reporting API credentials/scopes, report pagination characteristics, report retention and revision semantics remain unverified in production; no DB, P2 update or external writes. |
| P7 publication ↔ authenticated Reporting reach preview | partial | New service-token-protected, read-only `POST /analytics/candidate-performance/{attempt_id}/authenticated-reach-preview` accepts existing Google job/report IDs, declared CSV CTR unit and explicit completed-day period (not a caller-supplied CSV/source string). It first validates P7's verified publication/channel/selected candidate and P9's same-video metric snapshot; P9 then lists the exact Google Reporting job catalog and verifies that the requested report revision exists, downloads it over authenticated Google Reporting API, and **revalidates report ID, job ID, creation time and reporting interval against the catalog** (concurrent revision changes refuse). One report must match its exact `America/Los_Angeles` local calendar day (including DST 23/25-hour boundaries) and a completed date after the video's Pacific publication day; UTC-midnight assumptions are rejected. Wrong channels/days are refused, and Google HTTP 401/403 is distinguished as `google_reporting_access_denied` (required `yt-analytics.readonly` or `yt-analytics-monetary.readonly` scope and authorized channel/job must be verified separately). Returns downloaded bytes SHA256, job/report IDs, creation time, `current`/`superseded` status and video-level impressions/weighted CTR; retains `thumbnail_impressions`/`thumbnail_ctr_percent` as NULL for candidate effects. Source is marked `google_reporting_api_authenticated_download`, but actual YouTube reporting timezone and per-candidate exposure remain unverified. No stored report, reward, P2 update, publisher modification or other external write. Focused mock-HTTP P7/P9/Google tests: 78 passed. Final full non-integration regression: 2,231 passed, 1 skipped, 68 deselected (15 warnings); Ruff and mypy (451 source files) passed. Reporting Pacific-day/DST correction: report metadata must match its actual America/Los_Angeles civil-day bounds (normal 24 h, spring 23 h, fall 25 h) and the CSV date and publication-day boundary; missing/mismatched facts refuse. Windows IANA timezone data is an explicit runtime dependency (tzdata pinned in constraints). Google HTTP 401/403 maps to reporting-access-denied, not an empty result; scope/account-job access cannot be inferred from configured OAuth credentials alone (required scopes: yt-analytics.readonly or yt-analytics-monetary.readonly). Focused date/scope tests: 71 passed. Final whole-project non-integration regression: 2,241 passed, 1 skipped, 68 deselected (15 warnings); Ruff and mypy (451 source files) passed. Real OAuth scope, deployed P7/P9 handshake and real Google download unverified. |
| P7→Google metadata-only readiness | partial | Service-token-protected `POST /analytics/candidate-performance/{attempt_id}/reporting-readiness` reads the verified P7 publication and authenticated Google reach job/report revision catalog. It checks exact Pacific reporting-day eligibility with the SAME validator as the downloaded report preview, and preserves current/superseded provenance. `catalog_metadata_eligible_only` is **not** download access, CSV content, channel ownership, thumbnail exposure, or A/B evidence: all these checks explicitly remain unverified; metrics are null and no CSV is downloaded. Missing/invalid reports or denied catalog access fail closed (404/422/403/502). Focused metadata-only readiness and Pacific-date tests: 33 passed. Full non-integration: 2,250 passed, 1 skipped, 68 deselected, 15 warnings; Ruff and mypy (451 files) passed. Only mocked P7/Google integration tested, no real OAuth or deployment verification; no DB write, upload, or P2 reward. |
| Read-only live configuration and Reporting authorization preflight | partial | `scripts/check_reporting_environment.py` defaults to **offline** boolean presence-only checks for OAuth client ID/secret/refresh token and internal service token. Explicit `--probe-services` checks loopback P7/P9 `/healthz`+`/readyz`; `--probe-internal-auth` verifies token acceptance only with reserved nonexistent GETs, without real record enumeration. Both refuse remote/invalid URL overrides. Explicit `--probe-google-catalog` performs OAuth refresh and read-only Reporting `jobs.list`, returning only safe status flags (never credentials, provider response bodies, job IDs or URLs); failures are distinct from missing jobs. OAuth refresh failure messages no longer echo arbitrary provider body text. Local **2026-10-24**: all four credentials present; P7/P9 health and database ready; both internal service-token probes accepted; Google Reporting catalog **403 permission_denied**. A follow-up explicit read-only check found that the refreshed Google token reported scopes but **neither required Reporting Analytics read scope** (`required_scope_not_reported`); missing scope is confirmed, while additional channel/job/API restrictions and live report transfer remain unverified. YouTube Analytics HTTP error bodies from metrics, traffic-source and retention reads are now redacted. See `docs/runbooks/youtube-reporting-readonly-preflight.md`; focused preflight/OAuth/redaction tests: 32 passed; full non-integration regression: 2,279 passed, 1 skipped, 68 deselected (15 warnings); Ruff passed and mypy passed on 451 source files. No token changes, publication, DB writes or P2 updates. |
| Read-only latest-upload performance and chapter diagnostic | partial | `scripts/poll_live_analytics.py` discovers the authenticated channel's newest 1–50 uploads via `RealYouTubeClient.list_recent_uploads` (channels → uploads playlist, no hardcoded video IDs), validates channel identity, then reads each video's real description, length, processing status and 28-day-by-default metrics/retention. Reports 0:30/2:00/5:00 and 10%/50% checkpoints where measured; optional per-source view/watch-time query and operator-supplied (unverified-origin) `--reach-report` CSV with `--reach-ctr-unit` and exact `--channel-id` adds available thumbnail impressions/impression-weighted CTR with observed-day coverage; flags invalid long-form chapter descriptions but **never claims playable chapters verified**. Missing data are unknown and errors produce nonzero CLI exit. Tested with mock API and synthetic response fixtures only; operator CLI, not scheduled or persisted to P9/P2, and no live current channel read was performed. |

| Analytics CLI ingest | real | Missing facts are refused with exit code 2 (the platform `plm analytics ingest` forwards `--provider/--period/--currency`); JSON amounts parse to `Decimal` |
| Distributor statement ingestion | real | Declared statement currency required; amounts parsed to `Decimal`; a bad line refuses the whole statement before anything is stored; lines stored as incremental per period. No invented ids; rows without an ISRC are counted as unattributed (ADR-0030) |
| C7 leg-1 release links | real | `ingestion/releases.py` links each (release, ISRC) to its lineage, idempotently |
| Outcome measurement inputs | real | Maturity is derived from `published_at` and the window (`maturity_at`); caller-asserted maturity and an observation before publication are refused (422) |
| C8 feature view | real | Unmeasured values served as null (no zeros, `1.0`, `false` or hardcoded `REPORTED`) |
| Currency, decimal money | real | `Numeric(20,6)` plus currency columns (migration `0022_analytics_money`); `fx_rates` table and `POST /analytics/fx-rates`; reporting currency KRW by default; a missing rate is HTTP 409 `fx_rate_missing`; legacy rows with no currency are unreconciled, never assumed USD. C7 `raw_metrics.estimated_revenue` is still a float in the reporting currency (economics gate) |
| Reward computation | partial | `engagement-contribution-proxy-v1`, a bounded proxy, not economic value (economics gate). Net contribution as `Money` in the reporting currency; the USD 50 scale is converted through FX; attribution confidence passed through; sensitivity tests pin bounds and monotonicity |
| Reward delivery to P2 | real | Recorded and enqueued in one transaction (`analytics.outbox_events`, 0026); the pipeline's `dispatch_reward` step (or `plm analytics dispatch`) delivers with an idempotency key; P2 dedupes and answers `GET /ar/results` for reconciliation. A re-dispatch and a replayed payload are not counted twice (Gate R5). |
| Closed-loop Bayesian learning (P9 -> P2) | real | Verified live end-to-end via `scripts/run_phase_p9_closed_loop.py`: real metrics ingested from YouTube Analytics API v2 for published album `lPYmaPHPQr4`, evaluated under `engagement-contribution-proxy-v1`, delivered over Seam C7 leg-2 to `POST /ar/results`, applied in `POST /ar/update` (arm `lofi_study_focus` posterior mean 0.40825 -> 0.45798, variance 0.10649 -> 0.04878), and subsequent cycle allocated with Thompson sampling draws in `POST /ar/cycles`. |
| Autonomous loop worker & Go/No-Go Gate | stub | `scripts/autonomous_loop_worker.py` is a prototype, **not a working autonomous publish chain**: it reads an existing MP4/thumbnail and fixed tracklist, never uploads a new video, uses hardcoded YouTube video ID `lPYmaPHPQr4`, falls back to invented arm scores/identities on P1/P2 failures, and can post a D28 `MATURE` reward built from a seven-day Analytics query with a fixed experiment identity and asserted attribution. Do not schedule it for production or use its rewards to train P2. Go/No-Go is a prompt, not the independent proof that a new package passed P5/P7. See `docs/reviews/2026-10-23-content-quality-audit.md`. |

### Architecture parts with no code
PART X (weekly multi-agent review / LLM router), XII (Shorts engine), XIII (community),
XXIII (multi-brand beyond a `brand_id` column), and P10 recursive allocation. These are
deferred and tracked here; they are not scheduled before the economics gate.

## Focused content-quality remediation evidence (2026-10-23)

- Static analysis (historical legacy audio-backfill snapshot): `.\\.venv\\Scripts\\python.exe -m ruff check .` — clean; `.\\.venv\\Scripts\\python.exe -m mypy packages services` — no issues in 449 source files.
- Non-integration regression: `.\\.venv\\Scripts\\python.exe -m pytest -m "not integration" -q` — historical snapshot **2118 passed, 1 skipped, 68 deselected, 15 warnings** (181.23 s). Covers verified source-audio legacy P6 backfill with dry-run, explicit apply, report conflict refusal and idempotence; also includes prior same-brand cross-album screening, source/reach metrics, chapter/retention diagnostics, publishing verification, market cadence and thumbnail changes. Earlier failures came from legacy contract fixtures with identical audio and a fake REAL YouTube client lacking processing/length evidence; fixtures and validation order were corrected, retaining fail-closed production checks.
- This validates Python/unit/contract paths only. **Not tested:** live provider Suno generation, actual YouTube player chapter visibility, artist listening quality, real CTR/retention effects, Docker integration/full-chain, production expenses or live P2 policy learning after changes.
- All R6 live deployment and economic/rights gates remain governed by the existing authorization and review plan.
- **2026-10-24 offline listening-review follow-up:** the read-only P6 review validator now reports aggregate reviewer assignment concentration (unique declared pseudonyms, maximum distinct alias-pairs per pseudonym, pseudonyms spanning multiple pairs) and repeats where one pseudonym reviews an identical unordered SHA256 source-byte comparison through different aliases. Empty input yields zero counts; pseudonyms and notes are not emitted. Existing per-axis agreement metrics, P5/P6 gates and publishing remain unchanged. Synthetic-only evidence does not authenticate reviewer independence, source rights or audio novelty; distinct hashes can still represent the same song. Focused tests: 40 passed; non-integration regression at that earlier stage: **2,355 passed, 1 skipped, 73 deselected, 15 warnings** (267.04 s); Ruff clean and mypy 453 files passed. Docker/full-chain, real listening panels, rights and live release outcomes were not exercised.

- **2026-10-24 duplicate-byte report integrity follow-up:** the read-only offline review validator refuses conflicting unknown-measurement metadata under the same SHA256, non-1.0 measurable self-similarity for same-byte aliases, and different metric values for repeated unordered SHA256 source pairs. Consistently unavailable measurements remain `null`. This checks submitted report consistency, not source provenance, music rights or audio novelty. Focused audio/review tests: **66 passed**; final full non-integration regression after the type fix: **2,359 passed, 1 skipped, 73 deselected, 15 warnings** (266.63 s), exit code 0. Ruff full scan and mypy (453 files) passed; changed-file whitespace and Python syntax checks passed. Real listening labels, independent source/rights evidence, live P6/MinIO remediation and Docker/full-chain tests remain unverified.

- **2026-10-24 reviewer-overlap diagnostic follow-up:** a new read-only `data_quality.reviewer_overlap` summarizes distinct reviewer-pseudonym pairs that co-rated different-byte SHA256 source comparisons, shared-source coverage, alias-collapsed per-axis agreement/disagreement/uncertain counts, and repeated-alias label conflicts without disclosing pseudonyms. Repeated aliases and same-byte source pairs do not inflate overlap counts; an ambiguous label is excluded from decisive agreement. Co-ratings are dependent descriptive observations, not independent listeners, adjusted reliability, evidence of rights or authorization to publish. Focused audio/listening tests: **68 passed**; final full non-integration regression **2,361 passed, 1 skipped, 73 deselected, 15 warnings** (275.47 s, exit code 0); full Ruff scan and mypy (453 files) passed. The changed Python files passed syntax checks and were re-read. Genuine human reviews, independently authenticated provenance/rights, production MinIO backfill, Docker integration and actual audience metrics remain unverified.

- **2026-10-24 operator-source-reference completeness follow-up:** the read-only P6 offline human-review validator now records only whether an operator-supplied `same_recording_source` reference is nonblank, counting review rows, distinct unordered SHA256 source comparisons, mixed present/missing evidence references, same-reviewer inconsistencies across aliases, and reference-bearing same-byte reviews. No raw references or reviewer identities are emitted; a submitted reference is not independently authenticated provenance, a rights grant, or evidence of actual song originality. Focused synthetic audio/review tests **70 passed**; latest complete non-integration regression **2,363 passed, 1 skipped, 73 deselected, 15 warnings** (247.40 s, exit code 0). Ruff full scan and mypy (453 files) passed, and both changed Python files passed syntax checks. Live audio rights, real listening panels, MinIO backfill and production metrics remain unverified.

- **2026-10-24 local media evidence reconciliation:** Added read-only `scripts/audit_local_album_evidence.py` and synthetic tests. Actual inspection of 34 on-disk WAVs across three selected album directories found: 1456 album 16/16 manifest/clip-file identities matched (but six repeated-title groups across 16 tracks); 1459 folder two WAVs and no local publication manifest; `full_album` 16 local WAVs against 16 manifest rows, but only 10 clip-position filenames matched (six expected/unlisted mismatches at indices 5, 7, 8, 9, 11, 13). Exactly two cross-directory SHA256 duplicate-byte groups involved four files. Local metadata and byte matches do not independently prove rights, source provenance, musical novelty, actual video contents, or even that the separate folder is a published album; no auto-remediation or external action taken. The new synthetic focused tests: 6 passed; latest full non-integration regression **2,369 passed, 1 skipped, 73 deselected, 15 warnings** (246.07 s, exit 0); Ruff full scan and mypy (453 source files) passed, and the new Python files passed syntax checks. Docker integration, real listening, independent rights/provenance and actual YouTube inspection remain unverified.

- **2026-10-25 displaced clip-filename follow-up:** Read-only analysis of the original `full_album` 16-track publication summary and local WAV metadata narrowed all six unmatched *expected filenames* to unique **8-character clip-prefix matches at other filename positions**, with no measured duration mismatch: manifest positions 5→local 4, 7→5, 8→6, 9→7, 11→8 and 13→9. Thus six manifest-specific names remain absent but no manifest clip prefix is missing; this does not verify the full original clip UUID or whether source audio appears in the published video. The archived production script reused cached WAVs by prefix without keeping positional filenames in sync (plausible root cause, original execution logs not confirmed). Its already-disabled paid production entrypoint stays disabled; the helper now fail-closes on displaced cached clips rather than silently reusing them. The read-only album audit now separates unique displaced candidates from absent/ambiguous prefixes and reports duration conflicts. Synthetic focused tests **11 passed**; complete non-integration regression **2,374 passed, 1 skipped, 73 deselected, 15 warnings** (250.59 s, exit code 0); full Ruff check passed, mypy passed on 453 source files, and all three edited Python files passed syntax checks. Docker integration, original provider history and live YouTube/rights verification remain untested. No original WAV, DB, provider, publication or MinIO write was made.

- **2026-10-25 local album-master PCM verification follow-up:** Added read-only `scripts/probe_local_album_master.py` for bounded, time-aligned, gain-normalized waveform-correlation spot checks on two non-transition 2-second sections per track. The local `full_album` manifest resolved all 16 original clip-prefix candidates (including six displaced local filenames) against the on-disk album master: 32 valid waveform windows, correlations **0.716432–0.992708**, maximum measured timestamp offset **0.0004 s**. The 1456 album also yielded 16/16 locally measurable tracks and 32 windows, correlations **0.697260–0.992695** and offset ≤0.0004 s. These observations strengthen *local master audio* evidence only; 64 sampled windows do not prove full-track equality, original rights, actual public YouTube video audio, provenance, calibrated music quality or independent source authentication. Synthetic focused master/inventory tests **15 passed**; latest complete non-integration regression **2,378 passed, 1 skipped, 73 deselected, 15 warnings** (261.93 s, exit 0); Ruff full scan passed and mypy passed for 453 source files. Both newly added Python files passed syntax checks. All original audio, local archives, platform state and production gates untouched.

- **2026-10-25 local MP4 decoded-audio follow-up:** Added `scripts/probe_local_mp4_audio.py` and synthetic AAC/mismatched-source/bounds/privacy tests. This read-only probe decodes two short windows per manifest position from a local MP4 audio stream, compares them with resampled local WAV master segments using a common waveform-correlation function, and reports audio/video/container duration differences without an arbitrary publish/rights threshold. Direct on-disk runs of **both** available album MP4s measured all 16 local manifest positions (32 windows each): `full_album` AAC↔WAV correlation **0.998008–0.999997**, aligned offsets 0.0 s, MP4 audio 3,047.812 s vs video/container 3,066 s (**18.188 s discrepancy**); 1456 album **0.998713–0.999999**, offsets 0.0 s, AAC 2,930.607 s vs video/container 2,950 s (**19.393 s discrepancy**). These are actual local decoded AAC checks, **not** evidence of complete audio, current remote YouTube presentation, independent source provenance, rights or listening quality. Focused synthetic local MP4/master tests **9 passed**; complete non-integration regression **2,383 passed, 1 skipped, 73 deselected, 15 warnings** (256.38 s, exit code 0). The entire Ruff scan passed, mypy passed for 453 source files, and all three edited Python files passed syntax validation. No original media, production approval or external resources modified.

- **2026-10-25 MP4 end-of-stream packet follow-up:** Enhanced the existing read-only `scripts/probe_local_mp4_audio.py` to report bounded FFprobe audio/video **final packet PTS + duration**, number of video packets starting after AAC finishes, and a fail-closed `unknown` when packet-end coverage cannot be verified. Actually measured both local MP4s (not just their duration headers): `full_album` last audio 3,047.812167 s, last video 3,066.0 s, **36 video packets starting after AAC ends**, gap 18.187833 s; 1456 album last audio 2,930.606730 s, last video 2,950.0 s, **38 video packets starting after AAC ends**, gap 19.393270 s. This is evidence of local MP4 video packets continuing after its AAC stream, not direct proof of remote YouTube/player behavior or the renderer's precise root cause. A real synthetic AAC+video-tail test and an incomplete-packet-coverage test were added. Focused MP4/master tests **11 passed**; complete non-integration regression **2,385 passed, 1 skipped, 73 deselected, 15 warnings** (256.18 s, exit code 0); Ruff full scan and mypy of 453 source files passed, and both updated Python files passed syntax validation. No original MP4, WAV, provider, production write or publication state changed.

- **2026-10-25 archived still-image FFmpeg render reproduction and safe handoff:** Recreated the looping-image 2 fps/AAC/`-shortest` legacy FFmpeg path using disposable **four-second** fixture assets, with FFmetadata chapters and with an explicit source-WAV duration cap. All three local renders produced AAC/video/container duration exactly 4.0 s, so the historical 18–19 s video-only packet tail **was not reproduced** by these options; its original cause remains unknown. Added read-only `require_local_render_audio_sync` that requires known original WAV length, matching one-audio/one-video/container timing and actual final-packet evidence, otherwise rejects with a reason code. The `scripts/produce_full_playlist.py` helper (its paid `main()` and direct upload already disabled) now explicitly caps its video duration and refuses to return when that local timing check cannot be satisfied. Real FFmpeg synthetic clean and extra-video-tail fixtures and direct guarded renderer were exercised; focused tests **10 passed**. Complete non-integration regression **2,388 passed, 1 skipped, 73 deselected, 15 warnings** (258.91 s, exit 0). Ruff full scan and mypy on 453 source files passed, and all three changed Python files passed syntax validation. No user audio/video files, DB, provider or external platform state changed.

- **2026-10-25 active P6 video/audio-stream duration QA:** Investigated the current `services/media_factory/rendering/` FFmpeg → post-render QA → C6 package path rather than relying on the archived one-off renderer. Existing P6 QA had checked the **container** duration only: a plausible-duration MP4 whose AAC audio stream ends early could escape this specific check. The FFprobe adapter now preserves each stream's reported duration; P6 QA fails closed with `stream_duration_unmeasured`, `stream_duration_mismatch`, or `video_audio_duration_mismatch` when the audio/video durations are unknown, violate the profile's declared ±0.5 s timing tolerance, or disagree. It rejects even when the container reports the expected duration. A failed QA marks C6 `failed`, and the existing pipeline HTTP assembler requires `ready_for_publish` before the P7 step. Stand-in package-level tests demonstrate refusal and real local FFmpeg/P6 media fixtures demonstrate independently measured stream durations. Focused rendering/media tests **16 passed**, including actual local FFmpeg media. The complete non-integration regression **2,392 passed, 1 skipped, 73 deselected, 15 warnings** (259.97 s, exit 0); Ruff full scan, mypy of 453 source files, Python syntax checks and git diff --check passed. This is local post-render validation, **not** evidence of actual YouTube playback, source rights, a deployed production cycle, or an R6 authorization. No historical MP4s were rewritten and no external writes occurred.

- **2026-10-25 P6 failure-code persistence and HTTP handoff regression:** Confirmed that real authenticated in-process P6 `POST /media/packages/assemble` returns `status=failed` and the exact stream timing QA reason codes (`stream_duration_unmeasured`, `stream_duration_mismatch`, `video_audio_duration_mismatch`), and that a persisted `GET /media/packages/{id}` and idempotent assemble replay preserve those blocks without rerendering. Confirmed the actual pipeline `HttpPhaseSteps.assemble` refuses all three reported failures through its HTTP transport as non-retryable `ValidationFailure` rather than generating a ready package. One test passes the **actual P6 TestClient response JSON** into the pipeline MockTransport, proving shape compatibility without inventing a response. Focused P6 API/pipeline tests **37 passed, 11 warnings**; full non-integration regression **2,399 passed, 1 skipped, 73 deselected, 15 warnings** (260.35 s, exit code 0). The full Ruff scan and mypy of 453 source files passed; the two edited Python test files passed syntax validation. This is an in-process handoff regression (stand-in media probe and mock transport), **not** a Docker/Temporal integration, verified live YouTube upload, production P6 or R6 approval. No external side effects.

- **2026-10-25 P6-to-P7 failed-package API handoff regression:** Extended `tests/unit/test_media_api.py` to fetch the **actual persisted P6 C6 hop-1 payload** after each of three stream-timing failure scenarios and submit it to the authenticated P7 `/youtube/publish` API (in-process TestClients, mock YouTube adapter). In all cases P7 returns HTTP 422 `package_not_ready` and the adapter records **zero calls**; P6's persisted failure and idempotent replay remain intact. Focused tests: **4 passed, 23 deselected, 2 library deprecation warnings**; Python syntax, Ruff full scan, and mypy (453 source files) passed. Complete non-integration regression: **2,399 passed, 1 skipped, 73 deselected, 15 warnings**, 261.96 s, exit 0. The Docker CLI (`docker` 29.8.2) does not recognize the `compose` subcommand, but standalone `docker-compose` v5.6.0 works; its read-only `ps` check showed the local PLM APIs, Postgres, MinIO, Temporal and workers running with healthy status. No Docker/Temporal P6-to-P7 failure-path integration test was run, and deployed code versions were not verified. This does **not** prove deployed P6-to-P7 behavior, real YouTube rejection or production safety. No original media, operating DB, MinIO or external services were modified.

- **2026-10-25 deployed loopback P6 C6 → P7 refusal boundary and Temporal adapter:** Added `tests/integration/test_p6_p7_failed_package_http.py`: on the already-running local Compose stack, authenticated GET reads an existing READY P6 C6 payload, changes **only an in-memory request copy** to `failed` for each of the three stream-timing QA reason codes, then posts it to the actual P7 HTTP API in `dry_run`. All **3 integration cases passed** (20.27 s, exit 0): P7 returned HTTP 422 `package_not_ready` and the exact package's P7 manifest/attempt row counts were unchanged. The local media DB had eight READY packages and zero FAILED ones; therefore this proves **live P7 refusal and unchanged P7 manifest/attempt counts, not a real deployed P6 render failure or absence of all possible DB updates**. Extended `tests/unit/test_media_api.py` to carry an actual in-process P6 failed response through the `HttpPhaseSteps.assemble` **Temporal activity adapter**: `ValidationFailure` was converted to a non-retryable `ApplicationError` at attempt 1 (focused **1 passed**, 2 warnings). Python syntax validation, Ruff full scan, and mypy on 453 source files passed. Complete non-integration regression: **2,399 passed, 1 skipped, 76 deselected, 15 warnings**, 280.92 s, exit 0 (the three new integration tests were excluded from this tier and passed separately). The full Temporal workflow (including persisted FAILED state and absence of P7 execution) and runtime P6 renderer failure injection remain unverified; no live upload, original media write or operator R6 action took place.

- **2026-10-25 actual Temporal server workflow failure after P6 stream-timing QA:** Added `tests/integration/pipeline/test_p6_temporal_failure_handoff.py` and generalized only the existing test HTTP fixture's expected assemble idempotency key (`tests/unit/test_pipeline_http_assemble.py`). An in-process P6 API using stand-in audio/video probing creates genuine P6 `failed` package responses for three missing/mismatched stream-duration variants; the real `HttpPhaseSteps.assemble` consumes each via an in-memory P3/P5/P6 HTTP transport. A dedicated acceptance-queue worker runs the **real PipelineWorkflow on the live local Temporal server**, persisting transitions in PostgreSQL. **3 integration cases passed, 2 deprecation warnings, 17.92 s, exit 0:** all runs ended `FAILED` from `ASSEMBLING`, persisted the expected QA reason code, had `ValidationFailure` non-retryable on attempt 1, and never reached `PACKAGED`, `UPLOADING` or any P7 upload mock effect. Ruff full check and mypy of 453 source files passed, and both test Python files passed syntax validation. **Boundary:** the P6 rendering/probing toolchain, pre-P6 phases and phase HTTP replies are mock/in-process; this is real Temporal execution with controlled phase responses, not a deployed P6 container error or an actual YouTube provider call. The acceptance queue is isolated from the deployed pipeline worker; the test writes only its fresh `ops` workflow/transition evidence, with no paid-provider writes, media file changes or R6 transition. **Final non-integration regression: 2,399 passed, 1 skipped, 79 deselected, 15 warnings** in 261.49 s, exit 0. **Development deployment check:** the mounted P6 `qa.py` SHA256 matched the repo (`a3f897d6...`) but its modification date (Oct 25) was later than the running container start (Oct 22), so the long-running server might have held stale imported code. The local DB had eight `completed` render jobs and none queued/preflight/running. Restarted **only `api-media`** with standalone `docker-compose restart api-media` (exit 0); the P6 container returned `healthy`. The previously added read-only P6→P7 failed-copy HTTP regression was rerun after restart: **3 passed** in 18.37 seconds (exit 0). No PostgreSQL, MinIO, Temporal or P7 service was restarted; no provider upload, media mutation or R6 operation occurred.

- **2026-10-25 deployed-container FFmpeg audio/video-tail regression:**
  Extended `tests/unit/test_media_render_ffmpeg.py` with three four-second, disposable
  MP4 fixtures encoded by **real FFmpeg** and independently read by P6's
  `FFmpegToolchain.probe` and `measure_loudness`. A matched 4s/4s pair passes;
  a 4s video / 2s AAC pair and a 2s video / 4s AAC pair are rejected with
  `stream_duration_mismatch` and `video_audio_duration_mismatch` even when the
  container duration alone remains exactly 4s. Also added a package-level test:
  after a real P6 H.264/AAC render, an intentionally damaged, two-second-short
  AAC stream is remuxed by the test-only FFmpeg subclass, then P6's actual
  `assemble_media_package` returns a `failed` package with
  `RenderJobStatus.FAILED`, `post_render_qa_failed`, and QA reason codes in its
  in-memory render report. All artifacts are temporary or in-memory; no shared MinIO, P5/DB,
  running service or YouTube write is involved. Tests were executed **inside the
  already-running `api-media` container**, with missing FFmpeg forbidden:
  focused stream cases **3 passed** in 8.35s, focused assembler case **1 passed**
  in 11.91s, complete file **7 passed** in 19.47s (all exit 0). The full
  non-integration regression on the host passed **2,403 tests**, 1 skipped,
  79 deselected, 15 warnings in 288.35s, exit 0; Ruff all and mypy of 453
  source files passed. This verifies the installed FFmpeg/toolchain/P6
  assembly safety path, **not** a deployed HTTP API failure injection, a real
  consumer player check or authorization for production/R6.

- **2026-10-25 real FFmpeg failure → P6 HTTP persistence → C6 → P7 refusal regression:** Extended the FFmpeg media suite (`tests/unit/test_media_render_ffmpeg.py`) with a joined, authenticated P6/P7 API test. Three short synthetic recordings go through the real P6 FFmpeg audio/video renderer; a **test-only** encoder subclass remuxes AAC with a 2-second early ending while the MP4 container/video stay at expected duration. The real P6 FastAPI handlers and a private in-memory SQLite media schema persist `status=failed`, QA's `stream_duration_mismatch` and `video_audio_duration_mismatch`, plus a separate `failed` render job with `post_render_qa_failed`. Authenticated HTTP GET re-reads the package, render job and frozen C6 payload; an identical assemble POST returns the stored failure **without re-encoding**. The real P7 FastAPI handler, under an isolated P7 SQLite schema with a mock YouTube client, returns HTTP 422 `package_not_ready`, with **zero provider adapter calls**. The tiny 160×90 profile is injected into only this test's profile resolver; no production profiles change. Focused **1 passed**, 7 deselected, 1 warning, 14.08s; entire real-FFmpeg file **8 passed**, 1 warning, 28.26s, exit 0, executed inside the running `api-media` development container. Full non-integration regression **2,404 passed, 1 skipped, 79 deselected, 15 warnings** in 276.25s (exit 0); full Ruff and mypy 453-source-file checks passed. Boundaries: this is **in-process FastAPI over isolated SQLite**, test-only P5 manifests and memory artifacts, rather than a deployed P6 HTTP process with PostgreSQL/MinIO and a real faulty encoder. No shared DB, MinIO, production media, actual YouTube upload or R6 approval was modified.

- **2026-10-25 legacy P6 QA audit and P7 legacy-READY hold:** Read-only local PostgreSQL inventory found **8 READY media packages without saved FFprobe stream-duration evidence**. This is missing evidence, not proof of broken video. Added `packages/plm_platform/media_timing.py` to compare saved QA status, duration facts, stream timing and a maximum 0.5-second tolerance; P7 now rejects a READY C6 with missing or inconsistent evidence using its existing `422 package_not_ready` response, before persistence or provider calls. `scripts/audit_p6_ready_timing.py` aggregates reasons only, connects only to an explicitly allowed loopback DB and issues read-only SELECT. Test fixture C6 reports were completed, and new positive and negative P7 tests passed (focused 16 passed). Full non-integration regression **2,416 passed, 1 skipped, 79 deselected, 15 warnings** (276.09s, exit 0); full Ruff passed and mypy found no issues in **454 source files** after one typing correction. Re-running the shared read-only classifier returned **8 missing_stream_evidence**. The local P7 DB held eight `verified` attempts and no active publish attempt; restarted **only the development `api-youtube`** container (healthy). On actual deployed P6/P7 loopback HTTP, integration **4 passed in 23.68s**: the three previous failed-copy rejections plus one new test which sends a **real unchanged READY C6 that lacks saved stream timing evidence**. All rejected with HTTP 422; P7 manifest and attempt counts for the tested packages remained unchanged. No original media, P6/P7 stored READY states, shared DB records or MinIO files were modified; no external upload or R6 operation. This checks persisted reports, not independent current MP4-byte QA or the historic MP4 audio-shortfall root cause.

- **2026-10-25 read-only actual MinIO MP4 stream re-probe for all eight legacy READY P6 packages:** Added `scripts/audit_p6_media_streams.py` and `tests/unit/test_p6_readonly_media_audit.py`. The CLI refuses non-local databases/MinIO, requires explicit `--local-dev`, enforces per-object size limits, validates the immutable P6 URI digest and fully rehashes each downloaded MP4 against the stored SHA256 before FFprobe. It streams one video at a time into a deleted temporary directory, prints only anonymous row numbers/timings, performs SELECT and HEAD/GET only and never modifies P6/P7 state. The initial 128 MiB bound refused every video as oversized; a MinIO HEAD-only inventory established actual sizes **428.70–454.58 MiB**. A second run with bounded 512 MiB/object verified **all 8 complete SHA256 files** and probed the real MP4s: four had audio/video/container/timeline **3406.0s each**, four had **3574.0s each**; all eight returned `measured_timing_consistent_only` (no gap beyond profile tolerance). Hermetic boundary, hash and probe unit tests **10 passed** (including fake tampered storage bytes). Full non-integration regression **2,426 passed, 1 skipped, 80 deselected, 15 warnings** in 282.94s (exit 0); Ruff full scan and mypy of 454 source files both passed, and the two new Python files passed syntax validation. **The separate archived historical MP4s with 18–19s video-only tails are different assets and remain anomalous.** These live MinIO timings do not independently establish loudness, full decode, listening quality, source rights or current public YouTube content. Importantly, prior P6 reports still lack persisted stream-duration QA fields, so **P7 remains fail-closed** for these old READY packages until an authorized, auditable backfill/revalidation process is designed; no automatic READY recertification or database write occurred.

- **2026-10-25 durable local evidence snapshot for the eight legacy P6 videos:** Added `scripts/p6_media_evidence.py` and `tests/unit/test_p6_media_evidence.py`; expanded the existing read-only MinIO audit with explicit `--evidence-out BASENAME.json` (refused together with `--head-only`). Re-probed the same eight bounded immutable MP4s, verified each downloaded file's full SHA256 and stored content-addressed digest, and recorded per-asset *declared* video digest, profile, measured audio/video/container/timeline durations, source audit-script SHA256, local FFmpeg version, capture UTC, machine-readable outcomes and a canonical report self-checksum. The report is an **exclusive-create** ignored local file `output_media/audits/p6-ready-streams-20261025-evidence.json`, with `publish_authorized=false`. Offline `python -m scripts.p6_media_evidence --verify p6-ready-streams-20261025-evidence.json` returned **self_checksum_valid=true**, eight `measured_timing_consistent_only`; evidence SHA256 `4b0eeb3d1032786c89dfd52317234f9cd7b9b84ce8ceeff91b3e221e548323af`. Focused evidence and probe tests **18 passed** (including file changes, forbidden output paths and exclusive-create), full Ruff passed and mypy checked 454 source files without error. Complete non-integration regression **2,434 passed, 1 skipped, 80 deselected, 15 warnings** in 290.64s (exit 0). **Self-checksum is not a signature or independent provider proof.** The report is local, not a replacement for P6 post-render QA, rights clearance, full decode or actual YouTube verification; P7 continues refusing the eight old READY records because persisted stream QA remains absent. No media files, database records, MinIO objects or publication gates were changed.

- **2026-10-25 local P6 snapshot-to-current-DB reconciliation (no backfill):** Added `scripts/reconcile_p6_media_evidence.py` and `tests/unit/test_p6_media_reconciliation.py`. The CLI requires `--local-dev`, refuses non-loopback/non-local PostgreSQL, loads a named, bounded local snapshot from the Git-ignored `output_media/audits/` directory, checks its self-digest and uses a read-only SQL transaction. The pure reconciler maps each saved video SHA256 to **exactly one** current P6 DB record and compares READY status, render profile, saved container duration, playlist timeline, measured container/timeline values, audio/video stream duration consistency and <=0.5s tolerance; duplicate digests and missing/changed fields produce explicit **nonapproving** reason codes. This is metadata reconciliation, **not repeat download/FFprobe or a release QA backfill**. Running it against `p6-ready-streams-20261025-evidence.json` and current local PostgreSQL returned **8 `metadata_matched_legacy_p7_hold`**, 0 publication approvals, no DB/media modifications, evidence self-checksum valid with unchanged snapshot SHA256 `4b0eeb3d1032786c89dfd52317234f9cd7b9b84ce8ceeff91b3e221e548323af`. New+previous focused tests **25 passed**. Full non-integration regression **2,451 passed, 1 skipped, 80 deselected, 15 warnings** in 283.97s (exit 0); full Ruff and mypy of 454 source files passed. Existing P7 published-write guard and old-READY missing-QA block remain intact. A matching report proves only linkage to stored bytes measured during that earlier audit and their current metadata identity; it does not independently attest rights, decoded playback quality or authorize R6.

- **2026-10-25 P6 complete-measurement fail-closed QA follow-up:** Before any proposed historical P6 backfill, reviewed the **active** P6 `services/media_factory/rendering/qa.py` for other missing measurements. Found its QA previously treated an **absent true-peak measurement** as safe whenever integrated loudness existed; `NaN` in integrated loudness, video FPS, container/timeline and duration window could similarly bypass normal finite-number comparisons. Updated QA to reject absent/nonfinite true peak as `true_peak_unmeasurable`, absent/nonfinite integrated loudness as `loudness_unmeasurable`, nonfinite/invalid FPS and durations, and nonfinite/inverted timeline windows, with new explicit `expected_duration_unmeasured`/`duration_window_invalid` reasons. Added `tests/unit/test_media_rendering.py` parameterized boundary cases, including a stand-in P6 assembly whose missing true-peak value now leads to a **FAILED package/render job and FAILED C6 handoff**. Focused **29 passed**, real FFmpeg file inside `api-media` **8 passed** in 29.49s, no FFmpeg test skipped. These are additional fail-closed P6 generation checks; they do not turn the legacy eight READY records into passing QA, do not verify full archived media decode or rights, and do not constitute backfill, R6 approval, or external publishing. Full Ruff passed, mypy had no errors in **454 source files**, and full non-integration regression **2,467 passed, 1 skipped, 80 deselected, 15 warnings** in 281.72s (exit 0). Restarted **only the local development `api-media`** container (exit 0; `healthy`) to load QA changes; existing deployed-loopback P6→P7 HTTP rejection integration **4 passed** in 24.15s (exit 0). No other service restart, DB or MinIO mutation, QA backfill or R6 operation occurred.

- **2026-10-25 P6 nonfinite QA report persistence safety and P6→P7 regression:** Added in-process P6 and P7 HTTP regression in `tests/unit/test_media_api.py` using isolated SQLite, test-only music, memory artifacts and a stand-in meter/probe. Five cases inject absent/NaN true peak, NaN integrated loudness, NaN container duration and NaN video FPS. Every failure persists a failed P6 package and render job, can be retrieved as strict RFC-compatible JSON, is replayed without re-encoding, passes through frozen FAILED C6, and is refused by the actual P7 HTTP handler with 422 `package_not_ready` and zero YouTube adapter calls. This newly exposed an additional bug: although QA blocked nonfinite values, `post_render_qa` kept raw `NaN` in its saved metrics and `MediaRenderer` copied an invalid FFprobe container duration into its report, so strict JSON serialization failed for FAILED outputs. Fixed `services/media_factory/rendering/qa.py` to normalize only nonfinite nested metric numbers to JSON `null` (retaining fail reason codes), and `services/media_factory/rendering/renderer.py` to use the positive finite measured duration only, with timeline metadata fallback **solely for a FAILED probe**. Four direct assembly cases now require `json.dumps(..., allow_nan=False)` and assert unknown metrics are `null`; the focused P6 API/render files **65 passed** (11 warnings), and the actual FFmpeg media suite **8 passed** (1 warning, 28.49s) in `api-media`. Full Ruff and mypy 454-source-file checks passed; full non-integration regression **2,476 passed, 1 skipped, 80 deselected, 15 warnings** in 285.34s (exit 0). Local deployment follow-up (same date): restarted **only** `api-media` via `docker-compose restart api-media` (exit 0), then checked `docker-compose ps api-media` returned **healthy**. In the restarted container, four strict-JSON invalid measurement regressions passed in 4.92s, and deployed loopback P6→P7 HTTP refusal integration again passed **4/4 in 24.33s**, including the existing legacy READY missing-QA hold. **The deployed service was not injected with a synthetic broken meter**; the injected cases ran only in isolated TestClient and standalone container unit tests. Boundaries: older P6/P7 saved metadata and media objects were not changed, old READY package publication hold remains, no R6 approval or external publication.

## Test baseline (2026-10-15, R5)

| Tier | Command | Result |
|---|---|---|
| Unit, contract, e2e, simulation | `pytest -m "not integration"` | 1991 passed, 1 skipped (test_cli STUB_CASES), 68 deselected (integration/full-chain), ~185 s (R5). Hermetic since R0's follow-up: no `.env` or service variables are visible to the unit tier, matching CI. |
| Media (dev image, FFmpeg 7) | `docker compose run --rm -e PLM_REQUIRE_MEDIA_TOOLS=1 worker pytest -m media` | 6 passed (R4); in CI a missing tool fails |
| Gate R5 full chain | `pytest -m full_chain` (own CI job, 90 min timeout; `-m integration` skips it) | 8 passed (run 1: 2886 s, run 2: 2843 s); two consecutive green runs on the compose stack |
| Integration, compose up | `plm migrate` then `pytest -m integration` | R5: 60 passed, 8 skipped locally against the full compose stack. R4: 50 passed locally against the full stack. R3: 42 passed locally against the full compose stack (twice), including the Gate R3 pipeline and outbox tests. Green in CI on 2026-10-14 for R0, R1 and R2: migrations `0001`–`0024` upgrade, downgrade and re-upgrade on Postgres 16, and the schema matches the models. MinIO is Chainguard's build pinned by digest. |
| Lint | `ruff check .` | clean |
| Types | `mypy packages services` | clean (439 files) |
| Cross-process determinism | `tests/unit/test_cross_process_determinism.py` | P2 draws, P3 design/package, P4 plan/seeds/ids/artifact digests identical under two `PYTHONHASHSEED` values |

Before R0:

- **The W6.1 loop-closure test failed.** It passed only before 12:00 UTC on the day it
  was written: P9 stamped `observed_at` with the wall clock, and P2 ignores observations
  later than its pinned cycle clock. R0 fixed it by injecting the clock. Removing
  wall-clock reads across services is R2.
- **The integration tier hung indefinitely when compose was down**, because postgres
  connects had no timeout. R0 added a 5 s connect timeout and a 120 s per-test timeout
  (`pytest-timeout`).
- **The known skip** is `tests/unit/test_cli.py:57`. Its parameter list `STUB_CASES` is
  empty because no stub CLI commands remain, so the test has nothing to check.

**What the green suite does and does not prove (after R1).** Every seam contract test
now runs the real producer into the real consumer, and refuses every missing,
placeholder, unknown-field and future-major payload. That proves the hand-offs, not
the pipeline.

The e2e test still has these gaps:
- It runs in process on SQLite, with no HTTP, Temporal or Postgres.
- Its P1 opportunity and P2 directive are hand-built.
- Its P3 experiment is hand-built (its P5 gate is now real: the mock's audio passes the
  declared duration range, ADR-0032).
- Its P9 revenue figures are literals. The reward is posted at the declared 28d horizon
  and is the one effective outcome P2 applies (ADR-0031).
- It never runs a second allocation cycle.

R5 replaces it.

**Evidence for Gate R5 (2026-10-15).** `tests/integration/test_r5_full_chain.py` runs on
the compose stack with no hand-built hand-off. Operator acts are its only direct writes:
the P2 policy (P2's repository, as no route exists), the P7 channel (`PUT
/youtube/channels/{id}`), P5's controlled references, and resetting P2's learning state
for the two P1 CI arms. Pinned mock arm effects in `config/dev/mock_outcomes.json` make
`lofi_study_focus` the stronger arm, although P1 scores it lower.

Proven by Gate R5 (two consecutive passing runs, 48 min and 47 min; 8 of 8 checks passed):

- Both runs reach LEARNING, and every instant they hand a phase is `workflow.now()`.
- The strong arm earns the larger proxy reward. Its posterior rises above its prior and
  above the weak arm's.
- The learning cycle's expected-reward gap and the Thompson first-rank probability move
  toward the strong arm.
- Rewards are DELIVERED through P9's outbox.
- A re-dispatch, a replayed payload and a second update change nothing.
- A preflight failure writes nothing to P7.
- Each playlist lies in its declared range, and no candidate matched P5's catalog of
  earlier runs.
- One SQL query joins every hop by stored ids: P2 update → P9 reward, outcome and outbox
  → P7 → P6 → P5 → P8 (and P9's release links) → P4 → P3 → P2 directive, decision,
  decision log and snapshot → P1 score, arm, raw and normalized observations.
- Lineage joins at every hop without missing identifiers.

It does not prove economic optimality (the economics gate), real providers (R6), or
anything about a real audience: the outcomes are the mock model's.

**Open issues found by R5 (owner in brackets):**

- P1's C1 `evidence.top_observation_ids` are raw observation ids, while P1's
  `GET /market/v1/observations/{id}` resolves normalized observation ids, so the ids P1
  publishes do not resolve through its own API. The lineage joins through
  `market_raw_observations`. Changing what the field means needs an ADR (C1 is frozen).
  [P1, next contract change]
- P1's taxonomy sets `duration_bucket` only from title text, never from the measured
  video duration, so its arms (and P2's directives, which copy the arm) usually name no
  bucket and P3 applies the policy range. Deriving the bucket from the measured duration
  changes arm identity (an identity-bearing dimension), so it needs its own change.
  [P1]
- The deployed `PipelineOptions` defaults (600 s per step) are shorter than a
  production-scale QA step (about 200 evaluations) or a multi-package assemble. The gate
  runs with 3600 s. A heartbeat already detects dead workers, so a longer default (or a
  per-step bound) is safe. [platform, R6]
- P6 renders inside the HTTP request. The pipeline's 570 s read timeout expires on long
  playlists, and the retry then waits on P6's render lock and replays. A durable render
  job is later work. [P6]
- P5's internal-catalog match still scans every released fingerprint, so QA time grows
  with each run on a shared database (about 1.2 s per candidate at 700 releases).
  [P5]
- The 7 GB Docker Desktop VM on the dev machine stalled twice under the full chain's
  load, and P6 was OOM-killed before the R5 memory fixes. [environment]

## Detector limitations (R4, ADR-0034)

Audio fingerprints and lyric similarity are risk signals, not proof of originality.

- The fingerprint finds a reused recording (re-cut, re-gained, lightly noised,
  resampled). It does not find covers, re-performances, time-stretch or pitch-shift, or
  copied compositions.
- Lyric matching is lexical: it misses paraphrase and translation.
- Both see only their references, and no reference set or corpus is loaded yet.
- Voice cloning is caught only through declared voice inputs or impersonation wording in
  the prompt; there is no acoustic voice-clone detector.
- Thresholds were set on synthetic fixtures; no error rates have been measured on real
  music.
- A detector that is unavailable or inconclusive returns `UNKNOWN`, which routes the
  candidate to quarantine; it is never treated as clearance.
