# ADR-0041 — A replayed budget reservation must not reauthorize a paid call

- Status: Implemented in A–B code remediation on 2026-10-28; production deployment is not approved by this decision.
- Scope: Shared `plm_platform` write guard and persistent budget ledger adapter (P4/P7/P8 external effects).
- Owner: B03 budget and external-write idempotency.

## Context

`BudgetLedger.reserve(capability, idempotency_key)` returns its existing
`RESERVED` or `COMMITTED` entry with `allowed=True, replayed=True`.
This is correct for an **idempotent funding record**: the ledger does not
reserve a second amount. Previously `ReservationResult.verdict()` discarded
`replayed`; `ExternalWriteGuard` interpreted the allowed funding result
as permission to call the real provider a second time. If the service
crashed after the provider accepted work but before its own result was
persisted, another paid call could occur despite only one ledger charge.

## Decision

Preserve `replayed` in the internal `BudgetVerdict` (default False for
other budget implementations). Under LIVE policy and a REAL provider,
`ExternalWriteGuard` must refuse a replayed existing reservation with
`DENY / budget_reservation_replayed`, before calling any provider. The
pipeline classifies this refusal as a budget hold. This **does not**
automatically declare the existing write successful or not performed.

An explicit `RELEASED` ledger entry represents a known no-effect write.
The ordinary atomic ledger reserve operation may fund the same key
again after this confirmed release, subject to current ceilings.
Mock simulation, unfunded writes, policy checks and fresh reservations
retain their previous behavior. The ledger's public `reserve` semantics
and all persistent schema / externally frozen wire fields are unchanged.

## Verification and limits

The focused unit suite checks that one fresh LIVE write can proceed, a
reserved and a committed replay are both refused without a new ledger
entry, and an explicitly released request may be re-reserved. Another
test verifies the budget-hold classification. A subsequent SQLite
integration-of-components case simulates P4 music-transaction rollback
after an ambiguous provider exception while the separate ops reservation
remains held; the replay cannot invoke the fake real provider a second
time. A two-thread SQLite race test confirms exactly one EXECUTE and
one DENY for the same paid key. These are in-process/unit proofs, not
PostgreSQL crash/failover certification. Refer to `docs/STATUS.md` for
the actual test totals and full-regression evidence.

This decision does **not** constitute a durable service-side recovery
protocol, reconciliation of provider receipts, cross-worker crash or
PostgreSQL race testing, or cumulative positive **per-plan** budgets
and provider credits. An existing paid job must be reconciled, not
retried by silently treating the ledger replay as a new authorization.
No real provider call or connected database migration is authorized.
