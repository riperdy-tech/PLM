# ADR-0023: C2 frozen — the P2 production directive, its exploration vocabulary, money and value model

- Status: `ratified` — 2026-09-20
- Ruling: `R4`; seams `C2` (and `C7` for the reserved value terms)
- Seam: `C2`
- Supersedes / superseded by: `—`

## Context

Registry C2 was recorded as **broken**: six of twelve directive fields disagreed between
PHASE-P2 §32/§120 and PHASE-P3 §10, and P3 required two fields P2 never sent
(`budget_limit`, and per §4.5 `expected_value` / `uncertainty`).

Ten of the disagreements are mechanical — renames, a split, an expansion — and R4 already
settled them. **Five are business policy that no document in the corpus decides.** They had
to be ruled before P2 and P3 are implemented, because each would otherwise be baked
independently into two phases and discovered as a mismatch at the seam test:

1. PHASE-P2 §71 puts the budget on the allocation **cycle**; PHASE-P3 §10 requires a
   `budget_limit` on every **directive**. Nothing says which enforces the ceiling, and P3's
   `budget_envelope` appears in three places (§34, §44, §86) with no stated precedence.
2. PHASE-P3's `exploration_level` token set is **defined nowhere** in the corpus (verified by
   grep across all ten documents). P2 uses three discrete levels.
3. PHASE-P2 contradicts itself on allocation arithmetic: §13 requires exploit + explore to
   sum to 1.0 (70/20/10) while §90's sample config adds a `baseline_fraction` of 0.10, making
   1.10; §91's validation is ambiguous about which governs.
4. P2 must score arms before any revenue exists. `architecture.md` PART II defines both the
   full Expected Economic Value formula and an early-stage proxy, without saying when to
   switch. This decides what the system optimises in its first cycles.
5. P2 §117 defines five activation states and §111 forbids retiring an arm on a single
   result, but nothing states the starting state or how much evidence an arm needs before it
   may be ranked at all.

## Decision

All five were put to the human partner and answered:

1. **Budget authority — the cycle owns the ceiling, the directive carries the share.** P2
   sets the weekly ceiling; each directive's `budget_limit` is its allocated share; the sum
   of directive shares must not exceed the cycle ceiling. P3 refuses a package whose
   `budget_envelope` exceeds its directive's share. The directive's share wins over P3 §34,
   §44 and §86 wherever they disagree.
2. **Exploration vocabulary — the three discrete levels are the contract.** P3 consumes
   `exploit` / `adjacent_explore` / `radical_explore` verbatim. `exploration_fraction`
   (0.0–1.0) is carried for reporting only; no consumer may branch on it. This registry entry
   becomes the only definition of `exploration_level` in the corpus.
3. **Allocation fractions sum to 1.0.** Default policy 70% exploit / 20% adjacent / 10%
   radical. P2 §90's `baseline_fraction` is dropped as a separate slice; if retained as a
   config key it is the non-exploratory residual inside the exploit slice. Validation rejects
   any policy whose fractions exceed 1.0. P2 §13 governs over §90.
4. **Value model — proxy now, full formula reserved.** `expected_value` is computed under an
   explicit versioned `value_model` carried on the wire. First cycles use the early-stage
   proxy (view velocity, watch time, retention, subscriber conversion, engagement). The full
   Expected Economic Value terms — revenue, subscriber value, catalog value, Spotify
   conversion, future discovery value, production cost — are **reserved in the C7 reward
   schema from day one** so P9 can populate them with no contract change. P2 must never
   fabricate a revenue term before P9 supplies it; an unmeasured term is `unknown`, not zero.
5. **Activation `SHADOW`; rank at 2 measured evidence components.** Directives are computed,
   logged and auditable with no production effect until P4 and P5 exist. An arm is ranked
   only with at least 2 measured EVIDENCE components, using the `basis_weight` P1 persists
   beside confidence (ADR-0022 / the P1 basis_weight ruling); under-covered arms are returned
   flagged rather than silently mixed into the ranking. The human partner holds override.

## Correction - review finding, 2026-09-20

