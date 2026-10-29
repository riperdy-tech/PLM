# B02 — P6 durable render recovery: verified code gap and gate plan (2026-10-28)

**State: PARTIAL / HTTP pre-render claim connected, recovery still open.** The original gap below is retained as historical diagnosis. The HTTP path now commits intent and its claim before rendering (see follow-up), but there is no autonomous durable worker or proven crash/restart recovery.

## Source-verified behavior

- `services/media_factory/api/routes.py` builds a `threading.Lock()` inside each API router (line 79), then holds it around the synchronous `assemble_and_persist()` and `session.commit()` (lines 133–144). This serializes render calls **inside that one router process**, not across workers, containers, restarts or host failures.
- `services/media_factory/rendering/assembler.py` calls `MediaRenderer.render()` before it can return `AssemblyPipelineResult` (lines 202–225). An existing media package is returned only when a prior complete package is found (lines 163–168).
- `services/media_factory/rendering/renderer.py` constructs the `RenderJob` at the **end** of rendering and output upload, after audio/video generation (roughly lines 180–237).
- `services/media_factory/service.py` calls `insert_render_job()` and `insert_media_package()` only **after** `assemble_media_package()` returns (lines 49–64 and 86–90); `routes.py` commits the request session after this. On process death while FFmpeg runs or after writing blobs but before the database commit, the durable job state is absent and another worker has no cross-process claim to check.
- A `render_jobs` table exists with fields for job status, attempt count, worker ID, start/end and input checksum, but no pre-render claim/lease/recovery protocol is called on the request path. A GET `/media/renders/{id}` exposes completed records, not a durable queued/working lifecycle.
- Media rendering may write external artifacts before the SQL commit, so a retry can leave orphaned artifacts or run the render twice. Current process-local locking and completed-package replay reduce ordinary duplicate calls but do not prove process-crash recovery.

## Requirements before B02 can close

1. Use a persisted input identity (manifest hash + package/profile/version) and a DB-enforced unique job/attempt policy to create **QUEUED** intent before any expensive render or blob write. Write this in a committed transaction separate from the long render.
2. Claim one job with an atomic lease/fencing token in PostgreSQL; concurrent workers must not both enter FFmpeg for the same input. Define heartbeat, deadline, safe expiry and bounded retry policy. A stale worker must not publish outputs after a new lease claims the job.
3. Record durable transitions such as QUEUED → RUNNING → COMPLETED/FAILED/RECONCILIATION_REQUIRED. Never turn unknown work into a successful package; preserve exact failure reasons.
4. Stage artifacts with deterministic keys/checksums and publish only after durable verification. Reconcile possible partial blob uploads after restart, without overwriting unrelated or previously published output.
5. Keep existing package IDs, rights lineage, `C6` payload and post-render QA gating intact. Treat any schema/API changes through the appropriate migration/ADR and contract review. No connected-DB migration without separate authorization.
6. Run fault injection at **before start**, **during FFmpeg**, **after blob upload/before SQL commit**, and **after SQL commit/before HTTP response**. Prove no lost jobs, no duplicated final media, bounded restarts, and correct hold/QA on corrupt output. Include two-worker PostgreSQL contention and an actual worker termination/restart against disposable isolated infrastructure.
7. Validate configured render duration/resource limits under a long representative playlist and run the same-revision Compose/Temporal/MinIO/FFmpeg full-chain gate twice. Mock short renders are not this evidence.

## Code-only partial foundation (2026-10-28)

