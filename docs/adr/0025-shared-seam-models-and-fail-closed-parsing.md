# ADR-0025: Define each seam payload once, version it, and parse it fail-closed

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R2` (registry authority), `R8` (verdict vocabulary); remediation stage R1
  (`docs/reviews/2026-10-13-system-review-and-remediation-plan.md`)
- Seam: C1–C9 (all wire payloads)
- Supersedes / superseded by: `—`

## Context

The registry froze field lists for every seam, but each side then wrote its own model.
The R0 review found two failure patterns that the existing contract tests could not
catch.

1. **Consumers replaced missing facts with passing values.** Examples:
   - P6 read a missing disposition as `PASSED` (`media_factory/rendering/assembler.py`).
   - P6 invented artifact ids, durations and checksums.
   - P8 invented ISRCs, durations and checksums, and defaulted experiment identity to
     `exp_unknown`.
   - P9 invented ISRCs.
   - P3 invented `expected_value` and version strings.

   Each one turns "we do not know" into "it is fine".
2. **Contract tests checked copies.** Most seam tests validated the producer's payload
   against a schema written inside the test file, so the real consumer's parsing was
   never exercised.

Vocabulary also drifted at the seams. P4 emits `permitted`/`pro_tier` where P5 expects
`ELIGIBLE`/`pro`. P5's own `GateDisposition` adds `CLEARED_BY_OVERRIDE` as a fifth
disposition, while `plm_contracts.GateOutcome` keeps the override separate. P9's
`ObservationWindow` has `1h`/`14d`, which P2's shared `ObservationWindow` rejects.

## Decision

Every seam payload is defined once as a `plm_contracts.seams.SeamModel` subclass, and
both the producer and the consumer use that class. Consumers parse only through
`parse_seam`. Vocabulary that crosses a seam comes from `plm_contracts`; a service may
not redefine it.

## Rules

- **Closed and frozen.** `extra="forbid"`, immutable.
- **No defaults for required facts.** A missing required fact raises
  `SeamRejection(missing_required_field)` with `field_paths`. A fact the registry allows
  to be unknown is typed so that unknown is an explicit value, never a guessed one.
- **`schema_version` on every payload** (`"<major>.<minor>"`). Consumers accept the
  current major and the previous one; anything else is `unsupported_schema_version`.
  Minor bumps may only add optional fields. A major bump requires an ADR and a registry
  amendment.
- **Reason codes**: `missing_required_field`, `unknown_field`, `invalid_field_value`,
  `unsupported_schema_version`, returned as `ErrorShape`.
- **Contract tests** call the real producer and the real consumer. Each required field
  is tested for removal, and each consumer must reject the payload. A narrow static guard
  (`tests/contract/test_no_fail_open_defaults.py`) additionally forbids the specific
  fail-open patterns found in the review. It is a supplement, not the acceptance
  criterion.

## Consequences

- Wire payloads gain `schema_version`. Registry entries record it as a required field.
- Producers and consumers that invented values now block with a reason code. Callers
  that relied on the defaults must supply the facts or handle the rejection.
- Duplicate vocabulary enums in services become imports of the shared ones.

## Alternatives considered

- *Keep per-service models and strengthen the copied test schemas.* Rejected: tests
  would still not exercise the real consumer, which is where the defaults lived.
- *An AST scan as the main defence.* Rejected as primary: it both over-flags legitimate
  defaults and misses fail-open logic written another way. It is kept narrow and
  secondary.

## Cost if wrong

If a seam model is too strict, a legitimate payload is refused loudly with a named
field. That failure is visible and is fixed with a minor version that adds an optional
field. The opposite error, a lenient model, is the one this ADR exists to prevent,
because it fails silently.
