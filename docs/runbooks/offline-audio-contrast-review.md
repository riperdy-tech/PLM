# Offline album contrast: human-review and calibration protocol

Status: **operator-run evaluation plan**, not an automated quality gate.
Scope: local P4/P5/P6 candidate audio which is already authorized for internal
inspection. This runbook neither obtains commercial rights nor changes publishing,
P5 clearance, P6 assembly, provider settings or stored catalog data.

## Local album file/manifest reconciliation (implemented)

Before interpreting any music or reviewer labels, use the read-only local
manifest checker on an explicitly selected directory containing
`publication_summary.json` and `tracks/*.wav`:

```powershell
.\\.venv\\Scripts\\python.exe scripts\\audit_local_album_evidence.py --album-dir output_media\\albums\\20261021-1456-lofi-study-focus --album-dir output_media\\full_album
```

It checks declared track positions and clip UUID prefixes against filenames,
number and duration of local WAV files, unlisted files, repeated **titles**,
and exact file-byte reuse within/across supplied folders. A missing manifest
is reported as `missing`, never inferred complete from existing WAVs.
`missing_source_track_indices` means the **expected position-and-clip filename**
is absent, not necessarily the original audio. For each such position,
`displaced_clip_prefix_matches` lists the manifest index and a unique different
local filename index sharing the first eight clip-ID characters.
`clip_prefix_absent_track_indices` lists cases with no matching prefix anywhere;
`ambiguous_clip_prefix_track_indices` lists multiple matching files or repeated
manifest prefixes, never silently choosing one. Possible displaced files with
measurably conflicting duration are reported separately under
`displaced_clip_prefix_duration_mismatch_indices`. All of these are candidate
**filename** associations, not verified complete clip UUIDs. Original album
sources, rights and video ordering need separate proof.

The archived `produce_full_playlist.py` used to reuse cached WAVs by clip prefix
regardless of their original track number, a plausible cause of stale
position-specific filenames. The disabled legacy production entrypoint remains
disabled; its helper now refuses a cached WAV in a conflicting slot before
provider calls rather than silently treating it as a new position. Neither
checker nor helper renames, deletes, reharvests or uploads audio. Output uses
album slot numbers and aggregate counts, not individual track titles, full
paths or SHA256 values.

This is a **local inventory**, not a source-of-truth registry, independent
original hash verification, account entitlement, listening QA, or confirmation
of live YouTube state. Repeated titles do not prove duplicate songs; byte
matches mean identical local files, not permission or confirmed release reuse.
Do not turn an inventory result into an automatic P5/P6 clearance decision.

## Read-only album master waveform spot checks (implemented)

For an album directory with a local `publication_summary.json`, local
`tracks/*.wav` and `album_master_continuous.wav` (or `album_master.wav`),
run:

```powershell
.\\.venv\\Scripts\\python.exe scripts\\probe_local_album_master.py --album-dir output_media\\full_album
```

The checker resolves a **unique** local filename using the eight-character
clip-ID prefix, including WAVs retained under an earlier track-position number.
Missing and ambiguous prefixes, unsupported WAV formats or durations and
unreadable/silent sections remain explicit `status` values; none is treated
as a match. For each eligible track, it reads two short (2-second) source
windows away from crossfades and compares them with the corresponding
timestamp in the local album master, allowing a 0.6-second search offset.
The normalized mono waveform correlation and measured time delta are
reported **without a calibrated pass/fail cutoff**. Only the required
small audio windows are decoded; the full master is not loaded into memory.

A high correlation at the expected time is evidence of the sampled audio in
the **local master**, even when EQ and mastering change the PCM bytes. It
does not prove complete-track equality, authenticity of the original clip UUID,
source/inspection/commercial rights, real listening quality, the encoded
MP4/video audio or the remote YouTube publication. Lower correlations may
also reflect mastering rather than a wrong song. Never automatically rename
sources, approve P5/P6 gates or edit uploaded videos from this result.

## Local MP4 encoded-audio spot checks (implemented)

For an explicitly selected local album directory containing exactly one MP4,
one local master WAV and a `publication_summary.json`:

```powershell
.\\.venv\\Scripts\\python.exe scripts\\probe_local_mp4_audio.py --album-dir output_media\\full_album
```

The read-only probe uses local FFprobe metadata to identify exactly one audio
stream and at most one video stream. It reports their durations separately
from the container's overall duration; a longer video/container does **not**
prove that the uploaded YouTube copy has a silent tail. It samples two short
windows per manifest track in the decoded local MP4's audio stream through
FFmpeg at 8 kHz mono. It aligns those windows with the local WAV master after
resampling the latter to the same analysis rate, and reports correlation and
the relative time offset. Missing/unreadable audio, absent MP4 or master,
ambiguous audio streams, invalid manifest rows or out-of-audio sample positions
are never silently considered verified.

