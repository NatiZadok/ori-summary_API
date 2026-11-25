# -*- coding: utf-8 -*-
"""Family dashboard server — local + cloud (Render) updates via Webtop HTTP API.

Render Environment (מומלץ):
  APP_USER / APP_PASSWORD  — Basic Auth לדף (חובה בענן)
  WEBTOP_TOKEN             — ערך עוגיית webToken
  WEBTOP_BIO_JSON          — אופציונלי לחידוש bio
  WEBTOP_DIAGNOSTICS=1     — אבחון מורחב בלוגים בלבד
  WEBTOP_FORCE_ENV_TOKEN=1 — סיבוב מכוון: העדף WEBTOP_TOKEN על הקובץ
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from webtop_api import DIAGNOSTICS, ON_CLOUD, env_flag, git_revision

ROOT = Path(__file__).resolve().parent
ON_RENDER = bool(os.environ.get("RENDER")) or ON_CLOUD
PORT = int(os.environ.get("PORT", "8765"))
HOST = "0.0.0.0" if ON_RENDER else "127.0.0.1"
APP_USER = os.environ.get("APP_USER", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
UPDATE_TIMEOUT_SEC = int(os.environ.get("UPDATE_TIMEOUT_SEC", "180"))
_update_lock = threading.Lock()
ALLOWED_STATIC = frozenset({"/", "/סיכום-שבועי-אורי.html", "/data.json", "/healthz", "/api/health"})

_REDACT_PATTERNS = (
    (re.compile(r"(?i)(Bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(webToken=)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(W_T_)[A-Za-z0-9+/=_\-]{12,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r'(?i)("?(?:token|bioLogin|password|Authorization|Cookie)"?\s*[:=]\s*)"[^"]*"'
        ),
        r'\1"[REDACTED]"',
    ),
    (re.compile(r"(?i)\bprefix=[^\s]+"), "prefix=[REDACTED]"),
    (re.compile(r"(?i)\btoken_sha256=[0-9a-f]{16,}"), "token_sha256=[REDACTED_FP]"),
)


def _sanitize_secret_text(text: str) -> str:
    out = text
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _sanitize_for_render_log(line: str) -> str:
    """Safe line for Render logs — no tokens/cookies/credentials/student detail."""
    if line.startswith("STEP "):
        try:
            obj = json.loads(line[5:])
            return (
                f"STEP n={obj.get('n')}/{obj.get('total')} "
                f"title={obj.get('title')!r} status={obj.get('status')!r}"
            )
        except (TypeError, ValueError):
            return "STEP [unparsed]"
    if line.startswith("DONE "):
        try:
            obj = json.loads(line[5:])
            msg = _sanitize_secret_text(str(obj.get("message") or ""))
            return f"DONE ok={obj.get('ok')} message={msg}"
        except (TypeError, ValueError):
            return "DONE [unparsed]"
    return _sanitize_secret_text(line)


def _sanitize_for_browser(payload: dict) -> dict:
    """SSE payload visible in the browser — keep understandable, redact secrets."""
    out = dict(payload)
    if "message" in out:
        out["message"] = _sanitize_secret_text(str(out.get("message") or ""))
    if "detail" in out:
        # Avoid streaming student homework / raw exception dumps to the UI.
        detail = str(out.get("detail") or "")
        if any(marker in detail for marker in ("HTTP diag", "token_sha256", "W_T_", "Bearer ")):
            out["detail"] = "פרטים טכניים נשמרו בלוגי השרת בלבד."
        else:
            out["detail"] = _sanitize_secret_text(detail)[:240]
    return out


if (APP_USER and not APP_PASSWORD) or (APP_PASSWORD and not APP_USER):
    raise SystemExit("Set both APP_USER and APP_PASSWORD, or neither for local use.")
if ON_RENDER and (not APP_USER or not APP_PASSWORD):
    raise SystemExit("Cloud mode requires APP_USER and APP_PASSWORD (fail-closed).")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        # No wildcard CORS — same-origin dashboard only.
        super().end_headers()

    def _authorized(self):
        if not APP_USER and not APP_PASSWORD:
            return not ON_RENDER
        value = self.headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return False
        try:
            plain = base64.b64decode(value[6:], validate=True).decode("utf-8")
        except (UnicodeDecodeError, ValueError, binascii.Error):
            return False
        username, separator, password = plain.partition(":")
        return (
            bool(separator)
            and hmac.compare_digest(username, APP_USER)
            and hmac.compare_digest(password, APP_PASSWORD)
        )

    def _require_auth(self):
        if self._authorized():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Ori Family", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _health(self):
        self._json(
            200,
            {
                "ok": True,
                "service": "ori-summary-api",
                "git": git_revision(),
                "cloud": ON_RENDER,
                "diagnostics": DIAGNOSTICS,
            },
        )

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/healthz", "/api/health"):
            return self._health()

        if not self._require_auth():
            return

        if path == "/api/update":
            return self._stream_update()

        if path == "/api/open-signatures":
            if ON_RENDER:
                return self._json(
                    503,
                    {
                        "ok": False,
                        "message": "פתיחת חלון חתימות זמינה רק במחשב המקומי (דורש דפדפן).",
                    },
                )
            return self._open_signatures()

        if path == "/api/refresh-token":
            if ON_RENDER:
                return self._json(
                    503,
                    {
                        "ok": False,
                        "message": "רענון טוקן אפשרי רק במחשב הבית (Chrome + CAPTCHA).",
                    },
                )
            return self._refresh_token()

        if path == "/":
            self.path = "/סיכום-שבועי-אורי.html"
        elif path not in ("/סיכום-שבועי-אורי.html", "/data.json"):
            return self.send_error(404, "Not Found")
        return super().do_GET()

    def _open_signatures(self):
        subprocess.Popen(
            [sys.executable, str(ROOT / "get_webtop_token.py")],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._json(200, {"ok": True, "message": "פותח Chrome לרענון טוקן."})

    def _refresh_token(self):
        subprocess.Popen(
            [sys.executable, str(ROOT / "get_webtop_token.py")],
            cwd=str(ROOT),
        )
        self._json(
            200,
            {
                "ok": True,
                "message": (
                    "נפתח Chrome לרענון טוקן. אחרי כניסה הטוקן יועתק ללוח — "
                    "הדביקי ב־Render ל־WEBTOP_TOKEN."
                ),
            },
        )

    def _stream_update(self):
        corr = uuid.uuid4().hex[:12]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Update-Id", corr)
        self.end_headers()

        def send(event: str, data: dict):
            safe = _sanitize_for_browser(data)
            payload = f"event: {event}\ndata: {json.dumps(safe, ensure_ascii=False)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        if not _update_lock.acquire(blocking=False):
            send("error", {"message": "עדכון כבר רץ כרגע…"})
            send("done", {"ok": False, "message": "busy"})
            return
        proc = None
        try:
            where = "בענן (Render)" if ON_RENDER else "מקומית"
            print(
                f"[update] start corr={corr} git={git_revision()} cloud={ON_RENDER} where={where}",
                flush=True,
            )
            send("start", {"message": f"מתחיל עדכון מ־Webtop API {where}"})
            env = os.environ.copy()
            if ON_RENDER:
                env["WEBTOP_CLOUD"] = "1"
            if env_flag("WEBTOP_DIAGNOSTICS"):
                env["WEBTOP_DIAGNOSTICS"] = "1"
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "update_data.py")],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert proc.stdout is not None
            got_done = False
            deadline = time.monotonic() + UPDATE_TIMEOUT_SEC
            while True:
                if time.monotonic() > deadline:
                    proc.kill()
                    send("error", {"message": "תם הזמן לעדכון."})
                    send("done", {"ok": False, "message": "timeout"})
                    print(f"[update] corr={corr} timeout killed", flush=True)
                    break
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                line = line.strip()
                if not line:
                    continue
                print(f"[update] corr={corr} {_sanitize_for_render_log(line)}", flush=True)
                try:
                    if line.startswith("STEP "):
                        send("step", json.loads(line[5:]))
                    elif line.startswith("DONE "):
                        got_done = True
                        send("done", json.loads(line[5:]))
                    elif DIAGNOSTICS:
                        send("log", {"message": _sanitize_secret_text(line)[:300]})
                except (ValueError, BrokenPipeError, ConnectionResetError):
                    break
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            print(
                f"[update] corr={corr} subprocess exit_code={proc.returncode} got_done={got_done}",
                flush=True,
            )
            if proc.returncode not in (0, None) and not got_done:
                send(
                    "done",
                    {
                        "ok": False,
                        "message": (
                            f"העדכון נכשל (קוד {proc.returncode}). "
                            "בדקי לוגי Render לשורות CONNECTIVITY/HTTP diag."
                        ),
                    },
                )
        except (BrokenPipeError, ConnectionResetError):
            if proc and proc.poll() is None:
                proc.kill()
        except Exception as exc:
            try:
                send("error", {"message": _sanitize_secret_text(str(exc))})
                send("done", {"ok": False, "message": _sanitize_secret_text(str(exc))})
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            _update_lock.release()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    print(
        f"Server listening on {HOST}:{PORT} "
        f"({'render' if ON_RENDER else 'local'}) git={git_revision()}",
        flush=True,
    )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
