# P0 — Master Integration Plan: Build P1 → P9 End to End

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not deviate from the seam contracts in §5 — they are the mechanism by which nine independently-written phase specs become one system.

**Goal:** Build the Autonomous AI Music Media Engine as one monorepo whose nine phases (P1 market intelligence through P9 analytics/economics) connect end to end, mock-first, with frozen interfaces at every seam.

**Architecture:** A single Python monorepo. One PostgreSQL database with one schema per phase. One MinIO deployment with one namespace and a fixed prefix layout. Temporal for durable workflows. FastAPI for every phase's HTTP surface. Phases communicate only over HTTP contracts and events — never by reading another phase's tables.

**Tech stack (fixed by architecture.md PART XIX; do not renegotiate):** Python 3.12, FastAPI, PostgreSQL 16, Temporal, MinIO, FFmpeg 7, librosa-class DSP, Prometheus/Grafana, Docker Compose for local development.

**Spec:** `architecture.md` (master spec, parts I–XXVII) plus the nine phase specs `P1_market_intelligence_implementation_plan.md` … `P9_analytics_economics_engine_implementation_plan.md`. All ten move to `docs/` in Wave 0 (W0.10).

**Status:** Awaiting human review. No code exists yet. Nothing in §5–§9 has been implemented.

---

## §0 How to read this plan

**Two numbering systems collide, and every phase brief flagged it.** Fix it once:

| Written as | Means |
|---|---|
| `PHASE-Pn` | A build phase: `PHASE-P1` … `PHASE-P9`, one per plan file. This plan's subject. |
| `ARCH-Pnn` | A capability item in architecture.md parts I–XVII (`ARCH-P17` = "Publishing engine"). |

`PHASE-P7` (YouTube engine) and `ARCH-P7` ("Learn what success means") are unrelated. From Wave 0 onward, all new documents, code comments, ADRs, and commit messages use the qualified forms. Existing phase files are not rewritten; the mapping is recorded once in `docs/contracts/PHASE_MAP.md` (W0.10).

**Authority order when documents disagree:**

1. `docs/contracts/CONTRACT_REGISTRY.md` (frozen in Wave 0, amended only by an ADR) — for cross-phase seams.
2. The owning phase's plan file — for that phase's internals, and for its own table names and endpoints.
3. `architecture.md` — for cross-cutting principles, guardrails and vocabulary.
4. This plan — for sequencing and for seam resolutions, until superseded by a registry amendment.

**Vocabulary rule.** architecture.md PART XX's table list is an illustrative sketch. Where a phase plan defines concrete table names, the phase plan wins. This is Ruling R3 and it resolves the same class of conflict in P2, P5, P8 and P9 at once.

---

## §1 Success criteria for the program

The program is complete when this sentence is true of the running system, with no human step in the middle:

> A market observation collected by P1 becomes a scored opportunity, then a production directive, then an experiment and content package, then generated music, then a gate decision, then a playlist-level media package, then a published YouTube video and a distributed release, then measured revenue and cost attributed back to the originating arm — and that outcome updates the allocation policy that chose the arm.

Anything that breaks the chain — a renamed field, a dropped experiment ID, a duplicated reward — is a defect of the same severity as a crash. That is the whole point of this plan.

**Explicitly not in scope:** `ARCH-P10` recursive optimization as a separate phase (see §9 R12), architecture roadmap phases P10–P12, and any real external account or credential (see §10).

---

## §2 Global constraints

Copied from architecture.md; every task's requirements implicitly include them.

- **Determinism.** Scoring, feature calculation, sequencing, and the gate must be reproducible from stored inputs plus a version string. Any nondeterminism is a bug, not a convenience.
- **Provenance.** Every derived value traces to immutable raw evidence, a collector identity, a collection timestamp, and a transformation version.
- **Immutability.** Raw observations, artifacts, manifests, evaluation snapshots, publish intents, release packages, and provider reports are append-only. Corrections are new versions.
- **No silent transformation.** No silent truncation, silent downgrade, silent substitution, silent retry of a creative failure as a transport retry, or silent policy change. Each has a defined "blocked with reason code" path instead.
- **Unknown ≠ zero ≠ not-ready.** Every consumer must represent all three distinctly.
- **Human hard-stops:** payment method changes, KYC, platform-policy disputes, copyright disputes, account-security events, and everything on the `ARCH-P29` list stop publication and wait for a human.
- **Platform compliance:** synthetic-media disclosure is a first-class field (`ARCH-P18`, `PHASE-P7` gate E), never inferred from genre; no prompting by naming living or protected artists; no voice cloning without documented authorization; no artificial stream or engagement generation.
- **Provider neutrality:** music generation (`PHASE-P4`), distribution (`PHASE-P8`) and market sources (`PHASE-P1`) sit behind interfaces; the business must not require a specific vendor.
- **Secrets** live only in the environment or the secret store. Never in code, fixtures, logs, evidence bundles, or committed files.
- **Cost is a first-class metric** with per-run counters and enforced limits from Wave 0 onward.