AAC compression changes samples, so byte-level hashes should not match.
These correlation values have **no calibrated cutoff** and are not a publishing
gate; short sampled sections cannot prove an entire recording, verify music
rights/provenance or establish acoustic quality. A local MP4 is not the live
stream from YouTube. The command does not export extracted audio, modify
source media, visit external services or approve publication.

The `packet_tail` section separately checks real final audio and video
packet timestamps using bounded FFprobe `pts_time + duration_time` reads.
A `video_packets_continue_after_audio` status means video packets with
timestamps *starting after the final audio packet* were observed in this
**local MP4**. The reported `video_packets_starting_after_audio_end_count`
counts those packets, not silence samples. Packet reads use indexed seeks;
if they do not cover both actual stream ends, the result is `unknown` with
null counts, **not** a clean result. An audio-only test file is explicitly
`no_video_stream`. A longer video stream does not establish that any
specific player will present silent playback or that the remotely hosted
YouTube file is identical. Future rendering/publishing requires separate
approved QA; this diagnostic never corrects, trims or republishes files.

### Archived renderer timing handoff safeguard

`scripts/produce_full_playlist.py` remains disabled at `main()` before
paid generation and publishing. Its individual `render_album_video` helper
can nevertheless be called directly, so it now constrains its FFmpeg output
to the independently probed source-WAV duration **and** calls
`require_local_render_audio_sync` on the resulting MP4. That check
compares decoded stream and container timing with the original WAV duration,
demands actual final AAC/video packet coverage, and rejects mismatched,
ambiguous, missing or unverified media timing. It does not classify sound
quality or establish original rights. A locally timing-consistent receipt
never authorizes uploading.

A 4-second synthetic reproduction on the current Windows FFmpeg using the
legacy still-image `-shortest` options, with/without FFmetadata chapters
and with an explicit duration cap, gave exactly 4-second audio and video
in all variants. **The historical 18–19-second video-only tail did not
reproduce from these flags alone.** The exact historical FFmpeg invocation,
input contents and environment remain unverified. Do not alter preserved
MP4 files or claim this establishes the specific historical root cause.

## Before scoring

1. Confirm each candidate's source, use/inspection rights and immutable original
   SHA256 **independently** from the analysis command. A digest computed only from
   the currently available file proves repeatability, not provenance.
2. Select 2–20 named tracks from a representative range of arms/genres,
   positive examples (known repeated recordings) and genuinely distinct controls.
   Do not make decisions based only on the existing lo-fi production album.
3. Run locally, with the verified source digests when available:

   ```powershell
   .\.venv\Scripts\python.exe scripts\probe_album_audio_contrast.py --track A=first.wav --track B=second.wav --sha256 A=<original_sha256>
   ```

   Keep the JSON report with the original file identifiers and
   `policy_version`. The output contains NO rights/novelty verdict.
   A `null` metric plus `coverage[name].unknown` is **not** a score of zero.

## Blind listening labels (not yet collected)

- Assign anonymous A/B pairs in randomized order. At least two independent
  listeners assess the full-length audio and note time ranges of similarities.
- Keep separate assessments for rhythmic groove, lead melodic contour,
  instrumental palette and section/energy progression; also record
  `same_recording` when there is independent ground-truth evidence.
- For each axis use one of `similar`, `different`, `uncertain` (with optional
  observations). `uncertain` and missing reviews are *not* `different`.
- Record at minimum: `left_sha256`, `right_sha256`, `policy_version`,
  `reviewer_id` (pseudonymous), `rhythm_label`, `melody_label`,
  `instrumentation_label`, `structure_label`, `same_recording_source`
  (if independently verified), and `notes`. No labels should be invented by
  reusing diagnostic scores.
- Preserve reviewer disagreement and track exclusions for undecodable, silent,
  incomplete, unlicensed or provenance-unknown recordings.

## Offline CSV validation (implemented; no actual reviewer data yet)

After saving the diagnostic JSON to `contrast.json`, operators provide a
separate UTF-8 CSV. Its exact header, in any order, is:

```csv
left_id,right_id,left_sha256,right_sha256,policy_version,reviewer_id,rhythm_label,melody_label,instrumentation_label,structure_label,same_recording_source,notes
```

- `left_id` and `right_id` refer to aliases in `coverage`; SHA256 values must
  match the report. Reviewers may reverse the pair order, but they cannot submit
  two judgments for the same unordered pair under one pseudonym.
- Set `policy_version` to the diagnostic report's version (currently
  `p6-offline-contrast-v3`). The validator refuses stale/mismatched versions.
- Each of the four labels must be `similar`, `different`, or `uncertain`.
  Empty fields are not interpreted as `different` or `uncertain`.
