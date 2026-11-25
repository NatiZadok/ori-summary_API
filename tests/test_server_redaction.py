# -*- coding: utf-8 -*-
from __future__ import annotations

from server import _sanitize_for_browser, _sanitize_for_render_log, _sanitize_secret_text


def test_redacts_bearer_and_webtoken():
    text = "Authorization Bearer W_T_abcdefghijklmnop cookie webToken=secretvalue"
    out = _sanitize_secret_text(text)
    assert "secretvalue" not in out
    assert "abcdefghijklmnop" not in out
    assert "[REDACTED]" in out


def test_step_omits_student_detail_in_logs():
    line = 'STEP {"n":1,"total":7,"title":"x","detail":"שיעורי בית של אורי","status":"done"}'
    out = _sanitize_for_render_log(line)
    assert "אורי" not in out
    assert "title='x'" in out or 'title="x"' in out or "title=" in out


def test_browser_payload_redaction():
    payload = {"message": "Bearer W_T_abcdefghijklmnop failed", "detail": "HTTP diag token_sha256=abc"}
    out = _sanitize_for_browser(payload)
    assert "W_T_abcdefghijklmnop" not in out["message"]
    assert "token_sha256" not in out["detail"]