The existing `render_jobs` table supports a strictly bounded storage layer
without a schema or C6 change. `storage/render_claims.py` also now contains a pre-render `queue_render_job` insert that checks the deterministic job ID and SHA256 input hash; an existing key with different immutable inputs is rejected, while an identical replay does not reset execution state. The original queue identity/replay tests passed in a 45-test P6 suite (job `98f926540e7d431a321334a59d511bda`). A newly added outer-transaction rollback test then exposed a real SQLite legacy-SAVEPOINT bug: a queue row could survive the caller rollback. The helper was changed to dialect-specific atomic `INSERT ... ON CONFLICT DO NOTHING RETURNING id` in the caller transaction, with no nested savepoint. The focused P6 suite then passed **46 tests** (job `68bd89b5ab9529167b71e363fb6f0d87`); repository Ruff and mypy (464 files) passed. PostgreSQL transaction/race proof remains outstanding. The same module exports:
`claim_queued_render_job` (one conditional SQL UPDATE from QUEUED to
RUNNING, bound to job ID, exact input manifest hash and worker) and
`finish_claimed_render_job` (a terminal conditional UPDATE requiring
matching worker, original start timestamp and input hash). Completed work
requires a SHA256 output checksum; failed/quarantined work requires an
explicit reason. These operations do not implicitly commit and cannot
reopen completed or failed jobs. The caller must **separately commit** a
QUEUED intent, the claim, and finally the result before treating it as
durable. In-memory SQLite tests demonstrate that incorrect owners,
inputs, states and result evidence are refused: the new storage/API
focused suite passed **43 tests**. Ruff and mypy outcomes belong in
`docs/STATUS.md`.

The rollback-fix non-integration regression subsequently passed **3,126
tests** (one skipped, 80 integration deselected; job
`2cb3f52d0694f5045f398b3ae952a1f3`). An additional code-only
`quarantine_stale_render_job` CAS transition now preserves the old
RUNNING worker identity but marks overdue work QUARANTINED with an
explicit reconciliation reason. The cutoff must be supplied by the
recovery supervisor, which is **not yet implemented**. This action
does not kill the old process, reclaim the job, or authorize a blob
write. The old worker's terminal UPDATE loses the race after quarantine,
and completed jobs cannot be reclassified by it. Two SQLite-focused
tests were added: **48 P6 storage/API tests passed** (job
`1eab11504bcfaf12746c52fb1df56314`); Ruff, mypy (464 files)
and Git diff checks passed (job `7c9388347863a43ab7832da06cc343e2`).
A new opt-in PostgreSQL integration file
`tests/integration/test_media_render_claims_postgres.py` now defines
four scenarios: caller rollback, concurrent duplicate registration,
two-worker claiming and competing completion/quarantine. It requires
`PLM_RENDER_PG_ISOLATED=1`, a loopback PostgreSQL endpoint and permission
to create/drop its own randomly named disposable database. This test
**passed all 4 cases against an isolated disposable local PostgreSQL
database**, exit 0, 17.51s (job `127223819e0de2e75c68fcd5446a3a81`).
The storage-change full non-integration regression passed **3,128,
1 skipped, 80 deselected, 16 warnings**, exit 0 (job
`bbf43ad51d275348e69a7b605ca9011e`). Repository Ruff, mypy
(464 source files) and Git diff checks passed after the integration
file was added (job `8cf748c6d9879e11c268a0d18499d6ca`).
The four PostgreSQL checks prove SQL ownership/transactional races
only; there is no process-kill, lease expiry, FFmpeg or blob proof.

A later **bounded read-only recovery scan**,
`list_stale_render_job_candidates`, can identify RUNNING claims whose
`started_at` is at or before a caller-supplied cutoff. It orders by
start time and ID, rejects naive time and limits outside 1–500, and
never changes state. A supervisor must independently confirm worker
liveness and artifact state: elapsed time is not a valid lease. The
SQLite P6 suite passed **49 tests** (job
`ab33805d72cfa93be720bb8016b239f6`); the same four disposable
PostgreSQL integration cases passed **again** with scan assertions
(job `f15f8c7ac8728a2898ce2ec496edea3a`). Ruff/mypy 464
sources/diff check passed (job `41ece8f3886c5e75e0672dd708094675`).
Full non-integration post-scan regression **passed 3,129 tests,
1 skipped, 84 integration tests deselected, 16 warnings**, exit 0
(job `812d587615b7ca7f50fc1f0a0dad0372`). This still does not
exercise an FFmpeg process kill or production-length resource load.

## HTTP pre-render committed claim (follow-up)