- Use a pseudonymous `reviewer_id` (letters/numbers/underscore/hyphen only),
  not a real name or email. `same_recording_source` can contain an independently
  verified evidence reference or be empty. Its presence alone is **not** proof.
  `notes` are never included in aggregate terminal output.

Run the checker locally; it reads the JSON and CSV without changing them:

```powershell
.\.venv\Scripts\python.exe scripts\validate_album_listening_reviews.py --report contrast.json --reviews reviews.csv
```

The result lists each pair's number of reviewers, unanimous/disagreement/
unresolved status per axis, unavailable metrics, and whether **caller-supplied**
original SHA256 values were supplied for both sides of the diagnostic report.
It never prints reviewer identities or notes, and marks rights, independent
provenance and calibration **false** even for a unanimous pair. A missing pair
or one reviewer does not count as agreement. Invalid aliases, labels, checksums,
duplicates and malformed report schemas fail with a nonzero exit code.
The JSON parser also rejects duplicate object keys and nonfinite constants
(`NaN`/`Infinity`), and CSV parsing rejects malformed quoted fields. Each
pair's numeric/null metric availability must agree with the corresponding
`coverage` unknown reasons for **both** source recordings; inconsistent
reports are refused, not silently assigned a score. Invalid CSV/JSON values
are not echoed to terminal output. Only tests use synthetic evaluation rows;
no real human labels are present.

## Aggregate review data quality (implemented; not calibrated)

The validation JSON also contains a `data_quality` section. It counts distinct
audio pairs with zero, one, or at least two pseudonymous reviewers, pairs with
both caller-supplied digests, and non-null score coverage for each of the three
audio proxies. For each listening axis it counts `insufficient_reviewers`,
`unresolved` (at least one `uncertain`), `disagreement`,
`unanimous_similar` and `unanimous_different`.

The descriptive `decisive_agreement_fraction` is calculated as
(`unanimous_similar` + `unanimous_different`) /
(`unanimous_similar` + `unanimous_different` + `disagreement`).
Its denominator is separately exposed as `decisive_pair_count`. If the
denominator is zero, agreement is `null` rather than zero or 100%. Pairs
with fewer than two reviewers or any uncertain rating are excluded from that
fraction, **not** silently counted as agreement. Every audio pair contributes
at most once per axis, regardless of how many reviewers assessed it.

This fraction is only within-pair unanimity among decisive responses: it is
**not** Cohen's kappa, inter-rater reliability adjusted for chance, validation
against ground truth, nor threshold accuracy. Small/imbalanced samples or
self-selected evaluators can make it misleading. Reviewer identity is
pseudonymous and independence is not authenticated. No automatic PASS/HOLD or
copyright conclusion follows from these counts.

## Duplicate source aliases and repeated pair bias (implemented)

The review summary reports `unique_source_sha256_count`,
`sha256_reused_by_multiple_aliases_count`, and counts of source pairs with
exactly matching or different SHA256. A pair with identical SHA256 has
`exact_same_sha256: true` and must **not** be mistaken for independent
musical diversity evidence. Different SHA256 only proves byte differences;
alternate encodings of one song can produce different hashes.

For each axis, `different_sha256_agreement_fraction` excludes identical-byte
pairs, but can still double-count one original comparison if aliases A and B
refer to identical bytes and both are compared with C. Therefore
`unique_sha256_comparison_agreement_fraction` is the more conservative
descriptive view: it excludes any different-byte checksum-pair group that
occurs under multiple aliases. Its eligible-pair denominator is exposed as
`unique_sha256_comparison_decisive_pair_count` and is `null` when zero.
`repeated_different_sha256_comparison_group_count` shows how many such
duplicated source-pair groups were withheld. The associated pair flag is
`unique_different_sha256_comparison`.

Neither metric guarantees independent statistical observations: A–B and A–C
still share source A. `max_different_sha256_pairs_sharing_one_source` exposes
the strongest such concentration. Do not interpret either agreement rate
as accuracy, genre generalization, novelty, copyright clearance, or
inter-rater reliability corrected for chance. No review summary is a P5/P6
approval input.

## Reviewer workload and repeated source comparisons (implemented)

The read-only `data_quality` section also counts
`unique_reviewer_pseudonym_count`, `max_reviewed_pairs_per_reviewer`,
and `reviewers_covering_multiple_pairs_count`. These expose repeated
assignments to the **same declared pseudonym**, not proof of different
human listeners. A reviewer who labels A-C and B-C, when A and B have
identical SHA256, has evaluated the **same source-byte comparison twice**.
`repeated_sha256_comparison_reviewer_count` counts reviewer/source-byte
comparison groups with repetition, and
`extra_reviews_same_sha256_comparison_by_reviewer` counts their extra
assignments. These figures are zero when there are no reviews. No
pseudonym or notes are printed.

