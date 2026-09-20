# ADR-0021: P1 serves `/market/v1` behind an optional `PLM_SERVICE_TOKEN`

- Status: `ratified` — 2026-09-20
- Ruling: `R15` (`C1`)
- Seam: `C1`
- Supersedes / superseded by: `—`

## Context
R15 fixes the base path and "auth via service token from `plm_platform`", but no
service-token setting existed in W0.4's config, and the phase spec names no HTTP
framework (§36 leaves the choice open).

## Decision
`PLM_SERVICE_TOKEN` is an optional `Secret` in `plm_platform.config.Settings`,
presented as `X-PLM-Service-Token`. Unconfigured is not open: every read answers
`503 blocked_pending_credentials`; a mismatch answers `401 invalid_service_token`
compared in constant time. The API is FastAPI, tested through `httpx.ASGITransport`.

## Evidence
- Registry C1 ("Base path `/market/v1`; auth via service token from `plm_platform`").
- `plm_platform/correlation.py` and `observability.py` already document FastAPI hooks.
- `docs/P0_...md` §5 C1 required amendments.

## Consequences
P2's adapter has a concrete base URL, header name and failure mode. An operator who
forgets the token sees a blocked API, never an unauthenticated one.

## Alternatives considered
- HTTP Basic / API-key header per phase — rejected: one token concept, one place.
- Open on localhost — rejected: it would make the seam's auth untestable and untyped.

## Cost if wrong
Swapping the framework is contained in `api/` (fastapi + its ASGI transport tests);
the wire shapes live in `api/schemas.py` and are pinned by the C1 seam test.