---

## §3 Review focus across the whole program

These are the failure modes that no single phase's tests catch, because each lives *between* two phases. Every line names the input or condition, the behaviour a reasonable operator expects, and the task that owns its test.

1. **Missing data rendered as zero or as a pass.** An absent metric, an unmeasured arm, a missing rights field. Expected: `unknown`/`not_ready`, never `0`. Owning test lives in `plm_contracts` (W0.3) and is required by every phase's contract suite.
2. **A retry or duplicate callback creates a second real-world effect.** Double submit to a distributor, double upload, double posterior update. Expected: exactly-once effect with idempotency keys at every external boundary. Owning tests: W0.9 template, `PHASE-P7` P7.6, `PHASE-P8` P8.7, `PHASE-P2` P2.11.
3. **Experiment identity lost in transit.** `experiment_id`/`content_arm_id`/`content_genome_id`/`production_directive_id` dropped at a handoff. Expected: every published artifact is attributable to an arm. Owning test: contract test on each seam in the registry (C2 → C6 → C7), exercised end to end in Wave 6.
4. **A blocked or quarantined candidate reaching publication by another path.** Expected: the gate is unbypassable; the only route past it is the audited override. Owning tests: `PHASE-P6` P6.3, `PHASE-P8` AC-11, plus a Wave 6 end-to-end negative test.
5. **Time and money arithmetic errors.** Timezone-normalized schedules, 24h/72h/7d/28d/90d windows over non-stationary data, accounting periods, multi-currency. Expected: windows and periods explicit on every metric, currency explicit on every amount. Owning tests: `PHASE-P9` P9.1/P9.9, `PHASE-P7` gate F.

---

## §4 The repository layout

Reconciled from the nine phase specs. Phase specs that "defer to the existing repo structure" (P4, P5, P8) now have one.

```text
/                          repo root (was the PLM folder)
  pyproject.toml           single uv-managed project, path deps on packages/*
  docker-compose.yml       postgres16, minio, temporal, temporal-ui, worker, ffmpeg
  .env.example
  Makefile                 make up / test / lint / contract / doctor
  docs/
    architecture.md
    P1_..._implementation_plan.md ... P9_..._implementation_plan.md
    P0_master_integration_implementation_plan.md        (this file)
    contracts/CONTRACT_REGISTRY.md   PHASE_MAP.md   DECISION_LOG.md
    adr/                   ADR-0001-*.md ...
  packages/
    plm_contracts/         shared enums, IDs, envelopes, error shape, event base
    plm_platform/          config, secrets, db, storage, temporal, obs, cli
  services/
    market_intelligence/   PHASE-P1   (§6 spec layout)
    autonomous_ar/         PHASE-P2
    experiments/           PHASE-P3
    music_factory/         PHASE-P4
    quality_gate/          PHASE-P5
    media_factory/         PHASE-P6
    youtube_publishing/    PHASE-P7
    distribution/          PHASE-P8
    analytics/             PHASE-P9
  migrations/              one alembic chain, one directory per schema
  tests/
    contract/              cross-phase seam tests (owned by this plan)
    e2e/                   the Wave 6 end-to-end chain
    fixtures/              shared synthetic market/generation/report fixtures
  .superpowers/sdd/        git-ignored: ledgers, briefs, reports, review packages
```

Rules: one schema per phase (`market`, `ar`, `experiments`, `music`, `qa`, `media`, `youtube`, `distribution`, `analytics`); a service may read only its own schema plus `plm_platform` tables; cross-phase reads go over HTTP; `/tests/contract/` is the only place two services are imported together.

