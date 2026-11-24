# -*- coding: utf-8 -*-
"""
One-time local setup:
1) Login in Chrome (CAPTCHA once)
2) Save webToken + bioLogin (for ~1 year auto-renew, no more clicks)
3) Copy WEBTOP_BIO_JSON to clipboard for Render
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
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

    from webtop_api import WebtopApi, save_bio, save_token

    print("=" * 56, flush=True)
    print("הגדרה חד־פעמית לחידוש אוטומטי (בלי לחיצות אחר כך)", flush=True)
    print("1) Chrome + CAPTCHA + כניסה — פעם אחת", flush=True)
    print("2) נשמר bio (~שנה) + טוקן", flush=True)
    print("3) מועתק ללוח: WEBTOP_BIO_JSON ל־Render", flush=True)
    print("=" * 56, flush=True)

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
    captured_bio: dict = {}

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        def on_request(req):
            try:
                if "loginByBio" not in (req.url or ""):
                    return
                raw = req.post_data
                if not raw:
                    return
                body = json.loads(raw)
                if body.get("id"):
                    captured_bio.clear()
                    captured_bio.update(
                        {
                            "bioLogin": body.get("id"),
                            "deviceId": body.get("param2") or "",
                            "selectedUser": body.get("param3") or "",
                            "uniqueId": body.get("param4") or "",
                            "isMobile": str(body.get("param5", "true")).lower() == "true",
                            "mode": str(body.get("param1", "2")),
                        }
                    )
                    print("BIO captured from loginByBio", flush=True)
            except Exception:
                pass

        page.on("request", on_request)

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

        # Trigger loginByBio (SPA often calls it after reload when remember-me exists)
        print("מנסה ללכוד bio לחידוש אוטומטי…", flush=True)
        try:
            page.goto(
                "https://webtop.smartschool.co.il/dashboard",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(4000)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
        except Exception:
            pass

        # Fallback: cookie named bioLogin
        if not captured_bio.get("bioLogin"):
            for c in context.cookies():
                if c.get("name") == "bioLogin" and c.get("value"):
                    unique = next(
                        (x.get("value") for x in context.cookies() if x.get("name") == "uniqueId"),
                        "",
                    )
                    captured_bio.update(
                        {
                            "bioLogin": c["value"],
                            "deviceId": "",
                            "selectedUser": "",
                            "uniqueId": unique or "",
                            "isMobile": True,
                            "mode": "2",
                        }
                    )
                    print("BIO captured from cookie", flush=True)
                    break

        api = WebtopApi(token)
        if not api.check_token():
            print("אזהרה: הטוקן נשמר אבל CheckToken נכשל", flush=True)
            return 1

        bio_ok = False
        if captured_bio.get("bioLogin"):
            save_bio(captured_bio)
            # Verify renew works
            test = WebtopApi("")
            bio_ok = test.renew_via_bio()
            if bio_ok:
                print("BIO verified — חידוש אוטומטי עובד", flush=True)
            else:
                print("BIO נשמר אבל האימות נכשל — עדיין אפשר להשתמש בטוקן", flush=True)

        clip_payload = (
            json.dumps(captured_bio, ensure_ascii=False)
            if captured_bio.get("bioLogin")
            else token
        )
        clipped = _copy_clipboard(clip_payload)

        print("", flush=True)
        print("OK — נשמר מקומית.", flush=True)
        if captured_bio.get("bioLogin"):
            print("ב־Render שימי משתנה:", flush=True)
            print("  WEBTOP_BIO_JSON  =  (מה שהועתק ללוח — כל ה־JSON)", flush=True)
            print("אחרי זה «עדכן עכשיו» יחדש טוקן לבד (~שנה).", flush=True)
        else:
            print("לא נלכד bio — הועתק רק WEBTOP_TOKEN (קצר יותר).", flush=True)
            print("נסי שוב אחרי כניסה מוצלחת, או רענון דף ב־Webtop.", flush=True)
        if clipped:
            print("הועתק ללוח.", flush=True)
        return 0 if (bio_ok or token) else 1


if __name__ == "__main__":
    raise SystemExit(main())
