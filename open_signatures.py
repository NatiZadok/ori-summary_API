# -*- coding: utf-8 -*-
"""Open Webtop signatures with auto-filled login; CAPTCHA is completed by the user once."""
from __future__ import annotations

import sys
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

from webtop_auth import base_url, close_browser, ensure_logged_in, open_context, save_state


def main(target: str | None = None):
    dest = target or f"{base_url()}/signMessaes?view=Inbox"
    if dest.startswith("/"):
        dest = base_url() + dest

    with sync_playwright() as p:
        # Always headed so Cloudflare can be clicked once if needed
        browser, context = open_context(p, headed=True)
        page = context.pages[0] if context.pages else context.new_page()
        print("LOGIN Filling credentials — לחצי CAPTCHA ידנית אם מופיע", flush=True)
        ensure_logged_in(page, context, headed=True)
        page.goto(dest, wait_until="domcontentloaded", timeout=60000)
        save_state(context)
        print("OPENED", dest, flush=True)
        try:
            while True:
                if browser is not None and not browser.is_connected():
                    break
                page.wait_for_timeout(1500)
        except Exception:
            pass
        try:
            save_state(context)
        except Exception:
            pass
        close_browser(browser, context)


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    main(unquote(raw) if raw else None)
