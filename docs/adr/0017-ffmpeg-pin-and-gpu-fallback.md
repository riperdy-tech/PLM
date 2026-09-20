# ADR-0017: FFmpeg 7 pinned in the worker image; GPU profiles need a CPU fallback

- Status: `ratified` — 2026-09-20
- Ruling: `R17` (`PHASE-P6`, `PHASE-P5`)
- Seam: `none` (cross-cutting build constraint)
- Supersedes / superseded by: `—`

## Context

The stack is fixed by `architecture.md` PART XIX: FFmpeg for audio/video, GPU
encoding for video, librosa-class DSP. P6 §23 says the renderer "should use
FFmpeg or an equivalent deterministic media engine", and P5's detector stack
budgets GPU time (§130 `GPU_seconds`, §179 GPU VRAM load test) while allowing
each detector to run CPU or GPU (§182). None of this was pinned in the build:
an FFmpeg major-version drift silently changes render output, and a GPU-only
pipeline cannot run on machines without one — including CI.

## Decision

FFmpeg 7 is pinned inside the worker image; GPU profiles must have a recorded
CPU fallback.

## Evidence

- `architecture.md` PART XIX (Audio: FFmpeg/librosa; Video: FFmpeg plus GPU
  encoding/procedural motion graphics).
- `PHASE-P6` §23 (FFmpeg pipeline), `PHASE-P5` §130 (GPU cost accounting),
  §179 (GPU VRAM pressure under load), §182 (CPU/GPU choice is an
  implementation decision).
- Wave 0 implementation: `Dockerfile.dev` asserts the ffmpeg major version at
  build time (verified end-to-end in the D1 review: fail-case rc=1 on a 5.x
  line, pass on 7.1.5 in `python:3.12-slim`).

## Consequences

Renders and detector runs are reproducible against a known FFmpeg major version.
The worker image is the only place FFmpeg is installed for the pipeline — host
installs are not a supported path. A GPU profile that cannot degrade to CPU must
document why (there is currently no such profile).

## Alternatives considered

- *Float on "FFmpeg 6+"* — rejected: filter-graph and encoder behavior differ
  across majors; reproducibility (P0 §2) forbids it.
- *GPU-only pipeline* — rejected: CI and developer machines have no GPU; the
  cost is unbounded stalls for a non-functional gain.

## Cost if wrong

A CPU fallback render is slower — a time cost, recorded in P6's cost counters
(§§ cost sections) rather than a correctness cost. If FFmpeg 8 changes behavior,
the pin is bumped deliberately with the render goldens re-baselined — a known,
bounded event.
