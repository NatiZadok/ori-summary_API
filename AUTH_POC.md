# Webtop dynamic authentication — Proof of Concept report

Branch: `feature/webtop-dynamic-auth-poc`  
Date: 2026-11-25

## Gate decision

**Full dynamic auth (no `WEBTOP_TOKEN` / no manual Render env) is NOT implemented as production replacement.**

Reason: the minimal authorized-auth PoC cannot clear the acceptance gate for Render.

| Gate | Result |
|------|--------|
| Official third-party OAuth / token exchange for Israeli Webtop | **Unavailable** (Zendesk = end-user help only; no partner OAuth docs) |
| Auth without storing Webtop passwords | Required and respected |
| Interactive login on official site | Required (CAPTCHA) — cannot run on Render server |
| Authorized session usable **from Render egress** | Historically **HTTP 403** CloudFront HTML (`EXTERNAL_ACCESS_REJECTED`) even with fingerprint-matched tokens |
| Remove `WEBTOP_TOKEN` dependency | **Blocked** until Render session probe is green |

## What was built (PoC only)

1. `webtop_connection.py` — connection status model, connect-info (no passwords), live session probe via existing `establish_session()`.
2. `session_store.py` — storage abstraction; documents ephemeral FS; optional Supabase stub **not provisioned**.
3. Server APIs (Basic Auth protected):
   - `GET /api/webtop/status`
   - `GET /api/webtop/connect-info`
   - `POST /api/webtop/poc-probe`
   - `POST /api/webtop/disconnect`
4. UI: status chip + התחברות / התחברות מחדש / ניתוק (opens official Webtop; runs PoC probe; accurate Hebrew statuses including **גישה נדחתה**).
5. **Legacy `WEBTOP_TOKEN` / TokenManager path kept** until replacement verified.

## Explicit non-goals in this PoC

- Did not delete `bootstrap_token_from_env` / `WEBTOP_FORCE_ENV_TOKEN`.
- Did not install pywebtop or collect passwords.
- Did not bypass CAPTCHA / WAF / CloudFront.
- Did not provision Supabase or Render Disk.
- Did not claim `webtop_token.txt` on Render is durable.

## How to verify the PoC on Render (manual)

1. Deploy this branch to a review service (when approved).
2. Open the app → **התחברות ל-Webtop** → run probe.
3. Inspect JSON `acceptance_gate` from `POST /api/webtop/poc-probe`.
4. Success requires `authorized_auth_from_render_verified=true` with real InitDashboard OK.
5. Only then implement full TokenManager replacement + remove env token dependency.

## Local PoC

```powershell
python -c "from webtop_connection import run_auth_poc; import json; print(json.dumps(run_auth_poc(), ensure_ascii=False, indent=2))"
```

Expect honest classification: `connected` / `expired` / `access_rejected` / `needs_login` — never a fake success.

## Next step if Render probe fails (expected)

Keep local update working; treat cloud Update as blocked with **גישה נדחתה**; contact SmartSchool/Webtop for an official partner API — do not hide the limitation.
