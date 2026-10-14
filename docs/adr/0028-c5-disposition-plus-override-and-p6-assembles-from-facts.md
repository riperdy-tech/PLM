# ADR-0028: C5 carries disposition plus override; P6 assembles only from manifest facts

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R7` (P6 assembles the playlist manifest), `R8` (verdict vocabulary); stage R1
- Seam: C5
- Supersedes / superseded by: `—`

## Context

The R0 review found the C5 gate bypassable.

- P6 read a missing `disposition` as `provenance_status`, then as `PASSED`.
- P6 invented artifact ids (`art_{id}`), durations (180 s, 120 s), audio URIs and
  checksums.
- P5's override path wrote `CLEARED_BY_OVERRIDE` as a fifth disposition, which
  `plm_contracts.GateOutcome` forbids. It filled the manifest with `exp_unknown`,
  `arm_unknown` and `OVERRIDDEN`.
- P5 served the manifest with `extra="ignore"` and no lineage, and P6 never called P5:
  it took a hand-assembled playlist dict.

## Decision

- **One wire model.** `plm_contracts.seams.c5.ReleaseManifestPayload` (schema 1.0) is
  the per-track manifest. On top of the P5 §77 fields it carries:
  - `lineage` (ADR-0026);
  - `artifact_uri`, `artifact_sha256` and `measured_duration_seconds`, taken from the
    C4 event;
  - `override`: `null`, or an audited record `{status: CLEARED_BY_OVERRIDE,
    quarantine_case_id, auditor_id, resolved_at, reason_codes}`.
- **The verdict is two fields.** `disposition` is P5's automated decision, one of the
  four. An override may only accompany `QUARANTINED` or `REJECTED`, and the model
  refuses any other combination. `cleared_for_release` holds when the disposition is
  passed or passed-with-warnings, or when an override record exists.
- **P5's override path** rebuilds the manifest from the evaluation's stored C4
  snapshot. It keeps the automated disposition and adds the record. An evaluation
  without lineage or artifact facts (a legacy row) cannot be overridden into a manifest
  (409 `override_not_possible`), and a legacy manifest is not served (409).
- **P6** (`POST /media/packages/assemble`, `plm media assemble`) takes a list of C5
  manifests plus the P3 facts it needs: brand, required duration, visual profile and
  playlist concept, none of them defaulted. P6 refuses:
  - an empty playlist;
  - any track not cleared for release;
  - tracks whose lineage differs;
  - any manifest that fails the shared model.

  The playlist manifest id is content-addressed over the ordered P5 manifest ids.
  Session and package ids derive from it, so two playlists of one experiment no longer
  collide. Track titles are P6's own output (`Track <n>`) until the metadata stage
  supplies real ones.
- Migration `0018_c5_manifest_facts` adds the columns. P5's API and CLI share one
  `persist_gate_result`.

## Consequences

- P6 still receives manifests from its caller rather than fetching them from
  `GET /qa/release-manifests/{candidate_id}` itself. That transport is R3.
- Whether a given hold may be overridden at all (reviewable quarantine vs hard stop),
  and by whom, is R3's exception queue. This ADR only makes an override explicit and
  auditable on the wire.

## Alternatives considered

- *Keep `CLEARED_BY_OVERRIDE` as a fifth disposition.* Rejected by R8 and P5 §125: a
  consumer that switches on disposition could not tell an override from a pass, and
  the automated finding would be lost.

## Cost if wrong

If P6 should accept passed-with-warnings only for some brands, that is a P6 policy
setting layered on `cleared_for_release`. The seam does not change.