The synchronous `/media/packages/assemble` path now supplies an independent
render-session factory to `assemble_and_persist`. After clearance, source
verification, sequence and texture checks, the assembler calculates the
**exact renderer manifest SHA256** from the validated track checksums and
visual plan without loading source audio an extra time. The service commits
a QUEUED intent in a separate transaction, then commits the atomic RUNNING
claim under a unique worker identity *before* the normal renderer runs.
A conflicting or previously claimed job returns **409**
(`render_job_reconciliation_required`); it does not launch a second
render. The result's checksum, worker identity, start timestamp and input
hash are fenced in the original package transaction. If a stale claim was
quarantined mid-render, the fenced completion fails and **no media
package/C6 is committed** (409 `render_ownership_lost`). If the renderer
throws, the RUNNING record survives for reconciliation, not automatic
retry. The deployed P6 HTTP and CLI entry points both supply the
independent session factory and a unique worker-token factory at the
environment edge. Direct in-process callers **without** either factory
retain the old insert-after-render behavior.

Focused SQLite P6 tests passed **51 tests, 11 warnings** (job
`9184f4810d03be9293540ca5e570d1a2`). Includes injected exception
after intent commit, refusing duplicate retry and quarantine-vs-completion
fencing; the original bounded source-reading test still passes.
The first full regression found one real static determinism failure:
service-layer `uuid4()` (3,132 passed, 1 failed, job
`c7f082c3be6ef62fd58e74e747c72ba0`). The worker ID factory
was moved to the HTTP/CLI edges. Corrected focused P6/determinism
**53 passed** (job `61c04370674a9cf13d655f298675dfb6`).
Disposable localhost PostgreSQL **5 integration tests passed**
(job `e6cf6200fe311ad5325adca4162d7ac9`), including real
SQL transactions along the full P6 HTTP claim, terminal package
commit and replay path, with **stand-in** media tools (not FFmpeg).
Repository Ruff, mypy **464 files** and diff-check passed
(job `4f4c60efb90e976eea293c69a48a7773`).
Full latest non-integration **passed 3,133 tests, 1 skipped,
85 integration tests deselected, 16 warnings**, exit 0, 299.94s
(job `64af3a961ed60503c96b5d19a5503ba8`). This proves
in-process wiring and non-integration regressions, not real process
kill/restart, artifact reconciliation or sustained deployment safety.

**Still open:** the HTTP endpoint remains synchronous and holds its
process-local lock for load limiting. No autonomous worker polls jobs,
no heartbeat or trustworthy lease expiry exists, and QUARANTINED jobs
cannot be safely reclaimed without checking worker liveness, blob
outputs, and the old worker's ability to continue publishing. Real
FFmpeg interruption/restart, orphan-blob reconciliation, production
resource tests and twice-repeated Compose full-chain proof remain open.
Do **not** call this complete durable recovery. No connected/shared DB
migration, live media publication or bulk cleanup was authorized.

## Read-only operator render history (2026-10-28)

New `plm media render-jobs --package-id <exact-id> --limit 20` is
registered through both the platform `plm` CLI and P6 CLI. It queries
only the selected package's persisted render rows in descending
creation/ID order, capped at 100. Results include persisted status,
attempt number, profile, timestamps and reason code. A `RUNNING`
row does not prove a live FFmpeg process, and no rows does not prove
that no render work happened; it cannot revive or reconcile jobs.
It does **not** create a render, call MinIO, mutate a session, or reset
an older claim. This is operational visibility only, not recovery.

The first focused test run caught the missing platform command wrapper
(21 passes, 2 failures); after correction **23 focused unit tests
passed**, job `075698279eb56133bffb546c169e5ada`. Ruff,
mypy (**464 files**) and Git diff check passed after correcting
export ordering, job `02e420653a207d0d54427a77c42264bb`.
Full repository tests and live PostgreSQL/FFmpeg/Compose have **not**
been rerun after this small change because concurrent user video
production may be active. No shared media/database writes were made.

## Stale-job quarantine now requires termination assertion (2026-10-28)

