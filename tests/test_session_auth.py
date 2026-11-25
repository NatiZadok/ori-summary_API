# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
from email.message import Message
from http.cookiejar import CookieJar
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from webtop_api import ErrorCategory, TokenManager, WebtopApi, WebtopApiError


def _headers(**kwargs) -> Message:
    msg = Message()
    for key, value in kwargs.items():
        msg[key.replace("_", "-")] = value
    return msg


def _mock_response(status: int, body: str, set_cookie: str | None = None):
    resp = MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    headers = _headers(Content_Type="application/json")
    if set_cookie:
        headers["Set-Cookie"] = set_cookie

        def get_all(name):
            if name.lower() == "set-cookie":
                return [set_cookie]
            return None

        headers.get_all = get_all  # type: ignore[attr-defined]
    resp.headers = headers
    resp.read.return_value = body.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_check_token_data_false_is_not_success(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    body = json.dumps({"status": True, "data": False})
    with patch.object(api._opener, "open", return_value=_mock_response(200, body)):
        assert api.check_token() is False
    assert api._last_category == ErrorCategory.INVALID_TOKEN


def test_check_token_data_true_succeeds(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    body = json.dumps({"status": True, "data": True})
    with patch.object(api._opener, "open", return_value=_mock_response(200, body)):
        assert api.check_token() is True


def test_establish_session_requires_all_three(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    responses = [
        _mock_response(200, json.dumps({"status": True, "data": True})),
        _mock_response(200, json.dumps({"status": True, "data": True})),
        _mock_response(
            200,
            json.dumps(
                {
                    "status": True,
                    "data": {"childrens": [{"id": "1", "firstName": "A", "lastName": "B"}]},
                }
            ),
        ),
    ]

    with patch.object(api._opener, "open", side_effect=responses):
        api.establish_session()
    assert api.child is not None


def test_establish_session_stops_on_checktoken_false(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    with patch.object(
        api._opener,
        "open",
        return_value=_mock_response(200, json.dumps({"status": True, "data": False})),
    ):
        with pytest.raises(WebtopApiError) as exc:
            api.establish_session()
    assert exc.value.category == ErrorCategory.INVALID_TOKEN


def test_ensure_auth_no_renew_on_external_403(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBTOP_TOKEN", raising=False)
    tm = TokenManager(tmp_path)
    tm.save_verified("synthetic-token")
    tm.save_bio({"bioLogin": "x", "mode": "2"})
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    calls = []

    def fake_post(path, body=None):
        calls.append(path)
        raise WebtopApiError(
            "blocked",
            category=ErrorCategory.EXTERNAL_ACCESS_REJECTED,
            status=403,
            endpoint=path,
            diagnostics={"status": 403, "body_kind": "html"},
        )

    api.post = fake_post  # type: ignore[method-assign]
    with pytest.raises(WebtopApiError) as exc:
        api.ensure_auth()
    assert exc.value.category == ErrorCategory.EXTERNAL_ACCESS_REJECTED
    assert calls == ["dashboard/CheckToken"]
    assert "loginByBio" not in calls


def test_cookie_jar_used_and_manual_cookie_header_absent(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    assert isinstance(api._jar, CookieJar)
    assert "Cookie" not in api._headers()
    assert "Authorization" in api._headers()
    assert any(c.name == "webToken" for c in api._jar)


def test_set_cookie_rotates_session_token(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="old-token-value-old-token-value-old", token_manager=tm)
    new_token = "new-token-value-new-token-value-new"
    body = json.dumps({"status": True, "data": True})
    resp = _mock_response(200, body, set_cookie=f"webToken={new_token}; Path=/; Secure")

    # Simulate CookieJar ingesting Set-Cookie the way HTTPCookieProcessor would.
    def open_side_effect(req, timeout=None):
        # Manually set jar cookie as processor would after Set-Cookie
        from http.cookiejar import Cookie

        api._jar.set_cookie(
            Cookie(
                version=0,
                name="webToken",
                value=new_token,
                port=None,
                port_specified=False,
                domain="webtopserver.smartschool.co.il",
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
        return resp

    with patch.object(api._opener, "open", side_effect=open_side_effect):
        api.post("dashboard/CheckToken", {})
    assert api.token == new_token


def test_init_dashboard_401_classified(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    err = HTTPError(
        "https://webtopserver.smartschool.co.il/server/api/dashboard/InitDashboard",
        401,
        "Unauthorized",
        _headers(Content_Type=""),
        io.BytesIO(b""),
    )
    with patch.object(api._opener, "open", side_effect=err):
        with pytest.raises(WebtopApiError) as exc:
            api.post("dashboard/InitDashboard", {})
    assert exc.value.category == ErrorCategory.AUTHENTICATION_REQUIRED
    assert exc.value.status == 401
