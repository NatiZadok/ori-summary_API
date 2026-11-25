# Changelog

## Unreleased — `fix/webtop-403-phase1-2`

### Fixed
- Token lifecycle: no import-time file writes; deterministic precedence; intentional env rotation via `WEBTOP_FORCE_ENV_TOKEN`.
- Auth classification: CloudFront HTML 403 → `EXTERNAL_ACCESS_REJECTED` (no InitDashboard/bio renew storm).
- CheckToken: HTTP 200 without expected `status: true` schema is no longer treated as success.
- Opaque token normalization no longer URL-decodes credentials.
- Server: fail-closed Basic Auth on cloud; removed wildcard CORS; SSE/browser redaction; update subprocess timeout.
- Signatures: stop reporting unverified zero pending counts.
- Docker build context: `.dockerignore` excludes secrets and browser profiles.

### Added
- `TokenManager`, `ErrorCategory`, same-host API enforcement, redirect denial for authenticated calls.
- Connectivity diagnostics via `getproxies()` + DNS (no proxy URLs/credentials).
- `diagnose_webtop.py`, `WEBTOP_DIAGNOSTICS`, `/healthz`.
- Automated tests (`tests/`), `ROOT_CAUSE_ANALYSIS.md`, `CONNECTIVITY_COMPARISON.md`.

### Notes
- urllib request headers intentionally unchanged (local success path).
- pywebtop/httpx not adopted for production in this phase (no evidence it fixes cloud 403; avoids new dependency without approval).
