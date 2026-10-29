# B03: plan-level paid budget — atomicity and currency prerequisite (2026-10-28)

**Status: design assessment only; NOT implemented, activated, approved or signed off.**
Source: `packages/plm_contracts/seams/c3.py` C3 v2.0, `GenerationPlan`,
`services/music_factory/generation/engine.py`,
`packages/plm_platform/ledger.py` and `execution.py`.

## Verified code facts and unsafe shortcut to avoid

1. C3 `budget_limit` and persisted P4 `GenerationPlan.budget_limit`
   are nonnegative **floats without a currency**. They cannot be assumed to
   mean USD, provider credits, or the ops ledger currency. C3 is frozen at
   2.0; modifying the seam requires a versioned ADR and consumer updates.
2. The ops ledger stores exact `Money(Decimal, ISO currency)` per global
   and optional capability ceilings, serializing reservations using
   PostgreSQL `FOR UPDATE` or SQLite `BEGIN IMMEDIATE`.
   It **does not record `plan_id` or attach any entry to a plan ceiling**.
   Its global authorization is not proof that a P4 plan's positive
   `budget_limit` has headroom.
3. P4 uses the global rate card with `cost_estimate=None`, and records
   provider credits separately from money (no credit→USD conversion).
   Historic credit observations may be UNKNOWN. The existing
   `_prior_paid_credit_refusal` is a conservative post-hoc brake, not an
   estimate or an atomic reservation.
4. `BudgetVerdict.replayed` already denies a second live external
   effect through `ExternalWriteGuard`. A retried ops funding row does
   not authorize another provider call. Do not duplicate that work.

**Do not**: silently label the C3 float as USD; infer zero charge from
missing receipts; use an in-process lock for multiworker accounting;
allow unknown credit cost to consume an invented number; mass-apply
migration 0040/0041 or touch the shared dev database.

## Required implementation boundary, subject to stage approval

- Version the P3→P4 contract through an ADR. Require an **explicit plan
  budget currency** (and stable plan owner/period/version) for live
  money budgets. Keep old C3 inputs legible as legacy/unpriced; they must
  not be automatically assumed USD.
- Introduce a platform-owned, migrated plan-ledger binding that stores
  immutable `plan_id`, authorization scope, currency, exact limit, and
  the linkage of each ops `idempotency_key` to that plan. Existing
  `ops.budget_entries` cannot provide this linkage reliably by parsing
  its string key; use typed columns or a separately constrained table.
- **In the same database transaction**, lock global, capability, and
  plan ceilings in a stable order, validate known money cost and currency
  or versioned FX rate, check all three remaining balances, and insert
  the idempotent reservation. Both cross-worker contenders for the
  last plan headroom must not succeed. Replayed reservations cannot
  authorize a second paid write.
- Settlement must preserve reserved exposure on unknown provider
  outcome, record provider-reported cost when verified, and forbid
  charging an unsupported currency. On a known no-call refusal only,
  release all linked holds together. Refund/reconciliation requires
  external evidence and an auditable operator decision.
- Credits need their **own native-unit** (noncurrency) estimated
  per-call reservation, provider/model rate and usage receipt.
  Unknown usage or unreliable per-call estimates must fail closed;
  positive residual plan credits alone are not permission.

## Minimum evidence gate before rollout

1. Unit: missing currency, unknown rate/receipt, zero/positive limits,
   invalid finite inputs, replay, same plan vs different plans,
   cancel/refund/unknown outcome and model/provider mismatch.
2. Disposable PostgreSQL: two transactions/workers racing for the last
   plan unit, mixed global and plan ceilings, lock ordering, replays
   after simulated crash, overflow and rollback; migration/backfill
   reversibility and retained row hashes.
3. Frozen C3/C4 contract consumers and P1→P9 mock Compose replay on
   the **same revision**, plus R5 isolated-stack proof.
4. Actual provider billing reconciliation, rights clearance, operator
   approval and deploy/rollback plan are separate gates.

This note is not an authorization to run live paid providers or mutate
the shared database. B03 remains **PARTIAL** until the above exists.
