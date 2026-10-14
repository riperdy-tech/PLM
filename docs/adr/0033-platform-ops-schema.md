# ADR-0033: A platform-owned `ops` schema for cross-phase runtime state

- Status: `accepted`
- Date: `2026-10-14`
- Remediation stage: R3
- Supersedes / superseded by: `—`

## Context

The database has one schema per phase (P0 §4), and a phase may alter only its own schema.
R3 adds runtime state that belongs to no single phase:

- **The cost ledger and budget ceiling.** The ceiling is one number for the whole
  program. Reservations come from P4 generation, P7 uploads, P8 submissions and any
  later paid provider.
- **The exception queue.** A hold can originate in P5 (rights), P7 (platform policy),
  P8 (distributor dispute) or the platform itself (budget exhaustion, account security).
- **The decision log (PART XXI).** P2 writes allocation and override decisions; P9 later
  attaches the realized outcome.

Copying these tables into each phase schema would split one ceiling into nine. It would
also scatter one review queue across nine schemas and break the rule that a phase writes
only its own schema.

## Decision

- A tenth schema, `ops`, is created by migration `0025_ops_schema` and owned by
  `plm_platform` (`plm_platform.db.PLATFORM_SCHEMA`). Its migrations live under
  `migrations/versions/plm/`.
- `SCHEMAS` stays the nine phase schemas; `ops` is not a phase.
- Phases reach `ops` only through `plm_platform` APIs: ledger reservation, raising or
  resolving an exception, recording a decision. A phase never writes `ops` tables
  directly.
- Cross-service events that a phase emits (for example P9's reward to P2) stay in the
  producer's own schema, in its transactional outbox. The outbox row must commit in the
  same transaction as the fact it announces, and that transaction belongs to the
  producer.

## Consequences

- `plm doctor` and the migration tests account for ten schemas.
- When phases later run as separate services, `ops` is accessed through a platform
  service or a shared database role. That choice is deferred until services are split
  into separate deployments.
