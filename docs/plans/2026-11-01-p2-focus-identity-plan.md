# Plan: keep P2 (and what feeds it) inside the locked focus-music identity

- Date: 2026-11-01
- Status: **proposed; nothing implemented.** Each step needs the owner's go-ahead.
- Decision being applied: ADR-0044 (instrumental deep-focus electronic music)

## What exists today (checked 2026-11-01, dev DB `plm`, tip 911357b+)

- **P2 eligibility.** A cycle only picks arms whose `ar.ar_content_arm_state.status`
  is `candidate` or `active` (`decisioning/cycle.py:222`). `blocked`, `paused`,
  `retired` and `archived` are never picked (`policy/constraints.py`), and
  `upsert_arm_state` never resets a status (`storage/repositories.py:230`).
  States today: 74 `candidate`, 3 `active`.
- **Manual overrides do nothing.** `ar_manual_override` (`pause_arm`, `force_arm`)
  is written by `insert_override`, but `load_active_overrides` has no caller, so a
  cycle never reads it.
- **P2 has no identity concept.** Arms carry nine taxonomy keys and nothing says
  "this brand only makes X".
- **P1 does not measure the focus niche.** Its 77 arms, by genre and use case:

  | Group | Arms | Fit with ADR-0044 |
  |---|---|---|
  | rnb (7), city_pop (9), lofi (13) | 29 | OUT: genre excluded |
  | sleep use case (ambient 2, none 6) | 8 | OUT: sleep is excluded |
  | late_night_drive with no genre (3) | 3 | OUT: not a focus use case |
  | ambient / deep_house / synthwave × study_focus | 7 | IN, as rough proxies |
  | none × study_focus | 6 | unclear: P3 would infer lofi or jazz (both OUT) |
  | synthwave × none (8), synthwave × late_night_drive (3), ambient × none (5) | 16 | unclear: genre IN, use case not focus |
  | none × none (duration-only buckets) | 8 | unclear: no genre at all |

  None of the nine report sub-styles (future garage, chillstep, downtempo, etc.)
  exist as P1 taxonomy values, and P2 never mints arms, so P2 cannot explore them yet.
- **P3 cannot produce them.** The genre templates are rnb, lofi, city_pop, jazz,
  ambient, house and synthwave. `brief_genre_inference.by_use_case.study_focus` is
  `["lofi", "ambient", "jazz"]`, which is two-thirds OUT.
- **Caps would starve a one-identity portfolio.** The `p2-v1` policy has
  `max_genre_share` and `max_use_case_share` of 0.60. On a 3-slot cycle that is one
  arm per use case, so an all-focus portfolio fills 1 of 3 slots and ends `PARTIAL`.
- **Length conflict.** ADR-0036 fixes videos at 45–75 min, but the report proposes
  90–120 min.

## Steps

### 0. Stopgap: block clearly-OUT arms (data only, dev DB)
Set `status = 'blocked'` with reason `out_of_identity:ADR-0044` on the 40 arms in
the three OUT groups. This is reversible (set them back to `candidate`).
- Verify: a dry P2 cycle never selects them, and the arm count by status shows 40 blocked.
- Limit: new P1 arms arrive as `candidate`, so the filter leaks. It only holds until step 1.

### 1. Identity filter in P2 (code)
Add a per-brand identity spec (allowed genres, subgenres and use cases;
`vocal_mode` instrumental). The cycle drops non-matching arms before ranking and
records each drop with a new reason code `OUT_OF_IDENTITY`, so it is auditable
(§58 pattern). Wire `load_active_overrides` into the cycle (or delete it). Both
are needed so `pause_arm` means something.
- Verify: unit tests for match, no-match and missing dimension; a cycle test where
  an OUT arm is refused with `OUT_OF_IDENTITY`; PG-isolated run on the disposable DB.

### 2. Policy `p2-v2` for a one-identity channel (code + new policy row)
Use-case and genre caps were meant for a multi-genre channel. Move the diversity
caps onto `subgenre` (the sub-style), so variety comes from sub-styles. This is a
new policy version, not an edit to `p2-v1` (§92). It may need a migration if a new
cap column is persisted (see the `PolicyConfig` note).
- Verify: on a 3-slot cycle of focus arms, the cycle fills 3 slots with 3 distinct sub-styles.

### 3. P1 taxonomy and queries for the focus niche (code)
Add the sub-styles as P1 `subgenre` values under a focus-electronic genre, with
the YouTube queries that measure them, so arms come from measured demand instead
of proxies.
- Verify: a P1 collection run creates focus arms with at least 2 measured
  components each (ADR-0023 #5). This makes YouTube API read calls (quota, no money).

### 4. P3 templates for the focus sub-styles (code)
Add one genre template plus per-sub-style palettes (album contrast v5 pattern).
Change `study_focus` and `focus` inference to focus-electronic genres only.
- Verify: genome and materialization tests; a dry P3 build per sub-style gives
  instrumental 70–110 BPM sheets with nothing from the OUT list.

### 5. Retire the steps 0–1 leftovers
Once step 1 is live, keep the step-0 rows `blocked` (history), or archive them.

## Owner decisions needed before step 1

1. **Sub-styles:** adopt the report's nine (future garage focus, soft
   cyber-electronica, minimal pulse, ambient breakbeat, downtempo synth, deep-work
   chillstep, restrained retro-future, organic electronic, piano-synth
   concentration) as the arm set, or change the list.
2. **BPM:** 70–110 as the fixed range.
3. **Length:** keep 45–75 min (ADR-0036), or amend ADR-0036 to 90–120 min.
4. **Unclear groups (30 arms):** block them too in step 0, or leave them
   selectable until step 1.
5. **Brand id** `neon_seoul` is still the brand everywhere. Renaming it is
   separate and not in this plan.

## Not in scope
P6 visuals and P7 metadata for the new identity, and the channel name. These are
follow-ups after the arms exist.