---

## §5 The ten seams — frozen contracts

Each seam has a ruling. Rulings are binding; `CONTRACT_REGISTRY.md` (W0.3/W0.10) records the exact field lists and is the artifact both phases are tested against. Status is what the briefs said before this plan existed.

### C1 · PHASE-P1 → PHASE-P2 (market opportunity)
**Status: compatible, two gaps.** P1 §25 API and P2 §4/§46 field expectations agree. Gaps: no base path or auth stated; P1's taxonomy dimension set disagrees with its own AC-05.
**Ruling R14:** P1 §14.2's 13 dimensions are authoritative (`VocalGender`, `BPMRange` retained); AC-05's "10 dimensions" is corrected to match. **Ruling R15:** P1 mints and owns `arm_id` (identity, taxonomy, market score); P2 owns arm *state*, posteriors and exposure. Base path `/market/v1`, auth via service token from `plm_platform`. Freeze as C1 before P2 starts.

### C2 · PHASE-P2 → PHASE-P3 (production directive) — **broken**
**Status: field-level mismatch on six of twelve fields.** P2 §32/§120 (`quantity`, `allocation_type`, `brief`, `expires_at`, `reason_codes`, `feature_snapshot_id`) versus P3 §10 (`requested_experiment_count`, `exploration_level`, `required_constraints`, `recommended_attributes`, `production_deadline`, `selection_reason`, `market_context`). P2's directive also lacks `budget_limit`, which P3 requires; P3 omits P2's `expected_value`/`uncertainty`, which P3 §4.5 wants.
**Ruling R4:** P2's directive is the wire format; P2 extends it with `budget_limit`, `expected_value`, `uncertainty`, `market_context`, `selection_reason`, and a versioned `directive_version`. P3's adapter maps: `requested_experiment_count ← quantity`, `exploration_level ← allocation_type` (enum map), `production_deadline ← expires_at`, constraints/attributes ← arm hard constraints + creative search region. Frozen before either P2.1 or P3.4.

