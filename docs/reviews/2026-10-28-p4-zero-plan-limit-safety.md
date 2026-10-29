# B03 — P4 zero-plan-limit external-write refusal (2026-10-28)

**Scope:** A–B audit remediation, safety-only code changes in the connected PLM checkout. No new DB migration, external service calls, live-account writes, or database writes on the connected development DB. This is a **partial** B03 fix, not plan-budget completion.

## Defect and minimal systemic fix

P4's stored `GenerationPlan.budget_limit` and `credit_limit` were not consulted before calls to an authorized live music provider. A funded `ops` *global/capability* ledger reservation is not proof that a specific P4 plan has a nonzero allowance; a plan explicitly set to zero could still initiate an external paid generation.

The common `ExternalWriteGuard.authorize_external_write` now accepts optional `preflight_refusal` (default `None`). This allows upstream policy checks to **tighten**, never override, normal capability authorization and budget reservation. A nonempty refusal yields a recorded `DENY` before reserving budget or invoking the provider. Existing callers that do not supply a refusal retain the original decision policy.

The P4 generation engine checks **real-provider** plans before the guard:
- `budget_limit <= 0` → `plan_money_budget_exhausted`
- `credit_limit <= 0` → `plan_credit_budget_exhausted`

The normal write guard records the precise reason. Mock runs remain independent of real-world spending, and authorized positive-budget calls still pass through the global ledger guard as before. Pydantic `GenerationPlan` now refuses nonfinite limits (NaN, +Inf, -Inf), preventing unbounded or invalid accepted policies.

## Actual verification

- Focused engine/planner/guard run `plm-AB-B03-20261028-plan-zero-limits-focused-01`: **19 passed**, exit 0, no live provider called.
- Focused combined guard/engine/domain run `plm-AB-B03-20261028-zero-plan-guard-tests-02`: **53 passed**, exit 0.
- After six nonfinite-value model tests, `plm-AB-B03-20261028-zero-plan-guard-focused-03`: **52 passed**, exit 0. Includes tests verifying **no provider calls**, **no budget reservations**, and exact guard reason with a zero plan money/credit limit even when the global fake ledger funds every attempt.
- Whole-repo Ruff: **PASS** (`plm-AB-B03-20261028-zero-plan-ruff-01`). Mypy `packages services`: **PASS, 463 files** (`plm-AB-B03-20261028-zero-plan-mypy-01`).
- Full post-change non-integration regression **3,066 passed, 1 skipped, 80 integration tests excluded, 15 warnings** in 296.54s, exit 0 (request `plm-AB-B03-20261028-zero-plan-full-nonintegration-01`, job `95d51b52c938093ad31e415a728d3274`). The Compose R5 full-chain and long-duration failure/restart gates were **not** run in this work package.

## Follow-up: historical paid-credit ceiling and unknown-usage refusal

The P4 engine now checks verified credits from both previous stored and
current-run successful attempts before the next real-provider write. With
missing/reference-free paid usage it issues
`plan_credit_usage_reconciliation_required`; with reported credits
already at/over the plan ceiling it issues
`plan_credit_budget_exhausted`. This refusal is passed to the normal
external-write guard before its shared ledger reserve and payment.
It never converts credits into currency, and never invents provider
receipts. In-process fake-provider tests cover same-run prevention,
persisted replay and absence of an extra reserve: **43 passed** (job
`a22ddd9d0550fdaae79d16e8a2d685c5`). Ruff, mypy 464
source files and diff checks passed (job
`9fa27ba3c13a8a0f5ee95debc65ed286`).
The latest full non-integration regression is running (job
`6f0a43d8ada5f489b85042d12603e335`). There is no valid
per-call credit estimate, concurrent plan reservation or positive
money ceiling proof. A plan with a positive remaining allowance can
still overrun when a single charged call exceeds that allowance.

## Follow-up: persisted unknown paid attempts require reconciliation

The earlier unresolved-job safeguard depended on a nonempty
`provider_job_id`. A provider may have received a charged submission
before a crash, leaving a durable SUBMITTED/PROCESSING record but no
provider-assigned ID. Treating that empty field as a confirmed refusal
could fund a fresh attempt with a different idempotency key.

P4 now refuses new REAL-provider calls if a persisted attempt is
SUBMITTED, PROCESSING or CANCELLED, regardless of whether its job
ID is present; it also refuses SUCCEEDED attempts without a persisted
candidate even if the job ID is absent, or FAILED attempts with an
accepted job ID. Such states require external reconciliation. A
confirmed pre-job FAILED response without a job ID remains normally
retryable through the write guard. MOCK-only simulation is unchanged.
The refusal occurs before the next shared budget reservation and
provider call; it does **not** settle or reconcile any previous charge.

