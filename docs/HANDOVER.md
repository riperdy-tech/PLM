# PLM handover — 2026-10-16 (paused mid-R5)

Work was paused at the owner's request so Docker can be used for another project. Read
this file first, then `AGENTS.md`, `docs/STATUS.md`, and the plan
`docs/reviews/2026-10-13-system-review-and-remediation-plan.md`.

## 1. Where things stand

| Stage | Branch | State |
|---|---|---|
| R0 truth baseline | `review/r0-truth-baseline` | pushed, CI green |
| R1 fail-closed seams | `review/r1-fail-closed-seams` | pushed, CI green |
| R2 correctness / determinism | `review/r2-correctness-determinism` | pushed, CI green |
| R3 runtime | `review/r3-runtime` | pushed, CI green |
| R4 media / quality | `review/r4-media-quality` | pushed, CI green; 50/50 integration locally |
| **R5 full chain** | `review/r5-full-chain` | **in progress, not pushed, gate not met** |
| R6 live adapters | — | not approved, not started |

Each branch builds on the previous one. PRs are stacked (R5's PR, not yet opened, will
target `review/r4-media-quality`).

### R5 commits already on the local branch (not pushed)

- `c657e05` supply: playlists planned to a declared 45–75 min range; idempotent P3/P4;
  guarded generation.
- `444548d` live operator dashboard with the six PART XVII views (`--demo` for demo data).
- `2a16dfe` pipeline: the whole chain runs over HTTP (upload, distribution, processing,
  outcome evaluation, reward dispatch, learn, extend), top-up, single budget reservation.

### Uncommitted work in the tree (the full-chain gate test and what it exposed)

32 modified files plus new files. Nothing here is committed. Do **not** discard it.

New files:
- `tests/integration/test_r5_full_chain.py`: the Gate R5 test (marker `full_chain`).
- `tests/unit/market/test_ci_fixture_connector.py`
- `tests/unit/test_youtube_channel_route.py`

Defects found by the full-chain test and fixed (each has a unit test):
1. P1 `ENV=ci` fixture corpus formed no arm. It now uses the multi corpus (two arms).
2. P4 mock audio was a sine with about 10 distinct recordings, so P5 matched every later
   candidate against the internal catalog. It is now a seeded chord loop
   (`mock-chord-synth-2`).
3. P6 ignored `default_render_profile`. It is now `PLM_MEDIA_RENDER_PROFILE`, validated
   at boot; compose uses `preview_360p_v1`.
4. P6 re-render under another profile collided on the playlist session key (500).
5. P6 timed-out retries rendered concurrently, and the container was OOM-killed. Renders
   are now serialised per process.
6. P5 concurrent duplicate evaluation gave an IntegrityError 500. It now replays the
   stored evaluation.
7. P5 internal-catalog matching was about 13 s per candidate. It is now vectorised and
   cached, about 1.2 s.
8. New operator route `PUT /youtube/channels/{channel_id}`.
9. P6 held every offered recording in memory: about 4.5 GB peak, OOM on a 74-min
   playlist. It now measures them one at a time and reads only the selected ones at
   render: about 2.7 GB peak.

Config: `config/dev/mock_outcomes.json` pins the arm effects for the two CI arms (lofi
1.4, rnb 0.25). CI gets a separate `full-chain` job with a 90-min timeout
(`.github/workflows/ci.yml`). `-m integration` skips `full_chain`, and
`-m "not integration"` deselects it.

### In progress when stopped: P3 playlist-length policy (incomplete, unverified)

The owner decided playlists are 45–75 min (ADR-0036). The genre templates still
declared lofi/jazz 60–120 min and ambient 60–180, and a directive without a
`duration_bucket` inherited them, so lofi playlists came out at about 74 min under a
60–120 range.

The agent was making the owner's range the single policy source:
- `playlist_duration_policy.default_bucket = 45_75m` in `genome_templates.json`.
- An open brief gets the policy range.
- A bucket outside the policy is refused (`playlist_duration_outside_policy`); nothing is
  clamped.
- A template outside the policy fails to load.

Files: `services/experiments/genome/engine.py`, `genome_templates.json`, ADR-0036,
`tests/unit/experiments/test_playlist_supply.py`. The edit was cut off by the usage limit
and **no gate has been run on it**. Next session: finish it, check every template
declares the policy range, add unit tests for both refusal paths, run the unit gates.

