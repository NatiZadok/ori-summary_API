# -*- coding: utf-8 -*-
"""Shared Webtop login: credentials from env or config.json; session reuse for cloud."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "webtop_state.json"
ON_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("WEBTOP_CLOUD") == "1")


def _load_config() -> dict:
    cfg: dict = {
        "username": os.environ.get("WEBTOP_USERNAME", ""),
        "password": os.environ.get("WEBTOP_PASSWORD", ""),
        "base_url": os.environ.get("WEBTOP_BASE_URL", "https://webtop.smartschool.co.il"),
    }
    local = ROOT / "config.json"
    if local.exists():
        try:
            file_cfg = json.loads(local.read_text(encoding="utf-8"))
            for key in ("username", "password", "base_url"):
                if not cfg.get(key) and file_cfg.get(key):
                    cfg[key] = file_cfg[key]
        except Exception:
            pass
    if not cfg.get("username") or not cfg.get("password"):
        raise RuntimeError(
            "חסרים פרטי Webtop. הגדירי WEBTOP_USERNAME ו־WEBTOP_PASSWORD "
            "(ב־Render Environment) או קובץ config.json מקומי."
        )
    return cfg


def bootstrap_state_from_env() -> None:
    """Load session for cloud from WEBTOP_STATE_B64 (preferred) or WEBTOP_STATE_JSON."""
    import base64

    b64 = os.environ.get("WEBTOP_STATE_B64", "").strip()
    raw = os.environ.get("WEBTOP_STATE_JSON", "").strip()
    try:
        if b64:
            data = json.loads(base64.b64decode(b64).decode("utf-8"))
            source = "WEBTOP_STATE_B64"
        elif raw:
            data = json.loads(raw)
            source = "WEBTOP_STATE_JSON"
        else:
            return
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"BOOTSTRAP wrote webtop_state.json from {source}", flush=True)
    except Exception as exc:
        print(f"BOOTSTRAP state env invalid: {exc}", flush=True)


CONFIG = _load_config()
bootstrap_state_from_env()


def base_url() -> str:
    return CONFIG["base_url"].rstrip("/")


def _accept_cookies(page: Page) -> None:
    try:
        page.get_by_role("button", name="אשר cookies").click(timeout=2500)
    except Exception:
        pass


def _has_turnstile(page: Page) -> bool:
    try:
        if page.locator("iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']").count() > 0:
            return True
        for t in ("יש לאמת שאינך רובוט", "אני לא רובוט", "Verify you are human", "האימות נכשל"):
            if page.get_by_text(t, exact=False).count() > 0:
                return True
        return False
    except Exception:
        return False


def _turnstile_failed(page: Page) -> bool:
    try:
        return page.get_by_text("האימות נכשל", exact=False).count() > 0
    except Exception:
        return False


def _login_form_visible(page: Page) -> bool:
    """True when the account login form is actually on screen (not just URL)."""
    try:
        if page.locator("input[type='password']").count() > 0:
            return True
        if page.get_by_role("textbox", name="שם משתמש").count() > 0:
            return True
    except Exception:
        pass
    return False


def _is_logged_in(page: Page) -> bool:
    if _login_form_visible(page) or "/account/login" in (page.url or ""):
        return False
    url = page.url or ""
    if "/dashboard" in url or "/Messages" in url or "/signMessaes" in url or "/Student_Card" in url or "/notification" in url:
        return True
    try:
        return page.get_by_role("link", name="ריכוז מידע").count() > 0
    except Exception:
        return False


def save_state(context: BrowserContext) -> None:
    """Persist Playwright storage + base64 copy for Render WEBTOP_STATE_B64."""
    import base64

    context.storage_state(path=str(STATE_FILE))
    try:
        raw = STATE_FILE.read_bytes()
        (ROOT / "webtop_state.b64.txt").write_text(
            base64.b64encode(raw).decode("ascii"),
            encoding="utf-8",
        )
        print("STATE saved webtop_state.json + webtop_state.b64.txt", flush=True)
    except Exception as exc:
        print(f"STATE b64 write failed: {exc}", flush=True)


def _user_locator(page: Page):
    candidates = [
        page.get_by_role("textbox", name="שם משתמש"),
        page.locator("input[name='username']"),
        page.locator("input[name='UserName']"),
        page.locator("input[autocomplete='username']"),
        page.locator("input[type='text']").first,
        page.locator("form input").nth(0),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return page.get_by_role("textbox", name="שם משתמש")


def _pass_locator(page: Page):
    candidates = [
        page.get_by_role("textbox", name="סיסמה"),
        page.locator("input[type='password']"),
        page.locator("input[name='password']"),
        page.locator("input[name='Password']"),
        page.locator("input[autocomplete='current-password']"),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return page.locator("input[type='password']").first


def _set_input_value(page: Page, locator, value: str) -> None:
    locator.wait_for(state="visible", timeout=20000)
    try:
        locator.click(timeout=3000, force=True)
    except Exception:
        pass
    try:
        locator.fill("")
        locator.fill(value)
    except Exception:
        try:
            locator.press_sequentially(value, delay=25)
        except Exception:
            locator.fill(value, force=True)
    try:
        locator.evaluate(
            """(el, v) => {
              const proto = el instanceof HTMLInputElement
                ? window.HTMLInputElement.prototype
                : window.HTMLTextAreaElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(el, v);
              else el.value = v;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )
    except Exception:
        pass


