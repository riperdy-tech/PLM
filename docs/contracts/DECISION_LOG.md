# DECISION LOG

> The program's decision record. Format adopted from `docs/architecture.md`
> PART XXI (DATE, DECISION, INPUT DATA, MODEL, CONFIDENCE, ACTION, RESULT).
> Authority order (R2): `docs/contracts/CONTRACT_REGISTRY.md` → owning phase plan
> → architecture.md → P0 master plan. Decisions that change a cross-phase seam are
> amended into the registry (as an ADR) **before** the producer or consumer
> changes; ADRs live in `docs/adr/` and are numbered `ADR-0001`… in ruling order.

## Entry format for future decisions

```text
DATE        ISO date of the decision
DECISION    one sentence: what was decided
INPUT DATA  the evidence the decision rests on (files, sections, observed results)
MODEL       which model or agent decided it (and which reviewed it)
CONFIDENCE  numeric 0–1 where a model scored it; otherwise "n/a" — never invented
ACTION      what was changed as a result (code, registry, ADR, budget, policy)
RESULT      the realized outcome, recorded later; then assess decision quality
```

PART XXI's rule: *"Every major decision should record"* these fields, and
*"Later record the realized outcome and assess decision quality."* An entry whose
RESULT is still "pending" is open, not complete.

## Seeded: the twenty program rulings (ratified 2026-09-20)

Source: P0 master plan §9 ("Rulings index"); reasoning in the ADRs. Ratified date
= 2026-09-20 (the Wave 0 start). The source recorded no numeric confidence for
these plan-level rulings; `—` means *not recorded at ruling time*, not zero.
All twenty were made by the planning agent and are ratified by this log; where a
ruling amends a seam, the ACTION column names the registry entry it must land in.