Fake-provider SQLite tests covered six status/receipt permutations,
same-session resume and an independently constructed guard reading a
committed unknown submission in a new DB session. Latest focused
suite **44 passed, 2 third-party warnings** (job
`292b099a3de34f9380043358db5ac4df`); Ruff, mypy (**464
files**) and diff-check passed in the same job. Wider P4 and C3/C4
contract tests **246 passed, 3 third-party warnings** (job
`37c499df1b9e418d13f1bdbaeab6e1b9`), before the additional
committed-restart regression case. No real provider calls, connected
DB writes, live process-restart test or full R5 run.

## Follow-up: accepted paid output missing audio cannot be replayed

A REAL generation response can be SUCCEEDED with no usable audio and
no `provider_job_id`. Its global reservation is correctly committed
because the provider accepted the write, but the subsequent P4
attempt was historically persisted as FAILED with an empty job ID.
A new session and guard could mistake this for a free pre-job
refusal and fund another attempt under a new key.

P4 now writes the durable code
`PROVIDER_ACCEPTED_OUTPUT_MISSING` when a REAL accepted-success
response has no audio. A future run treats that failed attempt as
unreconciled regardless of whether the job ID exists. Similarly,
FAILED attempts with provider-reported or legacy-unverified money
or credit evidence are not considered free merely because the
provider job ID is blank. Normal confirmed pre-job refusals still
follow the existing guarded retry path.

SQLite tests cover 2 committed accepted/no-audio calls with and
without job ID, including fresh DB sessions/guards and no repeat
budget reserve, plus 4 failed-with-charge-evidence variants.
The focused P4/unit/runtime suite **50 passed** with 2 third-party
warnings, Ruff, mypy (464 files) and diff checks passed
(job `6df7f98a4ffd5cdd13a6c3b40efd9761`).
Wider P4 unit plus C3/C4 contracts **253 passed, 3 library
warnings** (job `93db22a1e40bca178253ed4684eae574`).
No real provider was called and no connected database was modified.

## Follow-up: a missing job ID is not a no-charge receipt

The previous P4 settlement still equated `FAILED` plus a blank
`provider_job_id` with proof that no paid submission occurred.
An HTTP request can be accepted or charged before an error response
or lost receipt, so automatically releasing the global ledger
reservation could allow another charged attempt.

`GenerationResponse.confirmed_unsubmitted` now defaults to
**False** and is validated to require a FAILED response with no job ID,
audio, credit usage or receipt. Only the adapter's explicit
pre-submission confirmation permits a REAL reservation to be released
(and persists `PROVIDER_CONFIRMED_NO_SUBMISSION` for legitimate guarded
retries). A REAL failure with an empty job ID but no confirmation keeps
its external ledger reservation **RESERVED**, is stored as
`PROVIDER_OUTCOME_UNKNOWN`, and halts further attempts and future plan
replays pending independent reconciliation. Historical FAILED attempts
without that explicit safe marker are likewise not assumed free.

The unconfigured Suno and ElevenLabs adapters and ElevenLabs' hermetic
disabled-call path can assert the safe marker because they do not
issue any HTTP request; the hermetic stub no longer invents a job ID.
Suno failures after `submit_generation` remain unconfirmed, even when
the API returns no clip ID. MOCK simulation remains unaffected.

Focused provider/engine/ledger/runtime tests **73 passed**, 2 library
warnings; Ruff, mypy **464 files**, diff-check passed (job
`23fb2e78e4abff5075f0f9619c00c7eb`). Tests cover ambiguous
responses and subsequent plan replay with both a fake budget and the
actual isolated SQLite ops ledger, where its `RESERVED` entry stays
held; explicit known-no-call refusal still releases. The wider P4
unit + C3/C4 contract suite **260 passed** with 3 dependency warnings
(job `a9b83a13bb72be59f1018f150cdafe86`).
No real payment or production service was exercised.

## Suno exceptions must not fabricate a provider job identity

The Suno adapter previously used `suno_err_<track_spec_id>` as its
`provider_job_id` on all client errors. This made a local error token
look like an actual external receipt; in particular, a timed-out
submission could be mistaken for a known accepted provider job.
It now retains only a clip ID returned by `submit_generation`:
errors at submission and responses with no clip ID return an empty
provider job ID and **no** `confirmed_unsubmitted` attestation.
After successful submission, failures at polling or WAV download
retain the genuinely observed clip ID. The P4 guard treats missing
receipt/ambiguous submission as unresolved; it will not release
the reservation or silently buy another attempt.

