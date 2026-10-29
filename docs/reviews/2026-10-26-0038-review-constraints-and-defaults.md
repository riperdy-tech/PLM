# 0038 release-review table defaults and constraints — read-only audit

**2026-10-26:** Extended the offline 0038 preservation evidence. No Alembic upgrade, schema write, clone creation or restore was executed.

## Gap and correction

The previous `release_review_schema` evidence matched column names/types/nullability/time-zone flags, primary-key columns and the event lookup index. It did not capture server-side column defaults or unexpected CHECK, foreign-key and UNIQUE constraints. A clone could change inserts or data-integrity rules without changing a single existing row fingerprint.

`tests/support/migration_review_snapshot.py` now captures every review column's `server_default` and the inspector's CHECK, foreign-key and UNIQUE constraints for both historical `ops` tables in the same existing `REPEATABLE READ, READ ONLY` transaction. An uninspectable non-string default causes refusal. `tests/support/migration_review_compare.py` requires the exact empty constraints and no defaults defined by the SHA256-pinned historical 0038 review revision, rather than accepting matching but defective source/clone metadata. Old snapshots lacking these fields must be recaptured.

The synthetic unit tests cover read-only inspector extraction, refusal of an uninspectable default, and rejection of inserted defaults and each newly examined constraint type in both an after-clone and a defectively matching source/clone-before. Tests use fabricated fixtures only for regression logic, not as evidence of the real database.

## Verification and limits

- Focused comparator/snapshot/offline PostgreSQL DDL suite: **90 passed, 0 failed** (exit 0).
- Targeted and repository-wide Ruff: **passed** (exit 0); mypy **462 source files passed** (exit 0).
- Whole-project non-integration regression: **2,745 passed, 1 skipped, 80 integration tests deselected, 15 warnings** (exit 0, 290.65 s).
- Change-scoped `git diff --check`: **passed** (exit 0). The whole-worktree `git diff --check` separately failed (exit 2) on existing trailing blank lines at `services/youtube_publishing/client/__init__.py:24` and `tests/unit/music/test_provider_adapters.py:190`, outside the changed files of this review. They were not altered.
- Real connected development DB snapshot, **read only**: revision `0038_release_reviews`; review rows 2; event rows 4; YouTube manifests 8; new `release_review_schema` matched the reference (`live_schema_contract_matched=True`, exit 0).
- No credential, record body or connection URL was printed.

This review does **not** prove real migration execution from either sibling, an isolated clone/restore, convergence to the draft 0039 merge head, or the completeness of all PostgreSQL DDL details (e.g., index expressions/predicates, permissions, ownership, triggers). Comparator input is operator-managed JSON and is not cryptographic proof of clone identity or authorisation. The draft 0039 merge remains uninstalled; the shared development database remains unchanged.
