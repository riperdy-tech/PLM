# ADR-0029: C6 carries lineage to publication and release; ISRCs are allocated per recording

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R9` (experiment identity end to end), `R7`; remediation stage R1
- Seam: C6 (hop 1 P6 → P7/P8, hop 2 P7 → P8)
- Supersedes / superseded by: `—`

## Context

The R0 review found that publication and distribution detached revenue from its arm,
and that both consumers invented facts.

**P6 producer.** `MediaPackage.to_c6_payload` dropped `content_arm_id`.

**P7 publish route and CLI.** They read an untyped dict and filled in what was missing:
- identity: `exp_unknown`, `arm_unknown`, `fam_unknown`, `gen_unknown`,
  `dir_unknown`;
- assets: a default video URI and checksum, and the thumbnail's *id* used as its
  checksum;
- metadata: titles, descriptions and `lofi`/`ambient` tags;
- channel: it auto-created a channel profile with a fake OAuth reference;
- disclosure: `containsSyntheticMedia=True` hardcoded.

The `youtube_video_published` event was built and then discarded.

**P8.** It consumed neither P6's package nor P7's event.
- `POST /releases` fabricated P5 and P6 dicts (`disposition: PASSED`) and took identity
  from request fields that defaulted to `*_unknown`.
- The compiler invented `Untitled Release`, 180 s durations, audio URIs and
  checksums.
- It derived each ISRC from the track's position. Every release's first track got
  `USPLM2600001`, so the second release failed on the unique constraint.
- Reconcile rebuilt the C7 record from call-time defaults.

## Decision

- **Shared models.** `plm_contracts.seams.c6` defines both hops, each at schema 1.0.
  - `MediaPackagePayload` (hop 1) adds `lineage` and `ai_disclosure_required`, and
    types every asset with its SHA-256 checksum.
  - `YouTubePublicationPayload` (hop 2) carries `lineage` instead of five loose
    identifiers, plus `channel_id` and `contains_synthetic_media`.
- **P7** derives its publish manifest only from the parsed package and the channel
  profile, in one function (`youtube_publishing.manifests.build_publish_manifest`)
  that the API and CLI share.
  - It refuses an unconfigured channel (409 `channel_not_configured`), a package that
    is not `ready_for_publish`, and a naive `publish_at`.
  - `containsSyntheticMedia` is P5's disclosure finding.
  - No playlist is attached unless one is configured.
  - The hop-2 event is returned from `POST /youtube/publish`.
- **P8** submits only through `distribution.submission.submit_release_from_seams`,
  which the API, CLI and smoke test share.
  - Inputs: C5 manifests, the C6 package, and the optional C6 event (which must
    belong to the same package and lineage).
  - The compiler requires every track to be cleared, every track's lineage to equal
    the package's, and the tracklist to name the same recordings.
  - Track facts come from the manifests.
  - Release metadata (date, label lines, territories) is supplied by the caller,
    never defaulted.
  - The release stores the lineage. Duplicate and reconcile paths read the stored
    identity back.
- **ISRCs.** `distribution.identifiers.allocator.allocate_isrc` keeps one ISRC per
  recording (the P4 candidate id), from a persisted sequence per country, registrant
  and year (migration `0019`). The registrant code is development configuration;
  issuing real ISRCs needs an assigned registrant (P0 §10).

## Consequences

- P7 and P8 receive payloads from their callers. They do not yet fetch from P6 or
  subscribe to P7's event; that transport is R3.
- `distribution.tracks.isrc` is still unique, so one recording cannot yet appear on
  two releases (for example a single and then an album). That is a P8 data-model
  change recorded in `docs/STATUS.md`.
- P6 metadata still uses fixed tags; that is metadata quality (P6), not this seam.

## Cost if wrong

If P7 should choose among metadata candidates by experiment, the choice moves upstream
into P3's title spec and P6's ranking. The seam already carries every candidate.
