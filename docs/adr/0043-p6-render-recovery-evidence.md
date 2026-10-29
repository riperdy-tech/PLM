# ADR-0043 — P6 render recovery is gated on persisted evidence, not caller booleans

- Status: accepted for A–B local code development; **no connected DB rollout authorized** (migration `0042_media_render_evidence` is code-only)
- Date: 2026-10-29
- Owner: P6 media factory (B02)
- Related: ADR-0013 (storage namespace), ADR-0028; audit rows R5-RENDER, R5-TIME, X04, RC3; `docs/reviews/2026-10-28-b02-render-recovery-assessment.md`

## Context

B02 added durable QUEUED → RUNNING claims, fenced terminal updates, claim-scoped
artifact namespaces, stale quarantine and staged retry. Two gaps remained:

1. `quarantine_stale_render_job` and `queue_reconciled_render_retry` accepted
   three caller-supplied booleans (`worker_termination_confirmed`,
   `orphan_artifacts_reconciled`, `source_inputs_reverified`). A boolean is an
   assertion, not evidence, and nothing recorded who asserted it or why.
2. When the render raised *inside* the owning process (encoder error, timeout,
   storage error), the claim stayed RUNNING forever. Only a supervisor
   attestation could move it, although the owner itself knew it had stopped.

## Decision

1. New append-only table `media.render_recovery_evidence`, one row per
   (render job, kind), kinds `worker_termination` and `orphan_artifacts`. Each
   row carries the exact claim identity (worker ID, start, input SHA256), a
   `method`, an optional `evidence_ref`, strict-JSON `detail`, `recorded_by`
   and `recorded_at`.
2. `record_render_recovery_evidence` inserts with `INSERT … SELECT` from
   `render_jobs` matching the full claim identity and an allowed status
   (termination: RUNNING or QUARANTINED; artifacts: QUARANTINED only). Evidence
   therefore cannot be written for an unclaimed, completed or different
   execution. Identical replay is idempotent; a different second record of the
   same kind is a conflict.
3. Accepted methods:
   - termination: `owner_exit_observed` (written only by the claim owner after
     its render call returned or raised; `subprocess.run` has reaped FFmpeg by
     then), `supervisor_attestation` (requires `evidence_ref`);
   - artifacts: `claim_namespace_inventory` (requires `evidence_ref` and a
     complete, counted, non-truncated listing), `supervisor_attestation`.
4. `quarantine_stale_render_job` requires a matching `worker_termination` row
   in its conditional UPDATE. `queue_reconciled_render_retry` requires both
   rows for the predecessor's claim in its conditional INSERT … SELECT. The
   booleans are removed. Input re-verification stays where it is enforced:
   the executing assembly recomputes the input SHA256 and refuses a staged
   attempt that no longer matches.
5. `assemble_and_persist` fences its own claim when anything after the claim
   raises an ordinary `Exception` (render failure, or a finished render it
   refuses to persist): it records `owner_exit_observed` with the exception
   type and code, and quarantines with `render_run_stale` in a separate session.
   If fencing fails, the original exception still propagates, with a note.
   `BaseException` (interrupts) is not treated as an observed exit. When the
   failure comes after the render (persisting its records), the failing
   request's transaction is rolled back first: its completion UPDATE holds the
   job row lock, and on PostgreSQL a fence from another session would otherwise
   wait on it forever. A lost claim (`render_ownership_lost`) is not fenced again.
8. An inventory is recordable only from a listing taken while the claim was
   already QUARANTINED. An empty listing under an unverified operator brand hint
   is refused: it cannot distinguish "no orphans" from "wrong prefix". A
   non-empty listing proves the prefix, because the claim namespace derives
   from the execution identity.
6. The retry `INSERT … SELECT` now builds its column list and select list from
   one name-keyed mapping, replacing a positional select that silently depended
   on table column order.
7. `plm migrate` refuses code-only heads through one `_CODE_ONLY_HEADS`
   constant; a unit test asserts the checkout's actual head is in it, so a new
   revision cannot silently lift the rollout refusal.

## Consequences

- An in-process render failure now leaves QUARANTINED + evidence instead of
  RUNNING. A replayed assemble request still returns 409
  `render_job_reconciliation_required`; nothing retries automatically.
- A killed process (SIGKILL, OOM, container stop) never reaches the owner
  path. Its claim stays RUNNING until a supervisor records
  `supervisor_attestation` with a reference. **No independent supervisor,
  heartbeat or lease exists yet**; that is still B02 work.
- `orphan_artifacts` evidence is accounting for later cleanup. Correctness of a
  retry rests on per-execution namespaces plus the completion fence: an
  orphan from attempt N can never be referenced by attempt N+1's package. No
  code deletes objects.
- The evidence `brand_source` of an inventory taken without a persisted package
  is an operator hint (see `render_reconciliation.py`); recording it does not
  make it verified.
- Migration `0042_media_render_evidence` adds one table; downgrade refuses
  while any evidence row exists. It is code-only: the shared dev DB is at 0039
  and must not be migrated without a separate approval.
