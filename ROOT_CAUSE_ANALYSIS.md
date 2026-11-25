# Root Cause Analysis — Webtop HTTP 403 on Render

Branch: `fix/webtop-403-phase1-2`  
Investigation date: 2026-11-25  
Primary objective: authorized Webtop update from Render without tunnels/proxies/bypass.

## Confirmed defects (implementation)

| Defect | Location | Evidence | Correction |
|--------|----------|----------|------------|
| Import-time credential file writes | `bootstrap_token_from_env()` called at import | Module load overwrote `webtop_token.txt` from env | Explicit bootstrap only (`update_data` / diagnose) |
| Env token always beat file token | `extract_web_token()` | Stale env could mask renewed file token | `TokenManager` precedence + `WEBTOP_FORCE_ENV_TOKEN` for intentional rotation |
| Opaque token URL-decoding | `_clean_token()` | `%` triggered `unquote` on opaque secret | `normalize_token()` strips whitespace/quotes only |
| HTTP 200 without `status` treated as auth OK | `WebtopApi.post` / `check_token` | Missing-schema JSON returned as success | Require `status: true` schema |
| CloudFront HTML 403 treated as renewable auth failure | `ensure_auth` | After 403, InitDashboard + bio renew attempted | `EXTERNAL_ACCESS_REJECTED` short-circuits; no renew/retry |
| Proxy diagnostics incomplete | connectivity logs | Only env vars checked | Log `urllib.request.getproxies()` types + applicability (no URLs) |
| SSE leaked subprocess/secrets to browser | `server._stream_update` | Raw lines forwarded in `log` events | Browser sanitization; non-diag logs suppressed |
| Fake signature zero count | `update_data.scrape` | Hard-coded `pending: 0` | `status=not_checked`, `pending=null` |
| Cloud auth could be disabled | `server._authorized` | Empty APP_* allowed open access | Fail-closed when `ON_RENDER` |
| Wildcard CORS | `server.end_headers` | `Access-Control-Allow-Origin: *` | Removed |
| Secrets in Docker build context | `Dockerfile` `COPY . .` | No `.dockerignore` | Added `.dockerignore` |

## Verified evidence (runtime)

- Local Windows (this branch, `diagnose_webtop.py`, 2026-11-25): `CheckToken` **HTTP 200**, `body_kind=json`, `token_len=1323`, `getproxies_types=[]`, DNS OK.
- Same local success still traversed CloudFront: `via=…cloudfront.net`, `x_cache=Miss from cloudfront`, `x_amz_cf_pop=FRA56-P13`. Therefore “CloudFront is in path” ≠ “CloudFront always 403”.
- Render (Ohio/Frankfurt) and other clouds (Railway, Cloud Run Tel Aviv): HTTP 403, `Server=CloudFront`, `X-Cache=Error from cloudfront`, `Content-Type=text/html`, `body_kind=html`.
- Token SHA-256 fingerprints previously matched local vs Render.
- Matching Python 3.11.9 did not change cloud 403.
- Unauthenticated curl 403 locally is **not** equivalent to authenticated app requests (ruled out as root-cause proof).
- On the investigator Windows host: `urllib.request.getproxies()` returned `{}` — Registry/env proxy is **not** currently explaining local success on this machine.

## Unverified hypotheses

- CloudFront / AWS WAF geo or bot/IP-reputation rule on datacenter egress.
- Origin-side ACL after CloudFront (403 may originate at WAF, CloudFront, or origin — [CloudFront 403 docs](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/http-403-permission-denied.html)).
- Subtle TLS fingerprint differences (stdlib urllib vs residential browsers) — not proven; headers intentionally unchanged because local urllib already succeeds.

## Ruled out (with current evidence)

- Truncated / mismatched token as sole cause (fingerprints matched; local 200).
- Wrong Python minor version alone (3.11.9 pin did not fix cloud).
- Hosting provider identity alone (multiple clouds failed, including Tel Aviv).
- Missing env proxy variables alone (getproxies empty locally while app works).
- pywebtop as a drop-in Render fix (same Webtop host; uses httpx + cookie auth; no evidence it bypasses CloudFront 403 from datacenters). **Not installed in production.**

## External references reviewed

- pywebtop 0.0.2 (Jan–Feb 2026): httpx async, cookie `webToken`, username/password login — [GitHub](https://github.com/t0mer/pywebtop), [PyPI](https://pypi.org/project/pywebtop/).
- smartschool-webtop (HACS, Sep–Oct 2026): Home Assistant monitor — relevant as another consumer, not a Render networking fix.
- Python 3.11 urllib (getproxies, ProxyHandler, redirects).
- AWS CloudFront HTTP 403 / WAF notes.
- Render Free/Docker/env docs (ephemeral FS, no interactive shell on free).

## Implemented corrections (Phase 1–2)

- `TokenManager`, error categories, schema validation, redirect denial for authenticated API.
- Connectivity diagnostics including `getproxies()` + DNS.
- `diagnose_webtop.py`, `WEBTOP_DIAGNOSTICS`, safer server logging/SSE.
- `.dockerignore`, health endpoint, fail-closed cloud auth.
- Automated tests under `tests/`.

## Remaining blockers

**External:** Authorized requests from cloud egress still receive CloudFront HTML 403 in prior experiments. After this branch’s correctness fixes, a controlled Render smoke test is required. If 403 persists with correct classification and no renew storm, the blocker is external access policy — not application token parsing.

**Out of scope for this phase (documented for Phase 4):** persistent disk for `data.json`, POST+CSRF for update, full UI state machine, paid storage.

## Acceptance status

Not yet met for “Render Update Now succeeds end-to-end.”  
Met for: defect fixes, safer auth classification, diagnostics, regression tests, local compatibility preserved in code path.