An aged RUNNING record does not mean its worker has stopped.
`quarantine_stale_render_job` no longer accepts only a cutoff and
input hash. Its required `expected_worker_id` and strictly true
`worker_termination_confirmed` precondition prevent age-only
quarantine, and the SQL conditional update matches the exact worker ID.
A mismatched worker, job state, input hash or cutoff never changes the
record. The method itself cannot determine worker liveness: the
caller-provided termination assertion must eventually be backed by
independent process/supervisor evidence, not simply a timer or DB row.
Quarantine blocks stale terminal package publication but **does not**
kill FFmpeg, reclaim the job, remove blobs or authorize retry.

With simulated termination assertions, **52 P6/API/runtime unit tests
passed** (job `edce0251124f336d849b801c3921bcf9`), **5 isolated
disposable PostgreSQL tests passed** (job
`b108177d47a8eb140d0866b95b9a45eb`), and Ruff, mypy
(464 sources), Git diff-check passed (job
`a6860bba3009f5d7303307bfa3ab9a22`). No actual FFmpeg kill,
restart, artifact reconciliation or full Compose test was run. The
latest full non-integration pass (3,166) predates this edit; no shared
DB or user video work was altered.

## Quarantine execution-identity strengthening (2026-10-28)

A known worker identity alone is not a unique execution instance
when a later supervisor may restart a worker using the same identity.
`quarantine_stale_render_job` now also **requires a timezone-aware
`expected_started_at`** from the observed claim. The atomic SQL
transition matches both worker ID and exact run start, in addition
to manifest hash, RUNNING status and cutoff. An old termination
attestation cannot fence a newer start under the same worker ID.
This is the same execution-token principle as the completion fence,
but still **not** external proof that the process was terminated.

The initial focused run revealed a SQLite fixture using a naive
read timestamp (51 passed, 1 failed, job
`6bf3df2c12794c9bcf629d2164f1849d`); the fixture now uses
the original injected aware UTC start while confirming the stored
instant. After correction, **52 focused tests passed**
(job `f72b26d3bfe3deee3108c610e7adfb57`),
**5 isolated disposable PostgreSQL tests passed**
(job `2e842b5e6a284ce167b3dbca3e2f3db2`), and
Ruff, mypy (464 files) and diff-check passed
(job `f6f60202595985718044b044ef3a1a21`).
No shared DB, actual worker termination, FFmpeg, artifact
reconciliation or current full Compose gate was exercised.

## Per-execution artifact isolation (2026-10-29, code-only partial fix)

The P6 HTTP/CLI claim path now returns a SHA256 namespace derived from
(job ID, worker ID, manifest SHA256, timezone-normalized UTC start) using
unambiguous JSON serialization. The assembler passes it to the renderer
and thumbnail generator. All new master audio/video, background and
thumbnail keys reside under `claims/<namespace>/` per execution; C6
continues to reference the actual content-addressed stored objects.
A later distinct owner/attempt cannot share the earlier run's keys even
if a killed worker still writes its orphaned outputs. Direct in-process
legacy render calls with no committed claim retain their existing keys.
The namespace can be recomputed from a separately validated, timezone-aware
claim record for inspection, but is **not** a lease, worker termination
attestation, or permission to delete/republish orphaned media.

Before the namespace helper refactor, 86 P6 render/API/claim/history tests
passed (job `474072a48d2928966b05b79e30cc3abe`; lint failed for a
string-join style issue). After refactor, 21 focused tests passed
(job `a993023e9fd590046cc686b4ee80b616`; lint found one test style
issue). Both were corrected, followed by 2 focused/API tests, Ruff,
mypy 464 sources and Git diff check passing (job
`27c819733e24ae8a2bb19d5d7956b809`). Isolated PostgreSQL and real
FFmpeg/MinIO kill/restart were not exercised this turn. See `docs/STATUS.md`.

## Bounded read-only claim object inventory (2026-10-29)

