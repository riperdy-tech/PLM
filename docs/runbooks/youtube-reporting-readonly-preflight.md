# YouTube Reporting — read-only operating preflight

This runbook concerns the PLM P7 (YouTube publishing) ↔ P9 (analytics)
reporting boundary. Do not treat an OAuth configuration or a health check as
evidence of verified publication, real CTR, or per-thumbnail A/B outcomes.

## Safe local checks

From the repository root, use the project venv:

```powershell
.\.venv\Scripts\python.exe scripts\check_reporting_environment.py
.\.venv\Scripts\python.exe scripts\check_reporting_environment.py --probe-services
.\.venv\Scripts\python.exe scripts\check_reporting_environment.py --probe-internal-auth
```

The default check is offline: it reports **presence flags only**, never secret
values. Service probes use GET `/healthz` and `/readyz` on loopback P7/P9.
Internal authentication probes use the shared service token, but only GET one
reserved nonexistent identifier on each service. Unexpected responses fail
closed. If `PLM_SERVICE_URL__YOUTUBE` or `PLM_SERVICE_URL__ANALYTICS`
resolves to a non-loopback host, these probes **refuse** rather than sending
the token elsewhere. No writes are issued.

## Explicit Google catalog authorization check

```powershell
.\.venv\Scripts\python.exe scripts\check_reporting_environment.py --probe-google-catalog
```

This option is **not offline**: OAuth refresh may contact Google's token
endpoint; the only Reporting API operation is `GET /v1/jobs`, scoped to
existing `channel_reach_basic_a1` jobs. It does **not** create jobs, request
the CSV media URL, save downloaded data, or send outcomes to P2. Printed
results contain statuses/booleans, not tokens, OAuth payloads, response
bodies, URLs, or job identifiers.

Interpretation:

- `oauth_credentials=present_unverified`: local client ID, client secret
  and refresh token are present, **not proof of API authorization**.
- `health_and_database_ready`: a local API passed liveness and DB readiness
  checks, **not proof of business-level publication**.
- `token_accepted_read_only`: P7/P9 accepted the internal token for reserved
  GETs; not proof of real attempt attribution.
- `google_catalog=permission_denied`: the Reporting API returned 401/403.
  Cross-check `google_oauth_scopes` from the *same explicit probe*:
  `required_scope_not_reported` means Google's successful token refresh
  **included** a scope list but neither `yt-analytics.readonly` nor
  `yt-analytics-monetary.readonly` was in it. This is a confirmed missing
  permission needed for Reporting; other account/channel/API restrictions
  have **not** thereby been ruled out. `required_scope_reported` means
  an eligible scope is listed, **not** proof of access.
  `not_reported_by_oauth` means the refresh response omitted usable
  scope metadata (or refresh did not succeed); it is **not** a negative
  scope verdict. Inspect Google project API enablement and channel/job access
  separately. Do not rotate credentials or grant new consent automatically;
  any scope change/re-consent requires the authorized account owner.
- `read_access_confirmed_no_reach_job`: catalog access worked, but no
  matching jobs were found; this is not a zero-impression report.
- `not_checked`: the check was not performed, not a success.

When a real report and P7 publication are known, use the service-token-protected
P9 `POST /analytics/candidate-performance/{attempt_id}/reporting-readiness`
for a metadata-only eligibility check (one completed America/Los_Angeles
reporting day). The authenticated reach-preview additionally downloads a
selected exact report, but is **not** an A/B winner calculation. Keep the
canonical P7 assignment, video-wide impressions and candidate-specific CTR
distinct. Never fabricate a missing video, job, or report ID.

## Observed local check, 2026-10-24

- OAuth client ID, client secret, refresh token and internal service token:
  **present** (values not recorded).
- P7/P9 loopback `/healthz` and `/readyz`: **passed**.
- P7/P9 reserved internal token GETs: **accepted**.
- Authenticated Google Reporting catalog GET: **permission denied** (HTTP 403).
- Follow-up read-only Google refresh + catalog probe: Google's token refresh
  **reported scopes but neither required Analytics read scope was present**,
  giving `google_oauth_scopes=required_scope_not_reported`. Scope consent is
  an account-owner action; no consent changes or token rotations were made.
  Whether additional channel/job/API restrictions exist is unverified.
- Real Google report downloads, service-to-service P7→P9 publication
  attribution and real per-candidate impressions/CTR: **not verified**.

Do not submit OAuth tokens, refresh tokens or private error response bodies in
tickets, documentation or chat messages. No credential rotation, consent,
deployment change, DB migration, publishing or P2 update was done here.
