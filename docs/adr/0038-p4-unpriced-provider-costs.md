# ADR-0038 — P4 credits and real-money provenance are distinct

- Status: Accepted for **A–B code only**, 2026-10-28. Schema rollout to connected development, staging or production is **not approved** by this decision.
- Scope: P4 internal persistence and money semantics; no alteration to frozen C3/C4 wire schemas.
- Related: RC5, P4-04, B03 in the 2026-10-28 remediation register; `migrations/versions/music/music_0040_unpriced_costs.py`.

## Problem

P4 converted `GenerationResponse.cost_credits` to `GenerationAttempt.actual_cost` using an invented exchange rate (`credits * 0.05`). The resulting price had no verified provider billing receipt or currency basis. Both `music.generation_attempts` and `music.provider_cost_records` required a non-null numeric price; the domain used default `0.0` and USD even if no price was known. A quota unit and a billable currency amount are not interchangeable, and zero is not a representation of unknown.

The separate platform `ops` ledger's approved rate-card *reservation* is a bounded authorization cost, not a provider-reported actual expense. It remains governed by the external-write guard and does not make the attempt's actual provider bill known.

## Decision

1. Keep provider credit numbers in their existing fields; never multiply by an assumed dollar rate in P4.
2. A new attempt and its cost record store `actual_cost/actual_amount=NULL`, estimated monetary amounts `NULL`, `cost_currency/currency=NULL`, `cost_receipt_ref=NULL` and `money_basis='unpriced'` until a validated bill and explicit currency exist.
3. The typed domain refuses monetary amounts/currency/receipt with `unpriced`, including a fabricated `0.0`. `provider_reported` requires an actual amount, ISO-shaped currency, and a nonempty provider receipt reference (and a pricing version on the cost record). Verification of that receipt against the upstream provider is future R6 financial integration; a string alone is not evidence.
4. Migration `0040_p4_unpriced_costs` relaxes the existing monetary column null constraints on two tables, adds basis, reference and currency columns, and enforces the same safe provenance shapes with DB CHECK constraints against direct writes. It never updates, deletes, resets or converts old monetary numbers. Rows that existed before this migration receive `money_basis='legacy_unverified'`. The temporary migration default is removed immediately afterward so future raw inserts must supply an explicit basis.
5. Downgrade is guarded: if any post-0040 unpriced/provider-reported row exists or null monetary field would violate the old schema, it refuses rather than inventing dollar zero or discarding provenance. Reversible only if all existing rows were compatible with the old contract.
6. P4 provider write authorization continues to use `ExternalWriteGuard` and the persisted ops cost ledger. This ADR does **not** authorize paid calls, infer provider commercial rights, make price cards provider receipts, or finish plan-level credit cap/cost reconciliation.

## Gates / known limitations

- Focused tests must prove unknown remains `None`, finite nonnegative priced values have currency and receipt references, historical records keep their amount but are explicitly unverified, and 0040 has a single Alembic head.
- Offline PostgreSQL SQL was reviewed, and the **real isolated PostgreSQL 16 backup/restore and 0039→0040 rehearsal passed** with 341+341 preserved historical rows and R5 fingerprints. See `docs/reviews/2026-10-28-p4-0040-isolated-postgresql-rehearsal.md`. **This is not permission to apply 0040 to any real/shared database.**
- The active local development DB remains on `0039_merge_0038_branches` and must not be silently upgraded by `plm migrate` or direct raw Alembic. The connected shared DB may be unavailable for 0040-dependent P4 functionality until a separately approved upgrade has passed. No false production-readiness claim.
- More work is needed for Decimal/NUMERIC monetary receipts, immutable verified rate cards, credit reconciliation, spend limits, source corrections and plan-level enforcement. Those are B03/R6 follow-ups; this is a narrow safety fix, **not B03 closure**.