`plm media render-inspect --job-id <exact-id> --brand-id <hint> --limit 20`
is now registered through the P6 and platform CLIs. It looks up the exact
render row, recomputes its execution namespace only with a persisted
worker ID and timezone-aware start, then calls the MinIO LIST operation
under exactly two prefixes: the matching render masters and thumbnails.
The listing is globally bounded to 1..100 objects and reports truncation;
no byte download, checksum verification, database mutation, deletion or
retry occurs. Each returned SHA256 is **only the object-key claim**.
A persisted package brand and namespace must match. If no package row
survived a crash, the explicit brand ID is just an **unverified path hint**;
an empty listing does not prove no orphan exists. SQLite can return timezone-naive
stored starts: these are refused rather than silently assumed UTC.
This provides inspection evidence, not supervisor liveness, clearance,
output completeness or safe-reclaim authorization.

The initial 8-case new suite plus 22 render-history/claim tests found a
missing platform CLI registration (**29 passed, 1 failed**, job
`d0b541b7b6befd9bfa8aa0d579dbc093`); after adding the wrapper,
**30 tests passed** (job `7c65c31f08f8adfd8aeded6b473f24e6`). A Ruff
`__all__` ordering issue was corrected and Ruff, mypy **465 sources**,
Git diff-check passed (job `57345ddb424518027ad9668630b4c765`).
Tests used a fake MinIO and SQLite, not live MinIO, actual FFmpeg
interruption, automated recovery or an isolated R5 full-chain run.

## Bounded reconciled retry staging (2026-10-29, code-only partial)

`queue_reconciled_render_retry` uses one SQL `INSERT ... SELECT ... ON
CONFLICT DO NOTHING` to create a new QUEUED attempt with the same
package, render version/profile and input hash as a specifically fenced
QUARANTINED `render_run_stale` predecessor. It rechecks the original
worker and start time, the quarantine completion timestamp, the absence
of a published package, and the absence of newer attempts. An explicit
bounded maximum attempt count prevents unbounded repetition. All three
supervisor attestations (terminated process, orphan-artifact review,
input re-verification) must be True before the statement runs. **They
are caller-supplied booleans, not independent or persisted evidence.**
No HTTP/CLI endpoint authorizes retry and no automatic executor starts.

A separately supervised caller may pass the ID of a previously staged
QUEUED retry to the internal `assemble_and_persist` path. The service
verifies the actual assembled input hash, render profile/version and
package ID before atomically claiming the preexisting attempt; the
renderer and terminal fence use the same attempt ID. The first retry
simulation exposed a real input-identity omission: playlist concept
changes affect title/thumbnail content, but were not included in the
render manifest. Both preclaim and preflight/finish now include the
concept in the fingerprint. Prior direct rendering without a supplied
concept preserves its historical fingerprint behavior. There is no
claim that old production jobs can be automatically migrated or replayed.

Initial targeted P6 run: 103 passed, 1 failed on the changed-concept
regression (job `32e35f24fcd10f1257b2e092e52799e9`). After the fix,
16 retry-focused cases passed (job `cba3f271cd2dfb386a7f0a888a89a467`).
Expanded P6 suite: 106 passed; mypy found two typing issues (job
`2f75fa6b64363600b2aa6d7615a4587b`), both fixed. Final 18 focused
retry tests plus Ruff, mypy 465 sources and Git diff-check passed (job
`ca74387187a099eff83f7ad576bf430f`). This covers SQLite and stand-in
media only, not a real PostgreSQL supervisor race or FFmpeg kill/restart.

## Retry-authority hardening (2026-10-29, code-only partial)

A regular `queue_render_job` call previously accepted any deterministically
named attempt number, permitting a hand-constructed attempt 2 to skip the
supervisor-only `queue_reconciled_render_retry` checks. This is now refused:
the generic queue records initial attempts only. The history test seeds a
legacy/abnormal second row via the test-only repository insert so its
read-only history requirements remain covered without weakening runtime.

In addition to the previously atomic retry staging, claiming a QUEUED
job now SQL-checks that (1) no persisted package already owns its output,
(2) no higher attempt exists for its package/profile and (3) any attempt
above 1 has a matching, finished, quarantined `render_run_stale` immediate
predecessor with identical input digest and render version. Terminal
completion checks the first two facts again while checking the original
worker/start/input identity. This blocks late output publication through
an older worker if another attempt or a stored package appeared between
claim and finish; it does not prove the original worker was terminated.
These are conditional SQL fences, not a new scheduler or retry approval.

