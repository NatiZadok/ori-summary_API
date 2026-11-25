# Connectivity Comparison — Windows vs Render

Use the **same git revision** and a currently valid authorized token.

## How to capture

### Windows (PowerShell)

```powershell
cd C:\Users\nben-har\ori-summary_API
git rev-parse --short HEAD
$env:WEBTOP_DIAGNOSTICS = "1"
python diagnose_webtop.py
python update_data.py
```

### Render

1. Deploy branch `fix/webtop-403-phase1-2` (manual approve — do not auto-deploy from this investigation).
2. Set `WEBTOP_DIAGNOSTICS=1` temporarily.
3. Click **עדכן עכשיו** (or run diagnose via one-off job if available).
4. Copy sanitized log lines: `CONNECTIVITY diag`, `HTTP diag`, `AUTH CheckToken`.

## Fields to compare

| Field | Windows (`diagnose_webtop.py` 2026-11-25) | Render (fill after deploy) |
|-------|---------------------------------------------|----------------------------|
| git | `a4b0a699c909` base + branch WIP | `RENDER_GIT_COMMIT` / log `git=` |
| python | 3.11.9 | expect 3.11.9 image |
| openssl | OpenSSL 3.0.13 30 Jan 2024 | fill |
| platform | win32 | linux |
| cloud | False | True |
| api_base | `https://webtopserver.smartschool.co.il/server/api` | same |
| token_source | file | fill |
| token_len | 1323 | |
| token_fp16 | `d53b24e97559ebd5` (compare; do not paste full token) | |
| getproxies_types | `[]` | |
| https_proxy_applicable | False | |
| dns_ok | True | |
| CheckToken status | **200** | historically **403** |
| via | CloudFront present | |
| x-cache | **Miss from cloudfront** | historically **Error from cloudfront** |
| x-amz-cf-pop | FRA56-P13 | |
| body_kind | json | html |
| error category (new) | none / success | should be `EXTERNAL_ACCESS_REJECTED` |
| bio renew attempted | only on auth failures | must be **false** on HTML 403 |

## Interpretation guide

- Matching `token_sha256` + cloud `body_kind=html` + `EXTERNAL_ACCESS_REJECTED` → application credential handling is not the differentiator; edge/origin rejected the request.
- Differing `getproxies_types` → document effective proxy applicability (still no bypass).
- Differing openssl/python after pin → revisit image/runtime.
- `UNEXPECTED_RESPONSE` on 200 → schema/client bug (now tested).

## Notes

- Do not paste tokens, cookies, or raw HTML bodies into this table.
- Unauthenticated curl is not a valid comparison control for authenticated app requests.
