# PHASE MAP — disambiguating the corpus's three numbering systems

> Written by W0.10, ruled by R1 (P0 §0 and §9). This is the **only** place the
> mapping between the three systems is recorded; do not duplicate it elsewhere.

## 1. The rule

The corpus carries **two colliding numbering systems** (and a third nearby one).
Every phase brief flagged the collision. From Wave 0 onward, all new documents,
code comments, ADRs and commit messages use the qualified forms:

| Written as | Means | Example |
|---|---|---|
| `PHASE-Pn` | A **build phase**: `PHASE-P1` … `PHASE-P9`, one per phase plan file. The subject of the P0 master plan. | `PHASE-P7` = YouTube publishing engine |
| `ARCH-Pnn` | A **capability item** in `docs/architecture.md`, parts I–XVII (`ARCH-P1` … `ARCH-P30`). | `ARCH-P17` = "Publishing engine" |
| `Pn.m` | A **phase's own implementation step**, from that phase file's recommended sequence (`P1.1` … `P9.12`). | `P7.13` = "live smoke tests" inside `PHASE-P7` |

Unqualified `P7 §9` still appears inside the existing phase files and the P0
master plan (which are **not** rewritten — R1); read it with this map and prefer
the qualified form in everything new. A section reference to a phase file is
written `PHASE-Pn §m`; a section reference to `architecture.md` is written
`ARCH-Pnn` or `architecture.md PART <Roman>`.

Existing phase files cite no architecture section numbers at all (checked for
W0.10), so nothing in them depends on the old confusion — it only bites readers.

## 2. The collision that caused confusion — worked example

From the P0 master plan, seam C6, before the rule:

> "P7 §9 explicitly tolerates field-name drift if semantics match"
> … "P7's `youtube_video_published` event (§65) and P8's `P8 → P9` record (§71)".

Decode it with the rule:

| Fragment | Looks like | Actually is |
|---|---|---|
| `P7 §9` | section 9 of architecture item `ARCH-P7` ("Learn what 'success' actually means")? | §9 of `PHASE-P7` — "Publish package contract from P6" |
| `P7's event (§65)` | step 65 of a phase that has 14 steps? | §65 of the `PHASE-P7` file — "P7 → P9 analytics handoff" |
| `P8's record (§71)` | `ARCH-P8` ("Create a structured content genome")? | §71 of the `PHASE-P8` file — "P8 → P9 contract" |
| `P7.6` (elsewhere) | a section of the P7 file? | **step** 6 of `PHASE-P7` ("resumable upload") |

The failure mode is real: `ARCH-P7` (measure what success means) and `PHASE-P7`
(publish to YouTube) are unrelated, and a reader who picks the wrong one writes
a test for the wrong chapter. The qualified forms make the sentence unambiguous:
"`PHASE-P7` §9 … the `youtube_video_published` event (`PHASE-P7` §65) … the
`P8 → P9` record (`PHASE-P8` §71)".

## 3. Build phases → plan file → ARCH items → steps

All plan files now live in `docs/`. `ARCH-Pnn` columns list the capability items
the phase implements; **Basis** says how that mapping was derived:

- **stated** — the P0 master plan §11 (or §9) names the phase for that part;
- **inferred** — phase and architecture content match, but no document states it;
- **stated part-level / inferred item-level** — the master plan places the *part*,
  this map assigns the individual items inside it.

