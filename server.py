# -*- coding: utf-8 -*-
"""Family dashboard server — local + cloud (Render) updates via Webtop HTTP API.

Render Environment (מומלץ):
  APP_USER / APP_PASSWORD  — Basic Auth לדף
  WEBTOP_TOKEN             — ערך עוגיית webToken (מומלץ)
  WEBTOP_STATE_B64         — חלופה: Playwright storage עם cookie webToken
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
ON_RENDER = bool(os.environ.get("RENDER"))
PORT = int(os.environ.get("PORT", "8765"))
HOST = "0.0.0.0" if ON_RENDER else "127.0.0.1"
APP_USER = os.environ.get("APP_USER", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_update_lock = threading.Lock()

if (APP_USER and not APP_PASSWORD) or (APP_PASSWORD and not APP_USER):
    raise SystemExit("Set both APP_USER and APP_PASSWORD, or neither for local use.")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _authorized(self):
        if not APP_USER and not APP_PASSWORD:
            return True
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

    def do_GET(self):
        if not self._require_auth():
            return
        path = unquote(urlparse(self.path).path)

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

        if path == "/":
            self.path = "/סיכום-שבועי-אורי.html"
        elif path not in ("/סיכום-שבועי-אורי.html", "/data.json"):
            return self.send_error(404, "Not Found")
        return super().do_GET()

    def _open_signatures(self):
        subprocess.Popen(
            [sys.executable, str(ROOT / "open_signatures.py")],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._json(200, {"ok": True, "message": "פותח את Webtop במחשב המקומי."})

    def _stream_update(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(event: str, data: dict):
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        if not _update_lock.acquire(blocking=False):
            send("error", {"message": "עדכון כבר רץ כרגע…"})
            send("done", {"ok": False, "message": "busy"})
            return
        try:
            where = "בענן (Render)" if ON_RENDER else "מקומית"
            send("start", {"message": f"מתחיל עדכון מ־Webtop API {where}"})
            env = os.environ.copy()
            if ON_RENDER:
                env["WEBTOP_CLOUD"] = "1"
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
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    if line.startswith("STEP "):
                        send("step", json.loads(line[5:]))
                    elif line.startswith("DONE "):
                        got_done = True
                        send("done", json.loads(line[5:]))
                    else:
                        send("log", {"message": line})
                except (ValueError, BrokenPipeError, ConnectionResetError):
                    break
            proc.wait()
            # אל תדרסי הודעת DONE אמיתית (למשל סריקה ריקה) בהודעת Cloudflare גנרית
            if proc.returncode != 0 and not got_done:
                send(
                    "done",
                    {
                        "ok": False,
                        "message": (
                            f"העדכון נכשל (קוד {proc.returncode}). "
                            "בדקי ש־WEBTOP_TOKEN תקף (או WEBTOP_STATE_B64 עם webToken)."
                        ),
                    },
                )
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                send("error", {"message": str(exc)})
                send("done", {"ok": False, "message": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            _update_lock.release()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    print(f"Server listening on {HOST}:{PORT} ({'render' if ON_RENDER else 'local'})", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