**81 focused P6 API/claim/retry/history tests passed** (11 dependency
warnings); changed-file Ruff, mypy 465 source files and Git diff-check
passed (job `6711db43c5387cc91fe3f7761461371d`). Tests use SQLite
and stand-in backends, not real PostgreSQL concurrency, supervised FFmpeg
failure, MinIO reconciliation, or an R5 production-equivalent chain.
The unverified supervisor attestation and cleanup concerns remain open.

## Next implementation boundary

Provide a separately supervised render worker, durable lease/heartbeat,
and **verified orphan output reconciliation** and fencing at publication.
Test actual kill/restart on isolated PostgreSQL+MinIO/FFmpeg infrastructure;
per-execution namespaces isolate blobs but cannot by themselves prove
safe reclaim, retry or complete B02 durable recovery.

## Persisted recovery evidence and owner-observed exit (2026-10-29, Claude takeover, code-only partial)

Reviewed the retry-authority code above before continuing. Found and fixed:

1. **Caller booleans were the only recovery authority.** `quarantine_stale_render_job`
   took `worker_termination_confirmed`, and `queue_reconciled_render_retry` took three
   booleans. Replaced by an append-only `media.render_recovery_evidence` table
   (ADR-0043, code-only migration `0042_media_render_evidence`). Evidence is inserted
   with `INSERT … SELECT` bound to the exact claim identity and an allowed status, so
   it cannot be recorded for another execution. Quarantine requires a
   `worker_termination` row; retry staging requires `worker_termination` and
   `orphan_artifacts` rows for the predecessor claim, all inside the same conditional
   statement. Supervisor attestations need an `evidence_ref`; an inventory needs a
   complete, counted, non-truncated listing.
2. **An in-process render failure left RUNNING forever.** The claim owner now records
   `owner_exit_observed` (exception type and code) and quarantines its own claim in a
   separate session, then re-raises. A failure to fence never masks the original
   error. Interrupts (`BaseException`) and killed processes are not covered: those
   claims stay RUNNING for a supervisor.
3. **Positional `INSERT … SELECT` in retry staging** depended silently on column order;
   now name-keyed.
4. **`plm migrate` code-only refusal was a repeated literal head list** in three
   places; any new head would have lifted the refusal. Now one `_CODE_ONLY_HEADS`
   constant plus a test that the checkout's actual head is listed.
5. Rehearsal also caught an over-long revision ID (35 > `alembic_version`
   VARCHAR(32)); renamed before any use. The existing layout test would also have
   caught it.

Verification (all in this session, worktree `plm-handover-audit-277422`):
- Disposable PostgreSQL 16 container on loopback `127.0.0.1:55432` (tmpfs, not the
  shared dev DB): the B02 suite `tests/integration/test_media_render_claims_postgres.py`
  **10 passed** — the 5 previous cases updated to the evidence API, plus concurrent
  evidence insert (one row), quarantine without evidence refused, two concurrent
  supervisors stage exactly one retry with every column correct, old attempt cannot
  finish once the retry is claimed, and HTTP owner-exit fencing with real
  `timestamptz` equality.
- Same container, database `plm_migration_disposable_b02`: `alembic upgrade head`
  from base through `0042` passed; table, columns, nullability and indexes match the
  ORM; downgrade **refused** while an evidence row existed; empty downgrade and
  re-upgrade passed. Base-to-head on an empty DB only, not a clone of real data.
- SQLite unit tests: see STATUS.md 2026-10-29 entry for counts.
- An independent review of the diff led to: fencing post-render persistence
  failures too, after rolling back the failing request (otherwise a PostgreSQL
  self-deadlock on the job row, confirmed by a mutation check of the new PG
  test); refusing inventories taken before quarantine or empty under an
  unverified brand hint; clock-skew-safe termination CLI. B02 PG suite then
  11 passed.

Still **not** done: independent supervisor process, heartbeat/lease, verification of
termination for killed processes, MinIO byte-level inventory recorded as evidence
(the `render-inspect` command still only prints), real FFmpeg kill/restart, compose
R5. B02 stays partial.