Four fake-client failure cases and an integrated P4 adapter/guard/
committed SQLite replay case verify the behaviour without network
access. Focused tests **67 passed**, Ruff/mypy **464 files** and
diff-check passed (job `bd17d4b4ae8bf98fdd35018459d6fe0c`).
Wider P4 unit + C3/C4 contracts **265 passed**, 3 dependency
warnings (job `d947d36a2a0cb499a08721d118de0023`).
No real Suno account or billing ledger was queried.

## Paid audio without provider job ID must not become a candidate

A REAL provider could return SUCCEEDED and valid audio but omit its
job identifier. P4 formerly treated audio alone as sufficient to create
an exportable candidate, artifact and rights snapshot. This allowed a
paid external effect to be associated with unverified job provenance.

The REAL success path now **requires a non-blank returned job ID**
before any downstream candidate/artifact/rights persistence. Otherwise
the already accepted write is settled as performed, the failed attempt
is persisted with `PROVIDER_ACCEPTED_JOB_ID_MISSING`, the ID is
normalised to an empty string, and the plan stops pending explicit
provider reconciliation. Neither the stored failed attempt nor a
subsequent fresh session/guard can pay again automatically. MOCK
simulations are unaffected by this real-receipt requirement.
A returned job ID is still **not** proof that it exists on the
provider side; external verification remains required.

Two in-process, no-network regressions for empty and whitespace-only
job IDs and committed-session replays passed within the **49-test**
focused P4/engine/runtime suite (job
`85ace36b4204feb56537322f9f94ccdc`). Ruff, mypy
(464 files) and Git diff check passed in the same job.
Wider P4 unit + C3/C4 contracts **269 passed**, 3 dependency
warnings (job `ca5b0a4de0e0bc19fd6f5337e12b3a51`).
No real supplier/rights or full-repository/R5 gate was exercised.

## Historical successful attempts with missing job identity (2026-10-29)

The earlier response-time fix did not protect legacy rows already stored as
SUCCEEDED with a candidate but with no authentic provider job ID. On replay,
P4 considered that candidate usable and could buy the next variant even though
the prior paid generation lacked provider identity. Two committed-session
regression cases (empty and whitespace-only IDs) both **failed before** the
change, with an unintended second attempt (job
`c1217804a6a9dba14d977cfadf68b490`). P4 now refuses the entire REAL
plan with `provider_job_reconciliation_required` before any further budget
reservation or paid call. No candidate or attempt is silently rewritten.

After correction, **45 focused guard tests passed** (job
`b2ba49f37f951f30954ba7dc656552d9`), **271 P4/C3/C4 tests passed**
(job `2bdabd9094351732bdd9b1ccd79379e7`), and Ruff, mypy (464
files) and Git diff-check passed (job `398f630c096974c31029f5fbfa8f2180`).
All tests used hermetic SQLite/fake providers. Real provider identity, invoices,
full repository regression and R5 remained unverified. This is another
partial safety fix, not budget completion.

## Downstream historical candidate origin refusal (2026-10-29)

A pre-fix P4 candidate might already have a full-mix artifact, persisted
rights snapshot and completed plan despite a missing producer job ID. The
C4 event builder and candidate detail/license accessor had independently
trusted those old records. Empty and whitespace IDs each reproduced two
unsafe C4 transfers (job `b0fa00c8884a2243cc035995a07e6a52`) and
two unsafe rights views (job `b3589f9283636b3b3db7bb5e6c31b442`).
Both flows now fail closed: `CandidateExportError(provider_job_id_missing)`
blocks the P5 event, and candidate detail returns no validated origin/rights.
Existing records remain untouched and available for reconciliation.

After the fixes, C4 contract **45 passed** (job
`9f4205acbc0012941eebfebc7c8a3a45`), P4/C3/C4 **275 passed** (job
`5885ae525ec0a670040399faa28cb4bc`), Ruff, mypy **464 files**
and Git diff-check passed (job `cff578a05ef1651c4f1281c9704aa410`).
These are hermetic tests, not live supplier identity, invoices, production
rights or R5 proof. An arbitrary nonblank ID is not independently verified.

## Still unsafe / not covered

**Positive** per-plan monetary and credit ceilings are **not enforced cumulatively or concurrency-safe across workers**. The existing global/capability persisted ledger controls program spending, not per-plan ceilings. Missing provider rate/usage receipts remain UNKNOWN; credits are not priced money. A complete B03 closure needs a durable per-plan budget reservation/settlement mechanism, currency-aware rate version, plan ownership and reset policy, idempotency and concurrent spend tests, plus real-account reconciliation. No claims of positive-plan cap enforcement or production readiness are made.
