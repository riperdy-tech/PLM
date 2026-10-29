# P6 rate/offset score evaluation with separate human labels (2026-10-26)

**Status: working read-only diagnostic; human adjudication absent and operating threshold unchanged.**

## Gap being closed

The earlier rate+offset experiment found known same-source pitch/speed variants using 21 hypotheses, but those results alone cannot measure false-positive rates. Different SHA256 hashes prove *different bytes*, not different musical works. The new checker refuses to convert such unlabeled pairs into true negatives or claimed accuracy.

## What was added

- `scripts/diagnose_p6_texture_robustness.py` now compares its **two SHA256-verified archived source WAVs in both directions**, using fixed, seven-offset-only and 21 rate+offset scores. The unlabelled result includes `cross_source_diagnostic` and a compact `unlabelled_pair_score_report` with both source digests. Pair-level scores are the **maximum across the two directional comparisons** for each method; maxima for different methods need not come from the same direction. They are for conservative screening, not a musical-difference verdict.
- `services/media_factory/audio/texture_label_evaluation.py` validates a bounded score report (1–200 unordered, distinct-checksum pairs) and independent reviewer *pseudonym assignments*. Only pairs with **two or more distinct pseudonyms and unanimous decisive labels** count as labeled. No review, one review, `uncertain`, or a split vote stays unadjudicated.
- `scripts/evaluate_p6_texture_labels.py` accepts the full diagnostic JSON output or the nested `unlabelled_pair_score_report` as `--report`, and an optional operator-supplied `--reviews` CSV. No reviewer identities, source references or private notes are echoed. Bad pair hashes, duplicate score pairs, duplicate reviewer votes, malformed reports/CSV and inconsistent scores refuse.
- Separate fixed, shift-only and shift+rate confusion counts report TP, FN, FP, TN and **sample-only** positive/false-positive fractions. A missing denominator is represented by JSON `null`, not zero. The displayed 0.998 threshold is an **uncalibrated reference**, never an instruction to change production behavior.

An empty, header-only CSV template is saved at `docs/reviews/2026-10-26-p6-human-review-template.csv`. It contains **no invented reviewers or labels**. The optional CSV requires columns in this exact order:

`left_sha256,right_sha256,reviewer_id,relationship,evidence_ref`

Each `relationship` is `same_recording`, `different_music` or `uncertain`. Hash values must match the score report; one reviewer has at most one vote per unordered audio pair. For decisive labels, a nonempty opaque `evidence_ref` is mandatory. The validator **does not verify the reviewer is a human**, that reviewer accounts are independent, or the submitted evidence. Distinct reviewer pseudonyms are not proof of independent judgment. External listening and evidence verification are still necessary.

## Real observed source-pair result

A newly executed read-only probe of the first source recording from each of the two previously selected eligible P6 packages returned exit 0 after MinIO checksum verification. The WAV byte digests differed. Scores:

| Direction | Fixed | Best shift-only | Best shift+rate |
| --- | ---: | ---: | ---: |
| Original A → original B | 0.5002244816 | 0.5002244816 | 0.5245151343 |
| Original B → original A | 0.5002244816 | 0.5002244816 | 0.5002244816 |

The compact score input is preserved as `docs/reviews/2026-10-26-p6-unlabelled-cross-source-scores.json`. A run of the new evaluator on this **single unlabelled pair**, with no `--reviews`, returned `source_pair_count=1`, `unadjudicated_pairs=1`, `reviewed_pair_count=0`, and `null` for sample true-positive and false-positive fractions for every method. **This is no empirical false-positive-rate estimate.** The saved JSON can be edited and has no cryptographic signature; its provenance is an operator-reported statement, not independently authenticated by the evaluator.

The same known-original transformation scores remain documented in `2026-10-25-p6-playback-rate-alignment.md`. Synthetic fixtures used in the unit tests are not human-labeled recordings.

## Limitations and next evidence requirement

Human-reviewed *independent* music pairs and confirmed same-source derivatives, diverse across genre/provider/mastering, are needed to calculate meaningful recall/specificity and review load with declared denominators and selection bias. Repeated comparisons of sources sharing recordings/reviewers are correlated; descriptive pair fractions alone do not justify changing the 0.998 hold. Rights, upload approval, 0038 migration branch reconciliation and historical backfill remain out of scope. No production gate changes, database writes, object writes, external posting or Git commit occurred.

**Verification:** combined label/alignment/robustness suite **60 passed** (exit 0); mypy succeeded on **459** service/package source files; full repository Ruff passed. Full non-integration regression **2,660 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0). Integration and live-provider tests were not run.
