# ADR-0026: Experiment lineage travels as one required object, starting at C3

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R9` (experiment identity carried end to end), `R5` (C3 union); remediation
  stage R1
- Seam: C3 first; C4, C5, C6 and C7 follow in their own changes
- Supersedes / superseded by: supersedes the C3 amendment line "P4 must reject only
  what P4 §7 says it rejects — no extra strictness"

## Context

R9 requires every published artifact to carry `experiment_id`,
`experiment_family_id`, `content_arm_id`, `content_genome_id` and
`production_directive_id`, so that P9 can attribute outcomes and P2 can learn. The R0
review found that none of these survived the pipeline:

- P3 never computed `experiment_family_id` or `content_genome_id`.
- C3 carried only `experiment_id` and `content_arm_id`.
- P4 exported candidates to P5 with `content_arm_id="arm_placeholder"`.
- P7 and P8 defaulted the missing identifiers to `*_unknown`.

The learning loop could not close: revenue arrived without a real arm.

## Decision

1. The five identifiers travel together as `plm_contracts.seams.ExperimentLineage`.
   Every field is required and non-empty, and the known placeholder values are
   refused.
2. P3 originates the lineage.
   - `experiment_family_id` is the hash of (arm, experiment type, sorted names of the
     varied variables). This is P3 §12 `experiment_family`: replications and follow-ups
     of the same question on the same arm share a family.
   - `content_genome_id` is the content hash of the effective genome the package was
     built from.
   - `production_directive_id` is the P2 directive id.
3. C3 carries `lineage` instead of the loose `experiment_id` and `content_arm_id`.
   - P4 stores it on the generation plan (`music.generation_plans.lineage_json`,
     migration `0016_music_plan_lineage`).
   - Plans stored before this change have no lineage and cannot export.
4. P3 replaces the defaults it used to invent with documented derivation rules (R5).
   - `requested_duration` and `bounded_preferences.bpm` are the genome target, or the
     rounded midpoint of the genome range.
   - `key_profile` is `null` when the genome leaves the key open.
5. P4 refuses any C3 spec missing a required fact. This includes an empty
   `provider_policy.allowed_providers` (P4 §7) and a `track_role` outside P3 §20.

## Consequences

- C4, C5, C6 and C7 must each carry the same lineage object. Each later seam change
  adds it, together with its consumer's refusal tests.
- The superseded C3 line said P4 should reject only what P4 §7 lists. That rule
  predates ADR-0025. Without extra refusals, P4 would have to substitute values for
  missing facts, which AGENTS.md forbids.

## Alternatives considered

- *Keep the loose identifier fields and add the three missing ones.* Rejected: five
  fields that each hop can drop or default independently is exactly how the loss
  happened.
- *Store a P3 package id and let consumers look lineage up.* Rejected for now: that
  needs cross-service reads (R3 transport), and it would still let an artifact exist
  without its identity.

## Cost if wrong

If the family-id rule groups experiments that should be apart, or splits ones that
should be together, family-level analytics in P9 are wrong but the rows remain
traceable to their experiments. The remedy is a new family rule under a new ADR,
recomputed from stored experiments.