| Build phase | Plan file | Wave | ARCH items implemented | Steps | Basis |
|---|---|---|---|---|---|
| `PHASE-P1` | `docs/P1_market_intelligence_implementation_plan.md` | 1 | `ARCH-P1`…`ARCH-P5` | P1.1–P1.14 | stated part-level (P0 §11: part I) / inferred item-level |
| `PHASE-P2` | `docs/P2_autonomous_ar_engine_implementation_plan.md` | 2 | `ARCH-P6`, `ARCH-P7`, `ARCH-P24`; share of `ARCH-P23` | P2.1–P2.16 | `ARCH-P24` and the `ARCH-P23` split are stated (P0 §11: parts X, XI); rest inferred |
| `PHASE-P3` | `docs/P3_content_genome_experiment_framework_implementation_plan.md` | 2 | `ARCH-P8` | P3.1–P3.13 | inferred (`PHASE-P3` is the content-genome + experiment framework; `ARCH-P8` is the genome item) |
| `PHASE-P4` | `docs/P4_music_generation_factory_implementation_plan.md` | 3 | `ARCH-P9`, `ARCH-P10` | P4.1–P4.16 | inferred (part III's generation items map to the music factory; QA item `ARCH-P11` goes to `PHASE-P5`) |
| `PHASE-P5` | `docs/P5_quality_rights_provenance_release_gate_implementation_plan.md` | 3 | `ARCH-P11`, `ARCH-P18` (disclosure), `ARCH-P28`, `ARCH-P29` | P5.1–P5.18 | stated part-level (P0 §11: part XV → P5); QA/disclosure split inferred |
| `PHASE-P6` | `docs/P6_playlist_visual_video_factory_implementation_plan.md` | 4 | `ARCH-P12`…`ARCH-P16`, `ARCH-P25` (run at P6.14) | P6.1–P6.17 | partly stated (P0 §11: part XII "partly to P6.14"); playlist/video/packaging items inferred |
| `PHASE-P7` | `docs/P7_youtube_publishing_engine_implementation_plan.md` | 4 | `ARCH-P17`, `ARCH-P18` (primary) | P7.1–P7.14 | inferred by content (`ARCH-P17` = "Publishing engine" is literally this phase) |
| `PHASE-P8` | `docs/P8_dsp_distribution_engine_implementation_plan.md` | 5 | `ARCH-P19`, `ARCH-P20` | P8.1–P8.16 | inferred by content |
| `PHASE-P9` | `docs/P9_analytics_economics_engine_implementation_plan.md` | 5 | `ARCH-P21`, `ARCH-P22`, `ARCH-P30`, share of `ARCH-P23`, dashboard (part XVII) | P9.1–P9.12 | stated part-level (P0 §11: parts XVI, XVII → P9, part X split); item-level inferred |

### Capability items with no single phase owner

| ARCH item | Where it lives | Basis |
|---|---|---|
| `ARCH-P23` Automated weekly review | split across `PHASE-P2` / `PHASE-P9`; the P10 remainder deferred (R12) | stated (P0 §11) |
| `ARCH-P26` Community / audience feedback | **deferred** — roadmap P11, not in this build | stated (P0 §11) |
| `ARCH-P27` Durable workflow states | cross-cutting: Wave 0 (Temporal skeleton, W0.7) plus each phase's own state machine | stated (P0 §11) |

Also worth stating plainly: the P0 master plan's shorthand "parts I–IX map to
PHASE-P1…P9" is **not** a literal one-to-one by index — by content, part III
(generation) and part IV–VI (playlist/video/packaging) belong to `PHASE-P4`/
`PHASE-P5`/`PHASE-P6`, not `PHASE-P3`/`PHASE-P4`/`PHASE-P5`. The table above is
the resolved mapping; the shorthand is superseded by it.

### The roadmap in architecture part XXVI

| Roadmap row | Disposition |
|---|---|
| P0 Accounts/OAuth/infrastructure | engineering half = Wave 0 (R16); accounts/OAuth/audits = P0 §10 human task list |
| P1–P9 | `PHASE-P1` … `PHASE-P9` (this table) |
| P10 Recursive learning / autonomous allocation | **deferred, not cancelled** (R12); the seam is frozen as C8 |
| P11 Shorts/social/community engine | deferred (part XII remainder, `ARCH-P26`) |
| P12 Multi-brand scaling | deferred; one brand is seeded for the first build (R19) |

Parts XVIII–XXV carry no `ARCH-Pnn` items (human-intervention model, tech stack,
database sketch, decision-log format, experimental design, multi-brand, revenue
targets, strategy). Two of them are load-bearing elsewhere: PART XX's table list
is illustrative only (R3 — phase plans own table names), and PART XXI's decision
record format is adopted verbatim by `docs/contracts/DECISION_LOG.md`.

## 4. Step ranges, in one place

```text
PHASE-P1  P1.1–P1.14   (§39)
PHASE-P2  P2.1–P2.16   (§114)
PHASE-P3  P3.1–P3.13   (§85)
PHASE-P4  P4.1–P4.16   (§120)
PHASE-P5  P5.1–P5.18   (§181)
PHASE-P6  P6.1–P6.17   (§56)
PHASE-P7  P7.1–P7.14   (§74)
PHASE-P8  P8.1–P8.16   (§76)
PHASE-P9  P9.1–P9.12   (§203)
```

(Step lists are quoted from each phase's own recommended implementation
sequence; the P0 master plan §6 repeats the counts: 14+16+13+16+18+17+14+16+12 = 136.)
