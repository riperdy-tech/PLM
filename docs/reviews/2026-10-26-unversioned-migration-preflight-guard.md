# Unversioned PostgreSQL migration preflight — fail closed

**2026-10-26; read-only development review.** No Alembic upgrade, downgrade, stamp, database clone, database write, or external publication was performed.

## Why the change matters

The `plm migrate` command already refused alien revisions (including the locally recorded `0038_release_reviews` sibling), but an absent/empty `public.alembic_version` could still be interpreted as a fresh database even when PLM schemas or tables remained. Starting Alembic from base on a partially initialized or unversioned database could collide with existing objects and obscure the 0038 reconciliation.

## Implemented

- `packages/plm_platform/cli.py` performs an additional **catalog-only read** if Alembic reports no current revision. The expanded query counts **any non-system user schema** (not only the known PLM names) and public relations, routines, composite/domain/enum/range types, and extensions. Nonzero, missing/malformed, or unreadable evidence blocks `plm migrate` before `alembic.command.upgrade`. The engine is disposed on all paths. These checks read catalog facts only, never record contents.
- For a genuinely empty unversioned target, the normal first-time upgrade path remains available. An already-versioned target retains the preexisting alien-revision rejection.
- `plm doctor` no longer implies that a missing version record alone makes migration safe; its diagnostic tells operators to establish that the database is empty.
- `tests/unit/test_cli.py` verifies that foreign revisions remain blocked, known revisions are unaffected, clean unversioned targets remain eligible, and populated/unknown unversioned targets never invoke upgrade. Tests mock connections, not real migrations.

## Observed evidence

- Targeted CLI + migration safety + offline graph suite after expanding the guard: **66 passed, 1 skipped, 0 failed** (exit 0); the extra test refuses a failed catalog query before Alembic runs.
- Entire repository Ruff: **passed** (exit 0); mypy `packages services`: **462 source files passed** (exit 0); changed-file `git diff --check`: **passed** (exit 0).
- Live local PostgreSQL *read only*: the expanded catalog query completed and detected existing user objects (Boolean evidence only). The earlier direct `_migration_preflight` check also rejected the known foreign `0038_release_reviews` revision. No data rows, role names, credentials or raw object names were printed.

## Limits

This is a **preflight safety guard**, not authorization to migrate or proof of a successful 0038/0039 reconciliation. It is not a schema transaction lock: database state can change after preflight and before Alembic opens its connection. It conservatively refuses nonempty unversioned databases, even if their objects are unrelated to PLM. The expanded checks now cover custom schemas and public routines/types/extensions, but do not attest every possible PostgreSQL object class, effective inherited/global permissions, or state changes that race with Alembic's later connection. The default catalog namespace filters also conservatively treat any third-party user schema as evidence the database is nonempty. The planned review and separately approved disposable-clone PostgreSQL migration/backup/restore test remain necessary.

The prior attempted full non-integration regression request was initially blocked before launch. A new command after this extension **actually ran and passed**: **2,796 passed, 1 skipped, 80 deselected, 15 warnings in 296.98 seconds**, exit 0. This does not substitute for integration tests or a genuine isolated PostgreSQL sibling merge.
