# -*- coding: utf-8 -*-
"""Safe Webtop connectivity + full authentication diagnostics.

Uses the same HTTP client as production.
Never prints tokens, cookies, passwords, Authorization values, or response bodies.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
from urllib.parse import urlparse

from webtop_api import (
    ALLOWED_API_HOST,
    ON_CLOUD,
    WebtopApi,
    bootstrap_token_from_env,
    git_revision,
    log_connectivity,
    proxy_config_summary,
    resolve_api_host,
    short_fingerprint,
)


def main() -> int:
    bootstrap_token_from_env()
    api = WebtopApi()
    parsed = urlparse(api.api_base)
    host = parsed.hostname or ALLOWED_API_HOST
    started = time.perf_counter()
    log_connectivity(api_base=api.api_base, token=api.token, token_source=api._token_source)
    base = {
        "git": git_revision(),
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "os": sys.platform,
        "cloud": ON_CLOUD,
        "api_base": api.api_base,
        "api_host": host,
        "method": "POST",
        "request_header_names": api.request_header_names(),
        "token_source": api._token_source,
        "token_present": bool(api.token),
        "token_len": len(api.token),
        "token_fp16": short_fingerprint(api.token),
        "proxy": proxy_config_summary(),
        "dns": resolve_api_host(host),
        "redirect_policy": "denied_for_authenticated_api",
        "cookie_jar_count": len(list(api._jar)),
    }
    auth = api.diagnose_auth_flow()
    report = {
        **base,
        "auth_flow": auth,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    print("DIAGNOSE " + json.dumps(report, ensure_ascii=False), flush=True)
    if auth.get("ok"):
        print("DIAGNOSE_AUTH ok=True", flush=True)
        return 0
    failed = auth.get("failed_at")
    steps = auth.get("steps") or []
    last = steps[-1] if steps else {}
    print(
        f"DIAGNOSE_AUTH ok=False failed_at={failed} "
        f"category={last.get('category')} status={last.get('status')} "
        f"data_bool={last.get('data_bool')}",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    if os.environ.get("WEBTOP_DIAGNOSTICS", "").strip().lower() in ("0", "false", "no", "off"):
        print("Set WEBTOP_DIAGNOSTICS=1 for verbose server-side logs during updates.", flush=True)
    raise SystemExit(main())