def _fill_credentials(page: Page) -> None:
    page.wait_for_timeout(400)
    user = _user_locator(page)
    pwd = _pass_locator(page)
    _set_input_value(page, user, CONFIG["username"])
    _set_input_value(page, pwd, CONFIG["password"])
    print(f"LOGIN filled user={CONFIG['username']!r}", flush=True)


def _try_click_not_robot(page: Page) -> bool:
    if _turnstile_failed(page):
        return False
    page.wait_for_timeout(500)
    try:
        iframe = page.locator("iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']").first
        if iframe.count() == 0:
            return False
        box = iframe.bounding_box()
        if not box:
            return False
        page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
        page.wait_for_timeout(2000)
        return not _turnstile_failed(page)
    except Exception:
        return False


def _try_click_login(page: Page) -> None:
    try:
        page.get_by_role("button", name="כניסה", exact=True).click(timeout=4000)
    except Exception:
        try:
            page.locator("button:has-text('כניסה')").first.click(timeout=4000)
        except Exception:
            pass


def login_page(page: Page, *, wait_human_captcha: bool = True, timeout_ms: int = 300000) -> None:
    page.goto(f"{base_url()}/account/login", wait_until="domcontentloaded", timeout=60000)
    _accept_cookies(page)

    if _is_logged_in(page):
        return

    try:
        _user_locator(page).wait_for(state="visible", timeout=30000)
    except PlaywrightTimeout:
        page.reload(wait_until="domcontentloaded")
        _accept_cookies(page)
        _user_locator(page).wait_for(state="visible", timeout=30000)

    _fill_credentials(page)

    if _turnstile_failed(page):
        page.reload(wait_until="domcontentloaded")
        _accept_cookies(page)
        _user_locator(page).wait_for(state="visible", timeout=30000)
        _fill_credentials(page)

    # במצב אנושי: לא ללחוץ אוטומטית על Turnstile — זה שובר את הלחיצה הידנית
    human = wait_human_captcha and not ON_CLOUD
    if not human:
        page.wait_for_timeout(600)
        _try_click_not_robot(page)
        _fill_credentials(page)
        _try_click_login(page)

    try:
        page.wait_for_url(lambda u: "/account/login" not in (u or ""), timeout=8000 if human else 25000)
        return
    except Exception:
        pass

    if human:
        import time

        try:
            page.bring_to_front()
        except Exception:
            pass
        print(
            "LOGIN waiting for CAPTCHA / login… "
            "לחצי בחלון Chrome על האימות ואז «כניסה» (עד 5 דקות).",
            flush=True,
        )
        end = time.time() + (timeout_ms / 1000)
        while time.time() < end:
            if not _on_login_screen(page) and _is_logged_in(page):
                return
            if "/account/login" not in (page.url or "") and not _login_form_visible(page):
                return
            page.wait_for_timeout(2000)
        raise PlaywrightTimeout("timeout waiting for login after CAPTCHA")

    raise RuntimeError(
        "Cloudflare חוסם התחברות בענן. "
        "התחברי פעם אחת במחשב (התחברות-webtop.bat), "
        "ואז העתיקי את webtop_state.b64.txt למשתנה WEBTOP_STATE_B64 ב־Render."
    )


def close_browser(browser: Browser | None, context: BrowserContext) -> None:
    try:
        context.close()
    except Exception:
        pass
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass


