# -*- coding: utf-8 -*-
"""
Login via a real Chrome window (not Playwright-controlled launch).
Cloudflare Turnstile often fails inside Playwright; here Chrome runs normally,
and we only attach afterwards to save the session for Render.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from webtop_auth import CONFIG, base_url, save_state

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / ".webtop-manual-profile"
DEBUG_PORT = 9333


def _find_chrome() -> str:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise RuntimeError("לא נמצא Chrome או Edge במחשב")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _launch_chrome(url: str) -> subprocess.Popen:
    PROFILE.mkdir(exist_ok=True)
    chrome = _find_chrome()
    args = [
        chrome,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]
    print(f"BROWSER launching {chrome}", flush=True)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _login_form_visible(page) -> bool:
    try:
        return page.locator("input[type='password']").count() > 0
    except Exception:
        return False


def _turnstile_failed(page) -> bool:
    try:
        return page.get_by_text("האימות נכשל", exact=False).count() > 0
    except Exception:
        return False


def _is_logged_in(page) -> bool:
    url = page.url or ""
    if "/account/login" in url or _login_form_visible(page):
        return False
    return any(
        p in url
        for p in ("/dashboard", "/Messages", "/signMessaes", "/Student_Card", "/notification")
    )


def _fill_once(page) -> None:
    try:
        user = page.locator("input[type='text'], input[name='username'], input[autocomplete='username']").first
        pwd = page.locator("input[type='password']").first
        if user.count() == 0 or pwd.count() == 0:
            return
        user.fill(CONFIG["username"])
        pwd.fill(CONFIG["password"])
        print(f"LOGIN filled user={CONFIG['username']!r} — עכשיו רק CAPTCHA + כניסה ידנית", flush=True)
    except Exception as exc:
        print(f"LOGIN fill skipped: {exc}", flush=True)


def main() -> int:
    login_url = f"{base_url()}/account/login"
    print("=" * 50, flush=True)
    print("התחברות Webtop ב־Chrome רגיל (בלי אוטומציה על ה־CAPTCHA)", flush=True)
    print("1) יופיע חלון Chrome", flush=True)
    print("2) אם כתוב «האימות נכשל» — לחצי F5 לרענון", flush=True)
    print("3) סמני את תיבת Cloudflare / האימות", flush=True)
    print("4) לחצי «כניסה»", flush=True)
    print("=" * 50, flush=True)

    if not _port_open(DEBUG_PORT):
        _launch_chrome(login_url)
    else:
        print(f"BROWSER already on port {DEBUG_PORT} — מתחבר…", flush=True)

    deadline = time.time() + 15
    while time.time() < deadline and not _port_open(DEBUG_PORT):
        time.sleep(0.3)
    if not _port_open(DEBUG_PORT):
        raise RuntimeError("Chrome לא נפתח עם remote debugging")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        if "/account/login" not in (page.url or ""):
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.locator("button:has-text('אשר cookies')").click(timeout=2500)
        except Exception:
            pass

        if not _is_logged_in(page):
            _fill_once(page)

        print("מחכה שתסיימי התחברות בחלון Chrome (עד 8 דקות)…", flush=True)
        end = time.time() + 480
        reloads = 0
        while time.time() < end:
            try:
                if _is_logged_in(page):
                    break
                if _turnstile_failed(page) and reloads < 3:
                    print("LOGIN Turnstile נכשל — מרענן (F5)… לחצי שוב על האימות", flush=True)
                    page.reload(wait_until="domcontentloaded")
                    time.sleep(1)
                    _fill_once(page)
                    reloads += 1
            except Exception:
                pass
            time.sleep(2)
        else:
            print("TIMEOUT — לא זוהתה התחברות. נסי שוב: סגרי את Chrome והריצי את הקובץ מחדש.", flush=True)
            return 1

        # אימות קצר בדף פנימי
        page.goto(f"{base_url()}/notification", wait_until="domcontentloaded", timeout=60000)
        time.sleep(1.5)
        if not _is_logged_in(page):
            print("FAIL — עדיין במסך התחברות אחרי ניווט", flush=True)
            return 1

        save_state(context)
        print("OK — הסשן נשמר ב־webtop_state.b64.txt", flush=True)
        print("עכשיו: העתיקי את הקובץ ל־WEBTOP_STATE_B64 ב־Render → Save → Redeploy", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
