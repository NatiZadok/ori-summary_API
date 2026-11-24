# -*- coding: utf-8 -*-
"""
One-time local tool: open real Chrome, login to Webtop (with Remember me),
save webToken to webtop_token.txt and copy it to the clipboard for Render.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / ".webtop-token-profile"
DEBUG_PORT = 9334
LOGIN_URL = "https://webtop.smartschool.co.il/account/login"


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
    raise RuntimeError("לא נמצא Chrome או Edge")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _copy_clipboard(text: str) -> bool:
    try:
        p = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
            ],
            input=text,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        return p.returncode == 0
    except Exception:
        return False


def _load_credentials() -> tuple[str, str]:
    user = os.environ.get("WEBTOP_USERNAME", "")
    password = os.environ.get("WEBTOP_PASSWORD", "")
    cfg = ROOT / "config.json"
    if cfg.exists():
        try:
            import json

            data = json.loads(cfg.read_text(encoding="utf-8"))
            user = user or data.get("username") or ""
            password = password or data.get("password") or ""
        except Exception:
            pass
    return user, password


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("חסר Playwright. הריצי: pip install playwright && playwright install chromium", flush=True)
        return 1

    from webtop_api import WebtopApi, save_token

    print("=" * 52, flush=True)
    print("ייצור / רענון WEBTOP_TOKEN (Remember me)", flush=True)
    print("1) ייפתח Chrome", flush=True)
    print("2) אם CAPTCHA — סמני ידנית ואז «כניסה»", flush=True)
    print("3) אחרי כניסה הטוקן יישמר ויועתק ללוח", flush=True)
    print("=" * 52, flush=True)

    PROFILE.mkdir(exist_ok=True)
    chrome = _find_chrome()
    if not _port_open(DEBUG_PORT):
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={PROFILE}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                LOGIN_URL,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    for _ in range(40):
        if _port_open(DEBUG_PORT):
            break
        time.sleep(0.25)
    else:
        print("Chrome לא נפתח", flush=True)
        return 1

    user, password = _load_credentials()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        # Remember me לפני login — מאריך תוקף הטוקן
        try:
            context.add_cookies(
                [
                    {
                        "name": "saveRememberMe",
                        "value": "1",
                        "domain": ".smartschool.co.il",
                        "path": "/",
                    }
                ]
            )
        except Exception:
            pass

        if "login" not in (page.url or ""):
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        try:
            page.locator("button:has-text('אשר cookies')").click(timeout=2500)
        except Exception:
            pass

        if user and password:
            try:
                page.locator("input[type='password']").first.wait_for(timeout=15000)
                page.locator(
                    "input[type='text'], input[name='username'], input[autocomplete='username']"
                ).first.fill(user)
                page.locator("input[type='password']").first.fill(password)
                print(f"מילאתי משתמש={user!r} — עכשיו CAPTCHA + כניסה ידנית", flush=True)
            except Exception as exc:
                print(f"מילוי אוטומטי דולג: {exc}", flush=True)

        print("מחכה להתחברות (עד 8 דקות)…", flush=True)
        end = time.time() + 480
        token = ""
        while time.time() < end:
            url = page.url or ""
            if "/account/login" not in url:
                for c in context.cookies():
                    if c.get("name") == "webToken" and c.get("value"):
                        token = c["value"]
                        break
                if token:
                    break
            # Turnstile failed → reload once in a while
            try:
                if page.get_by_text("האימות נכשל", exact=False).count() > 0:
                    print("האימות נכשל — מרענן…", flush=True)
                    page.reload(wait_until="domcontentloaded")
                    time.sleep(1)
                    if user and password:
                        try:
                            page.locator(
                                "input[type='text'], input[name='username']"
                            ).first.fill(user)
                            page.locator("input[type='password']").first.fill(password)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(2)

        if not token:
            print("TIMEOUT — לא התקבלה התחברות / webToken", flush=True)
            return 1

        save_token(token)
        api = WebtopApi(token)
        ok = api.check_token()
        if not ok:
            print("אזהרה: הטוקן נשמר אבל CheckToken נכשל — נסי שוב", flush=True)
            return 1

        clipped = _copy_clipboard(token)
        print("", flush=True)
        print("OK — הטוקן תקף ונשמר ב־webtop_token.txt", flush=True)
        if clipped:
            print("הועתק ללוח (Clipboard).", flush=True)
        print("עכשיו ב־Render → Environment → WEBTOP_TOKEN → הדביקי (Ctrl+V) → Save", flush=True)
        print(f"אורך טוקן: {len(token)} · מתחיל ב־{token[:8]}…", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
