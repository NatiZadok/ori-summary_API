# Webtop authentication feasibility (no UI, no deploy)

Date: 2026-11-25  
Branch context: `feature/webtop-dynamic-auth-poc`  
Scope: Determine whether a **fresh authorized session** can be established **without** existing `WEBTOP_TOKEN` / `webtop_token.txt`, via a **supported** interactive login + token exchange.

**Conclusion: Not available for our constraints. Do not implement further dynamic-auth UI.**

---

## 1. Authentication methods found

| Method | Source | Official / supported? | Needs prior secret? | Stores Webtop password? | CAPTCHA / bot protection | Notes |
|--------|--------|----------------------|---------------------|-------------------------|--------------------------|-------|
| Browser login on `webtop.smartschool.co.il` | Official product UI; Zendesk user help | **Yes** (end-user) | No | N/A (user types in browser) | Yes (reCAPTCHA / Turnstile) | Only fully legitimate interactive login |
| `LoginByUserNameAndPassword` | pywebtop; alexpekurovsky HA; iddo/ha-webtop | **Unofficial** reverse-engineered API | No (uses username/password) | **Yes** (integrations store password) | Often blocks (`CaptchaRequired` / status=false) | Not a published partner API; violates “do not store passwords” |
| Paste / load `webToken` cookie | All unofficial clients + our app | Unofficial session reuse | **Yes** (`webToken`) | No | No for later API calls | Validation ≠ minting |
| `user/loginByBio` | Shanksg/smartschool-webtop; our `renew_via_bio` | Unofficial but used by browser “remember device” | **Yes** (browser-captured `bioLogin`) | No | No | Renewal only; cannot bootstrap from zero |
| OAuth / authorize redirect for third-party apps | Belgian Smartschool.be OAuth; IL Zendesk | **Not found for Israeli Webtop** | — | — | — | Zendesk has no developer OAuth docs |
| OTP / “כניסה חד פעמית” | Official Zendesk | End-user recovery in **official app/site** | Phone/email verified | No | Yes | Not documented as third-party token exchange |

### Reference summaries

**pywebtop (unofficial)**  
Password → `LoginByUserNameAndPassword` → `data.token` → cookie `webToken`. Disclaimer: not affiliated with SmartSchool.

**alexpekurovsky/hass-smartschool-integration (unofficial)**  
Config flow asks for **username + password**, encrypts a `Data` field (AES constants reverse-engineered from JS), calls the same login endpoint, stores credentials in HA. Same unofficial password path.

**iddo/ha-webtop (unofficial)**  
Also password login; documents Cloudflare Turnstile / captcha: integration **will not** solve captcha; may get `CaptchaRequired`.

**Shanksg/smartschool-webtop (unofficial, strong empirical notes)**  
States password login is **blocked by reCAPTCHA** (server-verified). Recommends browser-captured `bioLogin` for renewal, or pasted `webToken`. Self-registered bio rejected; only browser-registered bio works.

**Official Zendesk (he)**  
Password reset, OTP one-time entry, profile — **no** third-party OAuth, no app client registration, no token-exchange API for external apps.

---

## 2. Fresh session without `WEBTOP_TOKEN` / `webtop_token.txt`?

**Under our requirements (no password storage, respect CAPTCHA, no bypass): No.**

Isolated local probe (2026-11-25), empty temp credential store, env cleared, **real project files not deleted**:

| Step | Result |
|------|--------|
| `TokenManager.load()` | `source=none`, no token |
| `LoginByUserNameAndPassword` (config username/password present; values not logged) | HTTP 200 JSON but **`status=false`** → `AUTHENTICATION_REQUIRED` (failed) |
| `loginByBio` | `false` (no bio) |
| `establish_session()` | `MISSING_TOKEN` |

So: **cannot mint a usable session from zero** without either (a) browser-interactive capture of `webToken`/`bioLogin`, or (b) storing/using password against an unofficial API that CAPTCHA often rejects.

---

## 3. Supported interactive login + token exchange?

| Desired piece | Available? |
|---------------|------------|
| User authenticates on official Webtop page | Yes |
| Official redirect back to our app with auth code | **No** |
| Official token endpoint for our client_id | **No** |
| Our server receives a new `webToken` without manual paste / local Playwright capture | **No supported path** |

Interactive login exists **only inside Webtop’s own UI**. There is no supported “Authorize ori-summary” exchange to wire into `TokenManager` / `SessionStore`.

---

## 4. CAPTCHA / MFA / access restrictions

- Respect: **do not** automate CAPTCHA solving or WAF bypass.  
- Password API attempts remain subject to bot protection (failed in our isolated test; documented by iddo/Shanksg).  
- Render/cloud **HTTP 403** HTML from CloudFront remains a separate egress issue even when a token exists.

---

## 5. Integration decision

**Do not integrate** unofficial password login into TokenManager/SessionStore.

Reasons:
1. Not an officially supported third-party interface.  
2. Requires storing Webtop passwords (explicitly disallowed).  
3. CAPTCHA/Turnstile prevents reliable unattended/mint-from-phone-on-Render flows.  
4. Does not satisfy “supported interactive login and token exchange.”

**Keep** existing verified local path: browser / `רענון-טוקן.bat` → `webToken` (+ optional bio) → `establish_session()` (CheckToken + CheckBackgroundToken + InitDashboard).

`loginByBio` remains valid **renewal** only when bio was previously captured in a browser — it is **not** bootstrap-from-zero dynamic auth.

---

## 6. Session validation standard (existing, keep)

When a token **does** exist, success requires:

1. `dashboard/CheckToken` → `data === true`  
2. `dashboard/CheckBackgroundToken` → `data === true`  
3. `dashboard/InitDashboard` → object payload  

Validating an existing token is **not** dynamic authentication.

---

## 7. Why prior PoC was insufficient

The PoC button only opened the official site and probed **existing** credentials. That was correctly rejected as not meeting the request. This document supersedes any implication that dynamic auth is ready.

---

## 8. What would unblock a real product path

Only one of:

1. **Official** Webtop/SmartSchool Israel partner API or OAuth for third-party apps, or  
2. User accepts **unofficial password storage** + accepts captcha failures (still fails your stated rules), or  
3. Hybrid: interactive capture always on a device that can run a browser tool, with secrets synced to Render — **still** subject to cloud **403** for API calls from Render.

Until (1) exists and cloud egress works, **do not** build more connection UI claiming password-free, PC-free, Render-native login.

---

## 9. Actions taken / not taken

| Action | Done? |
|--------|-------|
| Review pywebtop / HA integrations / Zendesk | Yes |
| Isolated zero-credential test | Yes |
| Integrate unsupported password auth | **No** |
| Additional UI | **No** |
| Push / deploy | **No** |
| Preserve working implementation | Yes |