Root-cause note from the agent: P2 copies `duration_bucket` from P1's arm taxonomy, and
P1 sets it only from title text, never from the measured video duration, so most
directives carry none. Decide whether P1 should derive it from measured duration; that
would be a P1 change, recorded in STATUS.

## 2. Gate R5 evidence so far

- Run 6 of the full-chain test went from P1 to LEARNING in 34 min: 7 of 8 tests passed.
  The failure was a UUID-vs-str comparison in the lineage test itself; every lineage hop
  joined. The fix is in the tree.
- Proven by run 6:
  - both runs reach LEARNING in time-skipped 28 days
  - the strong arm's posterior rises above its prior and above the weak arm's
  - the second cycle moves toward the strong arm
  - rewards are DELIVERED through the outbox
  - duplicate dispatch, replayed payload and update re-run change nothing
  - a preflight failure writes nothing
  - playlists stay inside their declared ranges
  - no candidate is quarantined against the catalog
- **Not yet done:** a fully green run. Run 7 was in progress and its result is unknown;
  it predates the P3 policy change. Two consecutive green runs are needed for
  stability. Also still to do: the full integration tier, the media tier in the
  container, and the unit gates after the P3 change.
- The test takes 35–45 min at production content scale (45–75 min playlists, about 200
  P5 candidates). The owner chose to keep production scale.

**Caution:** the uncommitted `docs/STATUS.md` already says the P0 §1 sentence is true on
the compose stack. That is premature until a fully green full-chain run exists. Keep the
wording or soften it to match the evidence before committing.

## 3. Open findings to record or own

- P1 C1 evidence `top_observation_ids` holds raw observation ids, but
  `/market/v1/observations/{id}` resolves normalized ids. The lineage query joins through
  `market_raw_observations`. Changing the field's meaning needs an ADR (open defect).
- P7 needed an operator route for channel setup (now added). The dev DB had no active P2
  policy; the full-chain test activates one.
- `test_migrations` downgrade fails once statements with NULL `track_id` exist; it is
  fine on a fresh CI DB.
- The new P7/P8/P9 pipeline endpoints may merit an ADR (contract registry).
- Compose renders 360p (about 4 min for a 53-min playlist); production 1080p takes about
  23 min.

## 4. Remaining steps to close R5

1. Finish the P3 playlist-length policy and its tests (section 1).
2. Run `ruff check .`, `mypy packages services`, `pytest -m "not integration"`.
3. `docker compose up -d --wait --force-recreate` (with the env vars below), then
   `plm migrate`, then the integration tier. Then the full-chain test twice (about 40 min
   each). Then `docker compose run --rm --no-deps -e PLM_REQUIRE_MEDIA_TOOLS=1 worker
   pytest -q -m media`.
4. Make `docs/STATUS.md` match the evidence (test counts, R5 rows, the P1 lineage
   defect, the duration-bucket note).
5. Commit in logical pieces, push `review/r5-full-chain`, check CI (including the new
   `full-chain` job), and open the R5 PR against `review/r4-media-quality`.
6. Stop and wait for owner approval before R6.

## 5. Local environment notes

- The PLM compose stack is **stopped** (`docker compose stop`; volumes kept). Restart it
  with the token exported:
  `PLM_SERVICE_TOKEN=change-me-dev-service-token docker compose up -d --wait --force-recreate`.
- Host-side test env (`.env` uses `localhost`, which is slow on this machine because of
  IPv6):
  `DATABASE_URL=postgresql://plm:<pw from .env>@127.0.0.1:5432/plm MINIO_ENDPOINT=127.0.0.1:9000 TEMPORAL_HOST=127.0.0.1:7233 PLM_SERVICE_TOKEN=change-me-dev-service-token`.
- The dev DB is shared and accumulates releases across runs (about 700 in the internal
  catalog). The full-chain test namespaces its ids.
- Write files with LF line endings (`newline="\n"`). Migration ids must be 32 characters
  or fewer; the head is `0037_dashboard_views`.
- `docs/Self-Improving Trading System Research.md` is untracked and unrelated to PLM
  work. Leave it, or commit it on the owner's say-so.

## 6. Moving to another PC

Push the R5 branch first (step 5 above), or copy the working tree, because the
uncommitted work exists only on this machine. `.env` is git-ignored; copy it privately.
Recreate `.venv`. Rebuild the stack with `docker compose up -d --wait` and `plm migrate`.
