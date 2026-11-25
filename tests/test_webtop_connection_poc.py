# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from webtop_api import ErrorCategory, WebtopApiError
from webtop_connection import (
    OFFICIAL_OAUTH_AVAILABLE,
    connect_info,
    disconnect_runtime,
    get_connection_status,
    run_auth_poc,
)
from session_store import FileSessionStore, SupabaseSessionStore


def test_official_oauth_unavailable():
    assert OFFICIAL_OAUTH_AVAILABLE is False
    info = connect_info()
    assert info["official_oauth_available"] is False
    assert info["collect_password_in_app"] is False
    assert info["login_url"].startswith("https://webtop.smartschool.co.il")


def test_poc_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBTOP_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    store = FileSessionStore(tmp_path)
    with patch("webtop_connection.default_session_store", return_value=store):
        with patch("webtop_connection.TokenManager") as TM:
            inst = TM.return_value
            inst.load.return_value = ("", "none")
            result = run_auth_poc(live_probe=True)
    assert result["probe_ok"] is False
    assert result["status"] == "needs_login"
    assert result["acceptance_gate"]["dynamic_auth_without_WEBTOP_TOKEN"] is False


def test_poc_classifies_external_403(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = FileSessionStore(tmp_path)
    with patch("webtop_connection.default_session_store", return_value=store):
        with patch("webtop_connection.TokenManager") as TM:
            inst = TM.return_value
            inst.load.return_value = ("synthetic-token", "file")
            with patch("webtop_connection.WebtopApi") as API:
                api = API.return_value
                api.establish_session.side_effect = WebtopApiError(
                    "blocked",
                    category=ErrorCategory.EXTERNAL_ACCESS_REJECTED,
                    status=403,
                )
                result = run_auth_poc(live_probe=True)
    assert result["status"] == "access_rejected"
    assert result["error_category"] == "EXTERNAL_ACCESS_REJECTED"
    assert result["acceptance_gate"]["authorized_auth_from_render_verified"] is False


def test_poc_connected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = FileSessionStore(tmp_path)
    with patch("webtop_connection.default_session_store", return_value=store):
        with patch("webtop_connection.TokenManager") as TM:
            inst = TM.return_value
            inst.load.return_value = ("synthetic-token", "file")
            with patch("webtop_connection.WebtopApi") as API:
                API.return_value.establish_session.return_value = None
                result = run_auth_poc(live_probe=True)
    assert result["probe_ok"] is True
    assert result["status"] == "connected"


def test_status_and_disconnect(tmp_path):
    store = FileSessionStore(tmp_path)
    store.save_session_meta({"status": "connected", "message": "ok"})
    with patch("webtop_connection.default_session_store", return_value=store):
        status = get_connection_status()
        assert status["status_label"]
        out = disconnect_runtime()
        assert out["status"] == "needs_login"


def test_supabase_not_auto_provisioned():
    store = SupabaseSessionStore()
    assert store.configured() is False
