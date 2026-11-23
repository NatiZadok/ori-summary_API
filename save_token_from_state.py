# -*- coding: utf-8 -*-
"""Extract webToken from webtop_state.json / WEBTOP_STATE_B64 into webtop_token.txt."""
from __future__ import annotations

from webtop_api import extract_web_token, save_token


def main() -> int:
    token = extract_web_token()
    save_token(token)
    print(f"OK token length={len(token)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
