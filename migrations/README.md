# Migrations

- One alembic chain for the whole platform ("single chain"). Revision files live
  in `migrations/versions/<schema>/` — one directory per schema (P0 §4).
- Platform-wide revisions use the `plm` token instead of a phase schema (the
  baseline that creates the nine schemas is `migrations/versions/plm/`).
- Migrations are a **single-writer** resource: exactly one migration may be in
  flight across the whole repository at a time, even in a parallel wave. Never
  dispatch two migration-creating steps concurrently — the chain forks.
- A phase may only alter its own schema (`market`, `ar`, `experiments`, `music`,
  `qa`, `media`, `youtube`, `distribution`, `analytics`); cross-schema changes
  are coordinated through the seam registry, never by editing another phase's
  revision.
- Every `upgrade()` must carry a working `downgrade()`: `alembic downgrade base`
  followed by `alembic upgrade head` must round-trip.

## Revision naming

Revision files are named `<schema>_<revision>_<slug>.py`:

- `<schema>` — one of the nine phase schemas, or `plm` for platform-wide
  revisions; it must match the directory the file lives in;
- `<revision>` — the alembic revision id (short and sortable, e.g. `0002`);
- `<slug>` — a short snake_case description (e.g. `initial_tables`).

Example: `migrations/versions/market/market_0002_initial_tables.py`.
`tests/unit/test_migrations_layout.py` enforces the convention.

## Running

`env.py` reads the connection string from `plm_platform.config` (process
environment, then `.env` at the repo root); it never reads `os.environ` and
never hardcodes a URL. Offline mode (`alembic upgrade head --sql`) is supported.

```
alembic upgrade head      # create the phase schemas / apply later revisions
alembic downgrade base    # drop everything this chain created
```

`tests/integration/test_migrations.py` exercises the real chain against the
docker compose postgres and skips with an explicit reason when it is down.