**Decision 5 says the ranking threshold uses "the `basis_weight` P1 persists beside confidence".
That is unsatisfiable and the phrase is withdrawn.** `basis_weight` is persisted on P1's score
rows, but it does **not** cross the C1 wire: ADR-0022 froze the C1 payload as
`{items, next_cursor, as_of, score_version}`, and P2's C1 consumer test pins that absence. P2
therefore cannot read a weighted coverage figure from P1.

The **intent** of decision 5 stands unchanged: an arm is ranked only when it has at least
**2 measured evidence components**. It is operationalised on data P2 can actually see - the
count of non-null entries in P1's `components` object on the returned payload - with
under-covered arms persisted and returned flagged as `EVIDENCE_INSUFFICIENT`, never silently
ranked.

If the *weighted* share itself is wanted on the wire, that is a C1 registry amendment (a wire
change against a frozen seam), which is out of PHASE-P2's remit and was not requested. Recorded
here so the corpus carries no instruction that cannot be carried out.

## Evidence

- Registry C2's producer/consumer field lists; ADR-0004 (`R4`) for the ten mechanical mappings.
- PHASE-P2 §13 vs §90 vs §91 (the fraction contradiction); §71 (cycle budget); §111, §117.
- PHASE-P3 §10, §11, §34, §44, §86; grep confirms `exploration_level` has no token set anywhere.
- `architecture.md` PART II P6/P7 (bandits, exploration allocation, Expected Economic Value and
  its proxy); PART XI P24 (production capital allocation).
- The human partner's answers, recorded in this session, 2026-09-20.

## Consequences

- PHASE-P2 and PHASE-P3 are unblocked. P2.1 implements the directive schema above; P3.4
  implements the adapter mapping described in the registry's required amendments.
- `tests/contract/test_c2_ar_to_experiments.py` becomes the enforcement point: the producer
  payload must validate against the P3 consumer schema on the frozen names, including the
  enum map and the rename, so a drift failure names the field that moved.
- C7's reward schema carries the reserved value terms, which is a change to a seam not yet
  frozen — it will be settled at the W5.0 freeze without another human decision.
- The fractions ruling gives P2 a validation rule it did not have, and removes a 1.10 policy
  that would have over-allocated by 10% of the weekly budget in production.

## Alternatives considered

- **Directive owns absolute budgets with no cycle cap** — rejected: it removes the only place a
  weekly ceiling can be enforced, making budget overrun a P2 bug rather than a contract.
- **Numeric exploration rate instead of three levels** — rejected: P3's diversity and
  replication logic needs discrete intent, and a number would leave P3 deciding its own
  thresholds, which is the boundary the corpus already drew.
- **Keep `baseline_fraction` as a fourth slice** — rejected: it necessitates re-tuning the other
  three and the corpus gives no basis for the split; the exploit slice already carries the
  baseline intent.
- **Implement the full economic formula immediately** — rejected: every term would be `unknown`
  until P9 exists, so P2 would rank on nothing; reserving the fields gets the schema stability
  without the pretence.
- **Start in `AUTONOMOUS`** — rejected: there is no P4 or P5 yet, so there is nothing to
  produce and no gate to respect; SHADOW is the only honest state.

## Cost if wrong

- Budget authority: contained to P2's cycle planner and P3's package validation; a wrong split
  shows up as refused packages, not as overspend, because the ceiling is enforced at the cycle.
- Vocabulary: a one-word registry amendment plus the enum map in two adapters; the C2 seam test
  fails loudly rather than silently.
- Fractions: a config default and one validation rule. The regression risk is 10% of a weekly
  budget misallocated once, caught by the cycle ceiling.
- Value model: the proxy's weights are the softest part of this ADR. If they prove wrong, P2
  reallocates production capacity toward the wrong arms; the blast radius is bounded by the
  SHADOW state until P9 supplies real terms, and the `value_model` version string makes every
  affected decision identifiable after the fact.
- Evidence threshold of 2: a higher threshold delays exploitation; a lower one ranks noise. This
  is the decision most likely to need revisiting, and it is a config value, not a wire change.