def open_context(playwright, *, headed: bool = False) -> tuple[Browser | None, BrowserContext]:
    """Open browser. Prefer webtop_state.json from login_webtop (same session local+cloud)."""
    use_headed = headed and not ON_CLOUD

    # חשוב: לא להשתמש בפרופיל Playwright נפרד — הוא מתעלם מהסשן של login_webtop
    # ולכן «התחברות הצליחה» עם 0 הודעות/אירועים.
    if STATE_FILE.exists():
        launch_kwargs: dict = {
            "headless": not use_headed,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if use_headed:
            launch_kwargs["args"].append("--start-maximized")
            launch_kwargs["channel"] = "chrome"
        try:
            browser = playwright.chromium.launch(**launch_kwargs)
        except Exception:
            launch_kwargs.pop("channel", None)
            browser = playwright.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict = {
            "locale": "he-IL",
            "storage_state": str(STATE_FILE),
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if use_headed:
            ctx_kwargs["viewport"] = None
        context = browser.new_context(**ctx_kwargs)
        print(f"BROWSER using saved session {STATE_FILE.name} headed={use_headed}", flush=True)
        return browser, context

    if use_headed:
        print(
            "BROWSER אין webtop_state.json — הריצי קודם התחברות-webtop.bat",
            flush=True,
        )

    launch_kwargs = {
        "headless": not use_headed,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if use_headed:
        launch_kwargs["args"].append("--start-maximized")
        launch_kwargs["channel"] = "chrome"
    try:
        browser = playwright.chromium.launch(**launch_kwargs)
    except Exception:
        launch_kwargs.pop("channel", None)
        browser = playwright.chromium.launch(**launch_kwargs)
    ctx_kwargs = {
        "locale": "he-IL",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    if use_headed:
        ctx_kwargs["viewport"] = None
    context = browser.new_context(**ctx_kwargs)
    return browser, context


def _settle(page: Page, ms: int = 1500) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def _on_login_screen(page: Page) -> bool:
    if "/account/login" in (page.url or ""):
        return True
    return _login_form_visible(page)


def _session_probe(page: Page) -> str:
    """Short diagnostic of current page for empty-scrape debugging."""
    try:
        text = (page.locator("body").inner_text(timeout=3000) or "").replace("\n", " ")[:180]
    except Exception:
        text = ""
    return f"url={page.url!r} login_form={_login_form_visible(page)} text={text!r}"


def ensure_logged_in(page: Page, context: BrowserContext, *, headed: bool) -> None:
    page.goto(f"{base_url()}/dashboard", wait_until="domcontentloaded", timeout=60000)
    _settle(page)
    # סשן ישן לעיתים «נדמה» מחובר לרגע ואז חוזר ל־login / טופס כניסה
    if _on_login_screen(page) or not _is_logged_in(page):
        login_page(page, wait_human_captcha=headed and not ON_CLOUD)
        page.goto(f"{base_url()}/dashboard", wait_until="domcontentloaded", timeout=60000)
        _settle(page)

    if _on_login_screen(page) or not _is_logged_in(page):
        print(f"AUTH_FAIL after dashboard: {_session_probe(page)}", flush=True)
        raise RuntimeError(
            "ההתחברות ל־Webtop נכשלה (סשן לא תקף / Cloudflare). "
            "התחברי מקומית עם חלון דפדפן, ואז עדכני WEBTOP_STATE_B64 ב־Render."
        )

    # אימות אמיתי: דף פנימי חייב להישאר מחובר (בלי טופס כניסה)
    page.goto(f"{base_url()}/notification", wait_until="domcontentloaded", timeout=60000)
    _settle(page, 2000)
    if _on_login_screen(page) or not _is_logged_in(page):
        print(f"AUTH_FAIL after notification: {_session_probe(page)}", flush=True)
        raise RuntimeError(
            "הסשן לא מחזיק אחרי מעבר לדפים פנימיים. "
            "יש להתחבר מחדש מקומית ולרענן את WEBTOP_STATE_B64."
        )
    # סימן תוכן מינימלי — דף התראות ריק לגמרי / דף שגיאה = סשן חלש
    try:
        body_len = len(page.locator("body").inner_text(timeout=3000) or "")
    except Exception:
        body_len = 0
    print(f"AUTH_OK {_session_probe(page)} body_len={body_len}", flush=True)
    if body_len < 40:
        raise RuntimeError(
            "הדף הפנימי נטען ריק אחרי התחברות. נסי שוב מקומית (CAPTCHA) ורענני סשן."
        )
    save_state(context)


def goto_authed(page: Page, context: BrowserContext, path: str, *, headed: bool) -> None:
    """Navigate to an internal path; re-login once if redirected to login."""
    url = path if path.startswith("http") else f"{base_url()}{path}"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    _settle(page, 2000)
    if _on_login_screen(page):
        print(f"REAUTH required after navigation to {path}: {_session_probe(page)}", flush=True)
        ensure_logged_in(page, context, headed=headed)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _settle(page, 2000)
    if _on_login_screen(page):
        print(f"AUTH_FAIL still on login for {path}: {_session_probe(page)}", flush=True)
        raise RuntimeError(f"עדיין במסך התחברות אחרי ניסיון גישה ל־{path}")