This is diagnostic only: a person can submit multiple pseudonyms,
different encodings can have different SHA256, and repeat evaluations
may be intentional blind consistency checks. The validator neither
deletes such records nor promotes aggregate scores to a quality gate.

## Operator-supplied source-reference completeness (implemented)

The `same_recording_source` CSV field is an optional operator-supplied
reference to independently obtained evidence; the validator cannot check that
evidence. The read-only `data_quality.operator_source_reference` section
counts nonempty reference fields in review rows, unique unordered SHA256
source-byte comparisons with reviews, and how many of those comparisons have
any nonempty reference. It also counts comparisons with both present and
missing references and reviewer/source-comparison groups whose reference
presence varies across aliases. Exact-same-byte pairs with a nonempty
reference are counted separately. Comparisons under alternative track aliases
are grouped by their original-byte SHA256, not counted as extra sources.

Whitespace-only reference fields count as missing. No submitted reference
strings, reviewer pseudonyms or notes appear in the summary. Mixed presence
indicates incomplete or inconsistent **documentation**, not conflicting facts
about copyright or the actual recording. A nonempty field may still be an
unverified assertion, irrelevant identifier or incorrect evidence link.
Independent original-source verification and rights clearance remain false;
none of these counts can approve a song, establish originality, or replace
manual source investigation.

## Shared-reviewer coverage (implemented, descriptive only)

`data_quality.reviewer_overlap` compares declared pseudonyms who reviewed the
same **different-SHA256 source comparison**. The same original byte pair under
several track aliases is counted only once per reviewer. Exact-same-byte
comparisons are excluded from overlap evidence. The report counts unique
source comparisons with at least two pseudonyms, distinct reviewer-pseudonym
pairs sharing comparisons, pseudonym pairs sharing at least two comparisons,
and the maximum shared comparison count for any pseudonym pair.

For each listening axis, `axis_co_ratings` counts decisive co-ratings that
agree or disagree, plus those that remain ambiguous or uncertain. A single
pseudonym's conflicting labels across aliases of the same source-byte pair
are recorded under `conflicting_alias_label_groups_by_axis` and excluded
from decisive co-ratings, never arbitrarily resolved. The descriptive
agreement fraction is agreement / (agreement + disagreement); a zero
denominator returns `null`.

**These units are reviewer-pair-by-source comparisons, not independent songs,
audience samples or verified distinct people.** More than two reviewers
on one comparison create multiple overlapping co-ratings. One source can be
shared by several comparisons, aliases with different bytes can be the same
song, and pseudonyms are unauthenticated. The output is neither Cohen's
kappa nor validated inter-rater reliability; do not claim statistical
significance or feed these numbers into P5/P6 approval.

## Same-source diagnostic consistency (implemented)

Before any human-label summary, a source SHA256 reused by multiple aliases
must declare exactly the same measurement-unavailability reasons. A measurable
pair of aliases of identical bytes must have a similarity of 1.0 for each
available diagnostic metric. For different-byte source pairs repeated under
multiple alias combinations, all metric values (including `null`) must
match. Mismatches refuse the whole submitted report with a nonzero exit
code; no reviewed record is treated as trusted by default.

These are deterministic report-internal consistency checks only. They do
not verify the asserted SHA256 against independently retained originals,
prove music originality or commercial rights, or determine whether listeners
really heard the claimed recordings.

## Evaluation before considering thresholds

1. Calculate score distributions separately for **labelled** similar/different
   pairs on each metric and each genre; report usable coverage and disagreement.
2. Avoid choosing and evaluating thresholds on the same recordings. Hold out
   releases/arms as independent evaluation sets to avoid repeated-catalog leakage.
3. Measure false positives, false negatives and confidence intervals with clear
   counts. Compare against P5 exact-recording fingerprint outcomes where
   applicable, but do not treat fingerprints as melody-ground-truth.
4. Treat underrepresented genres, small samples, high reviewer disagreement,
   unverified source rights, nonfinite scores and any missing feature as
   *not calibrated*. No automatic PASS/HOLD threshold may be promoted on that
   basis. Any future change to existing cross-phase contracts or approval rules
   needs its own authorized stage and evidence review.

## Present state

The local `output_media/albums/20261021-1456-lofi-study-focus` spot check
compared five available WAVs (ten pairs); all three diagnostics were measurable.
Rhythm-onset proxy scores clustered at 0.956–0.981, pitch-class section proxies
at 0.703–0.983, and energy-arc proxies at 0.956–0.983. The values are
**uncalibrated** and cannot diagnose plagiarism or actual song uniqueness.
No independent original hashes, account licensing entitlement or human
listening labels were validated. The report is a local diagnostic only.