| # | DATE | DECISION | INPUT DATA (what disagreed) | MODEL | CONFIDENCE | ACTION | RESULT |
|---|---|---|---|---|---|---|---|
| R1 | 2026-09-20 | Two numbering systems qualified as `PHASE-Pn` / `ARCH-Pnn` (steps `Pn.m`) | Every phase brief flagged the collision; P0 §0 | planning agent | — | `ADR-0001`; `docs/contracts/PHASE_MAP.md` | published in W0.10; applied to all new artifacts |
| R2 | 2026-09-20 | Authority order: registry → phase plan → architecture → P0 plan | P0 §0; seam drift found in every phase brief | planning agent | — | `ADR-0002`; registry header | enforced by contract tests (pending their waves) |
| R3 | 2026-09-20 | Phase plans own table names; architecture PART XX is illustrative | PART XX sketch vs P2 §44–§52, P5 §81–§92, P8 §6–§7, P9 §8 | planning agent | — | `ADR-0003` | pending (phase migrations) |
| R4 | 2026-09-20 | P2's directive is the wire format; P3 adapts; P2 adds `budget_limit`, `expected_value`, `uncertainty` | P2 §32/§120 vs P3 §10 (6 of 12 fields mismatch) | planning agent | — | `ADR-0004`; registry C2 | pending (W2.0 freeze) |
| R5 | 2026-09-20 | Track spec is the union; P3 emits every P4 §7 key | P3 §20/§44/§58 vs P4 §7 (`role`/`track_role`, `duration_range`/`requested_duration`, `*_profile`/`*_spec`) | planning agent | — | `ADR-0005`; registry C3 | pending (W3.0 freeze) |
| R6 | 2026-09-20 | P4 adds four read endpoints and extends `rights_snapshots` | P4 §67/§68 vs P5 §7/§33 (P5 §117 quarantines without the snapshot fields) | planning agent | — | `ADR-0006`; registry C4 | pending (must land before P5.3) |
| R7 | 2026-09-20 | P5 stays per-candidate and gains `p5_manifest_id`; **P6 assembles** the playlist manifest; profiles come from P3 | P6 §4 vs P5 §77/§138 (different shapes; nobody owned assembly) | planning agent | — | `ADR-0007`; registry C5 | pending (W4.0 freeze) |
| R8 | 2026-09-20 | `cleared` is not a state; canonical vocabulary is P5 §51's four dispositions + `CLEARED_BY_OVERRIDE` | P6 §4 `provenance_status: "cleared"` vs P5 §51 | planning agent | — | `ADR-0008`; registry C5 | pending (W4.0 freeze) |
| R9 | 2026-09-20 | Experiment identity carried through P7 event and P8 record to P9 | P7 §65 / P8 §71 vs P9 §38 (linkage absent today) | planning agent | — | `ADR-0009`; registry C6 | pending (W5.0 freeze) |
| R10 | 2026-09-20 | P8 stores/versions raw reports; P9 parses to facts and owns revenue | P8 §51/§72 vs P9 §53–§54 (overlapping ownership) | planning agent | — | `ADR-0010`; registry C7 | pending (W5.0 freeze) |
| R11 | 2026-09-20 | P9 posts normalized rewards to P2's `POST /ar/results`; P2 owns posteriors | P2 §72 + §74–§75 vs P9 (no P2-facing contract existed) | planning agent | — | `ADR-0011`; registry C7 | pending (W6.1 loop closure) |
| R12 | 2026-09-20 | P10 deferred; the §155 feature seam (`GET /analytics/p10/features`) is kept | P9 §155/§156 "P10 decides"; roadmap P10 | planning agent | — | `ADR-0012`; registry C8 | pending (P10 scheduled) |
| R13 | 2026-09-20 | One storage namespace, seven prefixes, content-addressed, checksummed | P4 §29 vs P5 §193 vs P6 §42 (three prefixes) | planning agent | — | `ADR-0013`; registry C9 | partially implemented — W0.6 `storage.py` (7 prefixes) |
| R14 | 2026-09-20 | P1's 13 taxonomy dimensions are authoritative; AC-05 corrected | P1 §14.2 (13 dims) vs P1 AC-05 (10 dims) | planning agent | — | `ADR-0014`; registry C1 | pending (W1 taxonomy, P1.7) |
| R15 | 2026-09-20 | P1 mints `arm_id`; P2 owns arm state and posteriors | P1 §20/§25 vs P2 §4/§45–§47/§54 (ownership unstated) | planning agent | — | `ADR-0015`; registry C1 | pending (C1 freeze before P2) |
| R16 | 2026-09-20 | Wave 0 is the engineering half of P0; accounts/OAuth/audits are human tasks | No P0 plan file; seven briefs assume infrastructure (C10) | planning agent | — | `ADR-0016`; registry C10; P0 §10 human list | in progress — W0.1–W0.12 executing |
| R17 | 2026-09-20 | FFmpeg 7 pinned in the worker image; GPU profiles have a recorded CPU fallback | architecture PART XIX vs P5 §130/§182, P6 §23 (hardware assumptions) | planning agent | — | `ADR-0017` | implemented — W0.2 `Dockerfile.dev` ffmpeg-7 assert (verified in D1 review) |
| R18 | 2026-09-20 | Reviewers always get fresh context; no cheap-tier model routing available in this harness — recorded, not fixed | P0 §7 (harness exposes no reliable model parameter) | planning agent | — | `ADR-0018` | n/a — revisit if the harness changes |
| R19 | 2026-09-20 | `brand_id` is a first-class field everywhere; one brand seeded for the first build | `active_channel_id` (P2 §30) vs `brand_profile_id` (P3 §26/§44, P7 §9) vs `brand_id` (P4 §29, P6 §4/§5, P7 §8.1, P8 §9) | planning agent | — | `ADR-0019` | pending (brand seed data) |
| R20 | 2026-09-20 | One `plm` CLI with a group per phase, for the phases that define no CLI (P1, P2, P3, P6, P9) as well as P5's | P5 §202 defines `p5 …` commands; other phases define none | planning agent | — | `ADR-0020` | implemented — W0.12 `plm` CLI, nine phase groups |

## Seeded: the Wave 0 ledger rulings (from `.superpowers/sdd/progress.md`)

These predate this log; recorded now so the log is exhaustive. "Dispatch" is the
working unit that owned the ruling at the time it was made.

| # | DATE | DECISION | INPUT DATA | MODEL | CONFIDENCE | ACTION | RESULT |
|---|---|---|---|---|---|---|---|
| L1 | 2026-09-20 | Artifact URIs are opaque text in the database; no schema layer parses storage paths | W0.6 storage vs W0.5 db (pre-flight scan, shared surface `uri`/`checksum_sha256`) | implementer + D1 reviewer | — | ledger ruling; implemented W0.5/W0.6 | holds — `storage.to_uri/from_uri` mechanical parse only |
| L2 | 2026-09-20 | CI builds service containers from the same `docker-compose.yml` as local development | W0.2 compose vs W0.11 CI (pre-flight scan) | implementer + D1 reviewer | — | ledger ruling; implemented W0.11 | holds — `ci.yml` reuses compose; guard test `tests/unit/test_ci_workflow.py` |
| L3 | 2026-09-20 | Temporal task-queue names are defined once in `plm_platform` | W0.7 temporal vs W0.12 CLI (pre-flight scan) | implementer + D1 reviewer | — | ledger ruling; implemented W0.7/W0.12 | holds — `TaskQueue` enum; doctor's task_queues check derives from it |
| L4 | 2026-09-20 | Enum values are stored as text validated by `plm_contracts`, not native Postgres enums | W0.3 contracts vs W0.5 db (pre-flight scan) | implementer + D1 reviewer | — | ledger ruling; implemented W0.3/W0.5 | holds — allows a status value without a cross-phase migration |
| L5 | 2026-09-20 | The Control Plane document is admitted to the repository as documentation in W0.10 and treated as a deferred cross-cutting workstream, not a tenth phase to build now | Unrecognised 88.5 KB doc found in the D1 working tree | dispatch D1 (process finding) | — | ledger ruling; being implemented in W0.10 (moves to `docs/`) | pending — its inputs are P1–P9 outputs |

