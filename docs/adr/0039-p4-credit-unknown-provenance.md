# ADR-0039 — P4 provider-credit UNKNOWN is not zero

- Status: A–B **code-only decision**, 2026-10-28. Applying 0041 to the connected development/production database **not approved**.
- Follows: ADR-0038 (monetary amounts independent of provider credits).
- Owner: P4 Music Factory, B03–B04 audit remediation.

## Defect

Suno generation success returned `cost_credits=10.0` regardless of provider evidence. `GenerationResponse.cost_credits` defaulted to `0.0`, and two persistence tables required non-null `actual_credits/credits_actual`, therefore no way to distinguish unavailable billing usage from actual zero. ElevenLabs similarly claimed 1,000 remaining credits from the mere presence of an API key (corrected independently). Real quotas/credits and mock-simulated usage must not be mistaken for billable facts.

## Code-only decision

- `GenerationResponse.cost_credits` and persisted attempts/cost-record credit amounts are nullable; UNKNOWN means `None`, never 0. The Suno adapter no longer reports invented 10 credits. A source reference is required before a real provider's credit usage is labelled `provider_reported`.
- `credit_basis`: `unknown`, `mock_simulated`, `provider_reported`, or `legacy_unverified`. `credit_receipt_ref` stores a provider reference only if supplied. The engine treats mocks explicitly as simulated, real observed usage as reported only when an amount and reference are both present; all other successful real provider outputs are UNKNOWN.
- Model validators and DB CHECK constraints refuse UNKNOWN with a numeric amount (including zero), mock with external receipt, or provider-reported credits without a nonempty receipt. Legacy numbers are **not changed**, only flagged `legacy_unverified`.
- Observability separately reports unknown-usage attempts, simulated mock credits and observed provider credits; simulated credits are not recorded as real credits spent.
- `0041_p4_credit_provenance` is a new code-only Alembic head following `0040_p4_unpriced_costs`. It relaxes four credit-column nullability constraints, adds `credit_basis` and `credit_receipt_ref` to two P4 tables, temporarily defaults *existing* rows to `legacy_unverified` then removes the default, and adds CHECK constraints. No UPDATE or DELETE rewrites historical values. Downgrade refuses losing unknown/provenance or inserting made-up numbers. No C4/P5 wire change.

## Verification

- Focused models, adapter, original merge, 0040/0041 DDL, CLI guard suite: **96 passed, 1 skipped** (request `plm-AB-B04-20261028-credit-0041-focused-03`).
- **Real disposable PostgreSQL 16** restore from local 0039 source; execute active code `0039→0040→0041` (request `plm-AB-B04-20261028-credit-0041-isolated-pg-01`, job `f355a0b4ef0d12cce5174afb9344b647`, exit 0). Preserved original old-column SHA256 for **341 generation attempts + 341 cost records**, all old rows explicitly `legacy_unverified` for both money/credits, correct nullability/default removal, both credit CHECK constraints, preserved review/YouTube data/security fingerprints, and idempotent upgrade. No cloned container remains.
- Fresh independent read-only source postcheck (request `plm-AB-B04-20261028-credit-0041-independent-postcheck-01`): connected dev DB still has **exactly one 0039 revision**, 341+341 historical P4 rows. **No source DB migration or write was performed.**
- Ruff passed. Mypy **463 files** passed after an initial type-annotation correction. Full non-integration regression **3,056 passed, 1 skipped, 80 integration tests deselected, 15 warnings** (296.81s, exit 0, job `2299a4a8ac57c3697d91ef174fadfb4d`). Real R5 integration and live provider billing tests were not run.

## Limits

This is a source-data integrity fix, not provider quota reconciliation. No independent Suno/ElevenLabs invoice, account entitlement, verified generation-time credits, production rate-card contract or total plan spending limit has been established. A provider-supplied receipt reference alone is not independent proof of billing. B03/B04 remain PARTIAL. The connected DB is still on 0039 and **new P4 code requiring 0040/0041 is not deployable there without separate DB-specific approval and retained backup/restore validation**. A02 worktree consolidation, Compose R5 and full legal/audio quality gates remain unverified here.