### C3 · PHASE-P3 → PHASE-P4 (track spec)
**Status: drift.** `role`/`track_role`, `duration_range`/`requested_duration`, `*_profile`/`*_spec`; P4 needs `candidate_target`, `priority`, `budget_limit`, `provider_policy`, which P3 does not emit.
**Ruling R5:** the canonical `music_specs[]` payload is the union; P3 emits every P4 §7 key (deriving `candidate_target`/`priority`/`budget_limit` from the budget envelope, `requested_duration` from `duration_range` by a documented rule, `provider_policy` from the experiment's provider constraints). P4 must reject only what P4 §7 says it rejects.

### C4 · PHASE-P4 → PHASE-P5 (candidate ready) — **broken**
**Status: event compatible; retrieval missing.** P4 §68 emits `music_candidate_ready_for_p5` v1 correctly. But P5 §7 needs prompt metadata, generation attempts, artifact manifest, provenance chain and license snapshot, and P4 §67 exposes none of them; P4's `rights_snapshots` lacks plan/tier, account identity and timestamps that P5 §33 requires — without them P5 §117 defaults every candidate to quarantine.
**Ruling R6:** P4 adds read endpoints `GET /music/candidates/{id}/attempts`, `/license`, `/artifacts`, `/provenance`, and extends `rights_snapshots` with `plan_or_tier`, `account_identity_ref`, `terms_version`, `created_at`, `downloaded_at`. This is a P4.1/P4.2 amendment and must land before P5.3.

### C5 · PHASE-P5 → PHASE-P6 (release manifest) — **most severe break**
**Status: different shapes entirely.** P6 §4 expects one playlist-level manifest (`release_manifest_id`, `tracks[]`, `brand_id`, `required_duration_minutes`, four `*_profile` versions). P5 §77/§138 emits one per-candidate manifest with none of those. No phase owns assembling the playlist-level manifest. Vocabulary also drifts: P6 says `provenance_status: "cleared"`, P5 says `PASSED`.
**Ruling R7:** P5 remains per-candidate and gains a stable `p5_manifest_id`. **P6 assembles** the playlist-level manifest: it reads P3's `playlist_spec` + `brand_profiles`, and for each track calls `GET /qa/release-manifests/{candidate_id}`, accepting only `PASSED` / `PASSED_WITH_WARNINGS` and rejecting any track that is `QUARANTINED` / `REJECTED` (audited override excepted). P6's `required_duration_minutes` comes from the playlist spec; the four `*_profile` versions come from P3's brand/spec tables, **not** from P5. P6 §4 is amended accordingly.
**Ruling R8:** `cleared` is not a state. Canonical verdict vocabulary is P5's four dispositions plus `CLEARED_BY_OVERRIDE`. "Cleared" in P6/P8 prose means "PASSED, or PASSED_WITH_WARNINGS where that phase's own policy permits warnings".

### C6 · PHASE-P6 → PHASE-P7 → PHASE-P8 (publish and distribution identity)
**Status: P6→P7 workable; experiment linkage incomplete.**
P7 §9 explicitly tolerates field-name drift if semantics match; its own conflicts are internal (a `PROCESSING_TIMEOUT` state missing from the §12 list; singular/plural table naming).
**Ruling R9:** experiment identity is carried end to end. P7's `youtube_video_published` event (§65) and P8's `P8 → P9` record (§71) must both carry `experiment_id`, `experiment_family_id`, `content_arm_id`, `content_genome_id`, `production_directive_id`, plus `media_package_id`. P9 §38 requires all of these; today neither producer sends them, so the learning loop would silently break at the last hop.
Also: P7's §12 state list gains `PROCESSING_TIMEOUT`; P8 consumes P5 clearance plus the P6 manifest and P7's publication record.

### C7 · PHASE-P8 → PHASE-P9 → PHASE-P2 (the feedback loop) — **open**
**Status: the loop does not close.** P8→P9 is fine (`release_id`, `isrcs[]`, `deliveries[]`, `source_package_hash`). But P2 expects `POST /ar/results` from P9, and P9 defines no P2-facing contract — P2 §72, P9 §155/§156. Raw-statement ownership also overlaps (P8 §80 vs P9 §53–§54).
**Ruling R10:** P8 stores and versions raw provider reports and exposes references; P9 owns parsing into canonical facts and the immutable archive of what it ingested. P8 must not compute revenue.
**Ruling R11:** **P9 posts normalized rewards to P2's `POST /ar/results`.** P9 owns the reward arithmetic (`normalized_reward`, `reward_version`, window, maturity, attribution confidence, quality status); P2 owns posterior updates. This is the loop-closing contract and it is tested end to end in Wave 6.

### C8 · PHASE-P9 → PHASE-P10 (deferred seam)
**Status: out of scope.** P9 §155 exposes a stable feature view; P9 §156 says "P10 decides". P2 owns allocation in this build (architecture PART XXVI's P10 is deferred).
**Ruling R12:** keep `GET /analytics/p10/features` and the §155 envelope as a frozen seam named for its future consumer; record P10 as deferred, not cancelled.

### C9 · Cross-phase storage layout
**Status: three divergent prefixes.** P4 §29 (`music/{brand_id}/{experiment_id}/…`), P5 §193 (`artifacts/candidates/…`), P6 §42 (`media/{brand}/{experiment}/{package_id}/…`).
**Ruling R13:** one namespace, top-level prefixes `market/`, `experiments/`, `music/`, `qa/`, `media/`, `reports/`, `analytics/`. Producers write immutable content-addressed artifacts; consumers read only by the URI recorded in a database row; checksums are mandatory on every artifact and verified on read.

### C10 · Missing engineering prelude (PHASE-P0)
**Status: no P0 plan file exists.** Seven briefs note infrastructure that is assumed but specified nowhere: OAuth applications, secret store, durable workflow engine, FFmpeg, audit states.
**Ruling R16:** Wave 0 below *is* the engineering half of P0. The account/OAuth/audit half is a human task list in §10 — it blocks only live-mode enablement, never the mock-first build.

---

## §6 Build waves

**Dependency chain (architecture PART XXVII):** `P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10`. Phases after the first are gated on their predecessor's *seam contract being frozen and implemented*, not on the predecessor's whole definition of done. That is what allows the limited parallelism marked below without breaking anything.

**Total scope: 136 phase steps + 12 Wave 0 tasks.** State this plainly: this is a program, not a session. Each wave ends at a gate; the ledger records what is actually done.

### Wave 0 — Foundation · 12 tasks · blocks everything

- [ ] **W0.1 Repo scaffold.** `git init`, `pyproject.toml`, `packages/` + `services/` trees, ruff/mypy/pytest config, `.gitignore` including `.superpowers/`. Test: `make lint` and `make test` run green on an empty tree.
- [ ] **W0.2 Docker Compose stack.** `postgres:16`, `minio`, `temporal`, `temporal-ui`, a worker image with pinned FFmpeg 7 and librosa deps. Test: `docker compose up -d` is healthy; FFmpeg version inside the image matches the pin.
- [ ] **W0.3 `plm_contracts` package.** Base event envelope, error shape (`code`, `field_paths`, `message` — P3 §81), cursor pagination, `as_of`/`*_version` envelope (P9 §89), canonical enums (dispositions, statuses, window, currency), `unknown`/`zero`/`not_ready` tri-state type. Test: unit + the Review-Focus-1 contract test.
- [ ] **W0.4 Config and secrets.** Layered env loader, `.env.example`, fail-fast validation, no secret ever logged. Test: missing-secret boot failure is explicit and names the variable, never its value.
- [ ] **W0.5 Database and migrations.** One alembic chain, schema-per-phase, migration ownership rules, seed fixtures. Test: `alembic upgrade head` then `downgrade base` round-trips.
- [ ] **W0.6 Object storage.** MinIO client, prefix constants for C9, checksum + immutability enforcement, no-overwrite policy. Test: writing the same key twice fails loudly.
- [ ] **W0.7 Temporal skeleton.** Worker, task-queue naming (`ph1.market`, `ph2.ar`, …), idempotency and retry conventions, schedule registration. Test: a sample durable workflow survives a worker restart mid-run.
- [ ] **W0.8 Observability.** Structured JSON logging with correlation IDs, Prometheus metrics, per-run cost counters (`api_calls`, `llm_tokens`, `storage_bytes`, `render_seconds`). Test: a sample run emits one correlated log line and its counters.
- [ ] **W0.9 Test harness.** Fixture conventions, contract-test template, mock HTTP/provider utilities, golden-file helpers. Test: the template contract test passes against a stub.
- [ ] **W0.10 Documents and governance.** Move the ten `.md` files to `docs/`, write `PHASE_MAP.md` (dual numbering), `CONTRACT_REGISTRY.md` (skeleton with all ten seam entries and their rulings), `DECISION_LOG.md`, ADR template. Test: every `ARCH-Pnn` reference in the phase files resolves through `PHASE_MAP.md`.
- [ ] **W0.11 CI.** GitHub Actions: lint, types, unit, contract, migration check. Test: CI green on the scaffold.
- [ ] **W0.12 `plm` CLI.** Typer app, one subcommand group per phase, plus `plm doctor`. Test: `plm doctor` reports the health of Postgres, MinIO, Temporal and the migration head.

**Gate W0:** `docker compose up -d && make doctor` green, CI green, ten seam entries exist in the registry. Nothing proceeds until this holds.

### Wave 1 — PHASE-P1 Market Intelligence · 14 steps (§39: P1.1–P1.14) · mock-first

Steps: P1.1 domain contracts · P1.2 migrations · P1.3 YouTube connector (fixtures + quota tracking) · P1.4 raw observation store · P1.5 normalization/entity resolution · P1.6 seed manager · P1.7 taxonomy (13 dims per R14) · P1.8 feature engineering · P1.9 opportunity scorer · P1.10 API (`/market/v1`) · P1.11 scheduler · P1.12 reports/observability · P1.13 tests · P1.14 acceptance run (mock cycle + quota-safe real smoke once credentials exist).

**Seam work in this wave:** freeze and implement C1 (P1 §25 API + R14/R15 ownership of `arm_id`).
**Gate P1:** P1 §45 + AC-01…AC-14; `tests/contract/test_c1_market_to_ar.py` passes against a fixture-backed API. (No YouTube credentials required — P1.14's real smoke test is marked blocked-pending-credentials, not skipped silently.)

### Wave 2 — PHASE-P2 and PHASE-P3 · 16 + 13 steps · partial parallelism

**Pre-step W2.0 — freeze C2.** Small, dedicated contract task: write C2 into the registry, amend P2's directive schema and P3's adapter mapping (R4) as an ADR. Reviews before either phase starts. Without this the two phases cannot connect.

Then:
- **P2** P2.1 … P2.16, ending with its dry-run and shadow modes (P2 §115/§116).
- **P3** P3.1 … P3.13. P3.1–P3.3 (domain, version layer, DB) may run **in parallel** with P2.1–P2.3 — disjoint files, frozen interface C2. P3.4 (the P2 adapter) is gated on C2 being implemented in P2.

**Gate P2:** P2 §118 ACs + §97 deterministic fixture producing directives. **Gate P3:** P3 §90 + ACs + P3.13 smoke. Both must pass `tests/contract/test_c2_ar_to_experiments.py`.

### Wave 3 — PHASE-P4 and PHASE-P5 · 16 + 18 steps · partial parallelism

**Pre-step W3.0 — freeze C3 and C4.** Includes R6's P4 amendment (the four read endpoints and the extended `rights_snapshots`).
- **P4** P4.1 … P4.16, mock provider first (P4.4), real adapter later (P4.8), ending at the autonomous-mode gate.
- **P5** P5.1 … P5.18. P5.4–P5.10 (technical/musical/lyric/provenance/policy detectors) may run in parallel with P4.6–P4.10 using fixtures, because they consume P4's frozen event, not its running code. P5.3 (P4 adapter) is gated on C4.

**Gate P4:** P4 §111 ACs + §112 acceptance run. **Gate P5:** P5 §203 + §178 hundred-candidate batch with the 60/15/10/10/5 class split inside detector tolerance.

### Wave 4 — PHASE-P6 · 17 steps, then PHASE-P7 · 14 steps

**Pre-step W4.0 — freeze C5.** This is the largest seam ruling (R7/R8): amend P6 §4, re-scope P6.3, define the playlist-level manifest assembly in P6, and settle the verdict vocabulary.
- **P6** P6.1 … P6.17, render pipeline against the FFmpeg container, preview-then-full with cost ceilings (P6 §51).
- **P7** P7.1 … P7.14. P7.1–P7.4 (domain, persistence, OAuth scaffolding, client abstraction) may start in parallel with P6.6–P6.17 — disjoint files, and P7's preflight consumes the frozen manifest, not P6's code. P7.5 onward is gated on P6's manifest.

**Gate P6:** P6 §53 DoD + §57 operational run + §52 experiment-integrity scenarios A/B/C. **Gate P7:** P7 §78 ACs + fully mocked P6→P9 path (§69); live enabling is §10 work.

### Wave 5 — PHASE-P8 and PHASE-P9 · 16 + 12 steps · partial parallelism

**Pre-step W5.0 — freeze C6, C7, C8.** Experiment-linkage propagation (R9), report ownership (R10), the reward path (R11).
- **P8** P8.1 … P8.16, mock provider (§69) with the F01–F12 failure scenarios, staged rollout modes 0–2 before any real submission.
- **P9** P9.1 … P9.12. P9.1–P9.5 (domain, schema, the three connectors) may run in parallel with P8.1–P8.6 — disjoint files, C6/C7 frozen. P9.6 (identity/reconciliation) onward is gated on P8's stable record.

**Gate P8:** P8 §77 ACs + §81's stated invariant. **Gate P9:** P9 §212 ACs + §208 reconciliation fixture (3 YouTube revenue records, 5 DSP royalty records, 10 cost records, 1 corrected statement).

### Wave 6 — Loop closure and end-to-end acceptance

- [x] W6.1 Wire P9 → `POST /ar/results` (R11) and run the end-to-end chain: P1 fixture → P2 directive → P3 package → P4 candidate (mock provider) → P5 clearance → P6 media package → P7 publish (fully mocked YouTube) → P8 submission (mock distributor) → P9 facts → reward → P2 posterior update. Assert the second P2 cycle allocates differently.
- [x] W6.2 The negative chain: a `REJECTED` candidate must not reach P6, a preflight failure must not reach YouTube, a duplicate webhook must not double-count revenue. These are Review Focus 3 and 4.
- [x] W6.3 Whole-program review on the most capable available model, then `superpowers:finishing-a-development-branch`.

---

## §7 Execution protocol

Adapted from `superpowers:subagent-driven-development` for this harness.

- **Ledger.** One per plan at `.superpowers/sdd/progress.md`, first line `# SDD ledger — plan: <path>`. Every dispatch records base commit, model, outcome; every finding records its ruling as `Ruling: <what> — <why> — <cost if wrong>`. Conversation memory does not survive compaction; the ledger does. After compaction, trust the ledger and `git log` over recollection.
- **Pre-flight scan before each wave.** For every pair of tasks sharing a file or a seam: producer fields versus consumer fields, in a table written to the ledger. Rule on everything found *before* dispatch, not during review.
- **Dispatch.** One implementer subagent per step, given: the step's spec section, the frozen contract entry, the file list, the acceptance test, and the constraint that it may not edit another phase's files. Everything pasted into a prompt stays in my context forever — hand artifacts over as files.
- **Review after every step.** A fresh reviewer subagent gets the diff (`BASE..HEAD`) and the spec section, and returns findings by severity; the review package is written to a file, not to the transcript.
- **Fix rounds.** Because `runSubagent` is synchronous and stateless, an implementer **cannot be resumed**. A fix round is a fresh dispatch whose prompt contains the reviewer's findings verbatim. Round 4–5 of a stuck task escalates to a stronger model and a tighter brief.
- **Continuity.** No check-ins between steps. Continuous execution until a wave gate, or until one of: an irreversible/destructive action, a security-sensitive action, a side effect outside the repo, or a plan so broken that every path is a guess.
- **Migration serialization (supervision rule).** Alembic is a single-writer resource: never dispatch two migration-creating steps concurrently, even in a parallel wave, or the chain forks. Queue them.
- **Model selection ruling (R18).** This harness exposes no reliable model parameter for subagents; dispatches inherit the session model. Recorded as a ruling with its cost implication: reviews still get fresh contexts, but the cheap-tier optimizations the skill recommends are unavailable. Revisit if the harness changes.
- **Parallel dispatch** is permitted only when both workstreams have a frozen interface, disjoint file sets, and no migration in flight. The wave lists above mark the only approved parallel pairs.

---

## §8 Phase gates summary

| Wave | Phase | Steps | Gate artifact | Blocks live mode only |
|---|---|---|---|---|
| 0 | Platform | 12 | `plm doctor` green, CI green, 10 seams registered | — |
| 1 | P1 | 14 | §45 + AC-01…14 + `test_c1` | real YouTube smoke |
| 2 | P2, P3 | 29 | §118 / §90 + `test_c2` | — |
| 3 | P4, P5 | 34 | §111 / §203 + §178 batch | real music provider |
| 4 | P6, P7 | 31 | §53 / §78 + mocked path | YouTube audit, public publish |
| 5 | P8, P9 | 28 | §77 / §212 + §208 fixture | real distributor submission |
| 6 | Loop | 3 | end-to-end + negative chain | — |

---

## §9 Rulings index

| # | Ruling | Seam |
|---|---|---|
| R1 | Two numbering systems qualified as `PHASE-Pn` / `ARCH-Pnn` | all |
| R2 | Authority order: registry → phase plan → architecture → this plan | all |
| R3 | Phase plans own table names; architecture PART XX is illustrative | P2, P5, P8, P9 |
| R4 | P2's directive is the wire format; P3 adapts; P2 adds `budget_limit`, `expected_value`, `uncertainty` | C2 |
| R5 | Track spec is the union; P3 emits every P4 §7 key | C3 |
| R6 | P4 adds four read endpoints and extends `rights_snapshots` | C4 |
| R7 | P5 stays per-candidate and gains `p5_manifest_id`; **P6 assembles** the playlist manifest; profiles come from P3 | C5 |
| R8 | `cleared` is not a state; canonical vocabulary is P5's four dispositions + `CLEARED_BY_OVERRIDE` | C5 |
| R9 | Experiment identity carried through P7 event and P8 record to P9 | C6 |
| R10 | P8 stores/versions raw reports; P9 parses to facts and own revenue | C7 |
| R11 | P9 posts normalized rewards to P2's `POST /ar/results`; P2 owns posteriors | C7 |
| R12 | P10 deferred; the §155 feature seam is kept | C8 |
| R13 | One storage namespace with seven prefixes, content-addressed, checksummed | C9 |
| R14 | P1's 13 taxonomy dimensions are authoritative; AC-05 corrected | C1 |
| R15 | P1 mints `arm_id`; P2 owns arm state and posteriors | C1 |
| R16 | Wave 0 is the engineering half of P0; accounts/OAuth/audits are human tasks | C10 |
| R17 | FFmpeg 7 pinned in the worker image; GPU profiles have a recorded CPU fallback | P6, P5 |
| R18 | Reviewers always get fresh context; no cheap-tier model routing available in this harness — recorded, not fixed | all |
| R19 | `brand_id` is a first-class field everywhere; one brand seeded for the first build | P1–P8 |
| R20 | One `plm` CLI with a group per phase, for the phases that define no CLI (P1, P2, P3, P6, P9) as well as P5's | all |

Each ruling becomes an ADR in `docs/adr/` during W0.10. Amend the registry, never the prose.

---

## §10 Human task list (not agent work)

- Create the YouTube API project, OAuth client, and complete the audit that gates public uploads (`PHASE-P7` §35 statuses).
- Choose and contract a music-generation provider with commercial-use terms (`PHASE-P4` §4.1); validate automation permissions, rate limits and license terms.
- Choose an API-capable distributor and complete artist identity/KYC (`PHASE-P8` §6, §80).
- Register a Spotify Web API application for the cross-platform signal (`PHASE-P1` §4.3).
- Set the weekly production budget ceiling and the allocation policy defaults (P2 §90; note the §13 versus §90 fraction inconsistency, resolved to sum to 1.0 — R4 annex).
- Decide the operator identity for the P7 channel profile and the P5 review queue owners.

---

## §11 Self-review of this plan

- **Spec coverage.** All nine phase files are mapped in §6 with step counts matching their own sequences (14+16+13+16+18+17+14+16+12 = 136). `architecture.md` coverage via the briefs: parts I–IX map to PHASE-P1…P9; part X (recursive learning) is split across P2/P9 with the P10 remainder deferred (R12); part XI (budget) to P2; part XII (shorts) partly to P6.14 with the social engine deferred; part XIII (community) deferred to roadmap P11; part XIV (failure management) is cross-cutting, owned by Wave 0 plus each phase's state machine; part XV (rights/`ARCH-P28`, `ARCH-P29`) to P5; part XVI (financial) to P9; part XVII (dashboard) to P9 with three unassigned views noted in §11.4. Roadmap rows P0–P9 covered; P10–P12 deferred per R12.
- **Placeholder scan.** The 136 phase steps are named but not expanded to bite-sized code steps here. This is deliberate structure, not an omission: per the writing-plans scope check, nine subsystems each get their own execution plan, generated just before their wave from the phase spec plus the frozen contract (procedure: §12). Wave 0 is fully specified because it executes first.
- **Type consistency.** Seam field names in §5 are given once and referenced, not restated — the registry is the single source of truth, which is what prevents the naming drift the briefs found.
- **Review focus.** All five cross-cutting failure modes have an owning task and test in the wave tables.

**Known open items, carried into the ledger rather than hidden:**

1. P2 §13 and §90 disagree on whether allocation fractions sum to 1.0 (70/20/10 versus a 1.10 sample). Resolution: must sum to 1.0, `baseline_fraction` redefined as the residual within exploration. Needs P2's own ADR.
2. P3 §44 lists `description_spec` with no entity or table anywhere; treat as a spec omission to be resolved in P3's execution plan.
3. P5 §149 emits `p3_specification_feedback_event` and §150 emits P2 quality-yield signals — neither has a consumer. Resolve with a consumer or drop; do not leave a dangling event.
4. P9's dashboard (§121, five views) versus architecture PART XVII (six views): Market Map, Production Queue and Exceptions are unassigned. Assign during P9's execution plan.
5. P8 has two state vocabularies (§6 and §22) with no mapping; P8.1 must publish one.

---

## §12 Just-in-time phase planning

Before each wave opens, write `docs/plans/<date>-phase-Pn-execution-plan.md` following the `writing-plans` skill: file structure, bite-sized steps with real code, per-task interfaces ("consumes/produces" using the registry's exact field names), and the phase's own tests. Inputs: the phase spec file, the frozen registry entries for its inbound and outbound seams, and the ruling rows that touch it. A phase may not start executing on this plan alone — its expanded plan is the artifact its implementer subagents read.

This is the mechanism that keeps nine large specs and one integration plan from becoming ten mutually inconsistent documents.