## Phase-build decisions: P1.10 API (2026-09-20)

| # | DATE | DECISION | INPUT DATA | MODEL | CONFIDENCE | ACTION | RESULT |
|---|---|---|---|---|---|---|---|
| D1 | 2026-09-20 | P1 serves `/market/v1` behind an optional `PLM_SERVICE_TOKEN` (unconfigured ⇒ 503 `blocked_pending_credentials`, mismatch ⇒ 401 `invalid_service_token`, constant-time compare); framework is FastAPI | R15 fixed the base path and "service token from `plm_platform`", but no such setting existed in W0.4 and §36 names no framework | planning agent | — | `ADR-0021`; `plm_platform.config.SERVICE_TOKEN_VAR/HEADER`; `services/market_intelligence/api/` | pending |
| D2 | 2026-09-20 | The C1 list response is the registry's flat `{items, next_cursor, as_of, score_version}` — not `plm_contracts.Page`/`Envelope`; P1.2's table list is frozen in the same entry; `brand_id` stays on `market_arms` and off the payload | Registry C1 field list vs platform envelope shapes; open item 18 (R19 vs the frozen wire); P1.2 `MARKET_TABLES` | planning agent | — | `ADR-0022`; registry C1 seam test landed (`test_c1_market_to_ar.py` — exists) | pending |

## C2 freeze decisions: PHASE-P2 → PHASE-P3 production directive (2026-09-20)

Five policy decisions that no document in the corpus settles. Ten of C2's thirteen field
disagreements were mechanical and needed no judgement (R4); these five could not be derived
from the specs, and P2 and P3 would each have baked in a different assumption. Asked of the
human partner by the controller; recorded in `ADR-0023`; registry C2 is now **frozen**.

| # | DATE | DECISION | INPUT DATA | MODEL | CONFIDENCE | ACTION | RESULT |
|---|---|---|---|---|---|---|---|
| W2-1 | 2026-09-20 | The allocation **cycle** owns the budget ceiling; each directive carries its **share** in `budget_limit`; P3 refuses a package whose `budget_envelope` exceeds its share | PHASE-P2 §71 (budget on the cycle) vs PHASE-P3 §10 (per-directive `budget_limit`); P3's `budget_envelope` in §34/§44/§86 with no stated precedence | human partner | — | `ADR-0023`; registry C2 frozen | pending — enforced by P2's cycle planner and P3's package preflight |
| W2-2 | 2026-09-20 | Exploration vocabulary is P2's three discrete levels `exploit` / `adjacent_explore` / `radical_explore`; `exploration_fraction` is reporting only and no consumer may branch on it | PHASE-P3's `exploration_level` token set is defined nowhere in the corpus (verified by grep across all ten documents) | human partner | — | `ADR-0023`; registry C2 is now the only definition of `exploration_level` | pending — P2.1 schema, P3.4 adapter |
| W2-3 | 2026-09-20 | Allocation fractions must sum to 1.0; default 70/20/10; P2 §90's `baseline_fraction` is dropped as a separate slice (retained at most as the residual inside exploit) | PHASE-P2 §13 (sum 1.0) vs §90 (sample config sums to 1.10) vs §91 (validation ambiguous) | human partner | — | `ADR-0023` | pending — P2's policy validation; removes a 10% weekly over-allocation |
| W2-4 | 2026-09-20 | `expected_value` is computed under an explicit versioned `value_model`: early-stage proxy now, full Expected Economic Value terms reserved in the **C7** reward schema from day one; P2 must never fabricate a revenue term | `architecture.md` PART II P7 defines both the full formula and the proxy and gives no switch point; P9 does not exist yet | human partner | — | `ADR-0023`; extends into C7, to be settled at the W5.0 freeze | pending — P2.6 reward model |
| W2-5 | 2026-09-20 | P2 starts in `SHADOW`; an arm is ranked only with ≥ 2 measured EVIDENCE components, using the `basis_weight` P1 persists beside confidence; under-covered arms are returned flagged; the human partner holds override | PHASE-P2 §117 (five activation states, no starting state) and §111 (one-result retirement forbidden); P1's `basis_weight` ruling | human partner | — | `ADR-0023`; registry C2 frozen | pending — P2.7 and P2.13 |
