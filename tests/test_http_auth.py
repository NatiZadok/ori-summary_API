# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from webtop_api import ErrorCategory, TokenManager, WebtopApi, WebtopApiError, classify_http_failure


def _headers(**kwargs) -> Message:
    msg = Message()
    for key, value in kwargs.items():
        msg[key.replace("_", "-")] = value
    return msg


def _mock_response(status: int, body: str, **header_kwargs):
    resp = MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    resp.headers = _headers(**header_kwargs)
    resp.read.return_value = body.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_classify_cloudfront_html_403():
    cat = classify_http_failure(
        status=403,
        content_type="text/html",
        text="<!DOCTYPE html><html>error</html>",
        endpoint="dashboard/CheckToken",
    )
    assert cat == ErrorCategory.EXTERNAL_ACCESS_REJECTED


def test_classify_json_401():
    cat = classify_http_failure(
        status=401,
        content_type="application/json",
        text='{"status":false}',
        endpoint="dashboard/CheckToken",
    )
    assert cat == ErrorCategory.AUTHENTICATION_REQUIRED


def test_http_200_valid_schema(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBTOP_TOKEN", raising=False)
    tm = TokenManager(tmp_path)
    tm.save_verified("synthetic-token")
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    body = json.dumps({"status": True, "data": {"ok": True}})
    with patch.object(api._opener, "open", return_value=_mock_response(200, body, Content_Type="application/json")):
        data = api.post("dashboard/CheckToken", {})
    assert data == {"ok": True}


def test_http_200_unexpected_schema_not_auth_success(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    body = json.dumps({"hello": "world"})
    with patch.object(api._opener, "open", return_value=_mock_response(200, body, Content_Type="application/json")):
        with pytest.raises(WebtopApiError) as exc:
            api.post("dashboard/CheckToken", {})
    assert exc.value.category == ErrorCategory.UNEXPECTED_RESPONSE


def test_http_403_html_external_access(tmp_path):
    tm = TokenManager(tmp_path)
    api = WebtopApi(token="synthetic-token", token_manager=tm)
    from urllib.error import HTTPError

    err = HTTPError(
        "https://webtopserver.smartschool.co.il/server/api/dashboard/CheckToken",
        403,
        "Forbidden",
        _headers(Content_Type="text/html", Server="CloudFront", X_Cache="Error from cloudfront"),
        io.BytesIO(b"<!DOCTYPE html><html>denied</html>"),
    )
    with patch.object(api._opener, "open", side_effect=err):
        with pytest.raises(WebtopApiError) as exc:
            api.post("dashboard/CheckToken", {})
    assert exc.value.category == ErrorCategory.EXTERNAL_ACCESS_REJECTED
    assert exc.value.status == 403


def test_ensure_auth_does_not_renew_on_cloudfront_403(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBTOP_TOKEN", raising=False)
    tm = TokenManager(tmp_path)
    tm.save_verified("synthetic-token")
    bio = {"bioLogin": "x", "mode": "2", "deviceId": "d", "selectedUser": "u", "uniqueId": "i"}
    tm.save_bio(bio)
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
    assert "InitDashboard" not in calls


def test_ensure_auth_renews_only_for_auth_failure_with_bio(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBTOP_TOKEN", raising=False)
    tm = TokenManager(tmp_path)
    tm.save_verified("old-token")
    tm.save_bio({"bioLogin": "x", "mode": "2"})
    api = WebtopApi(token="old-token", token_manager=tm)

    with patch.object(
        api,
        "establish_session",
        side_effect=WebtopApiError(
            "data=false",
            category=ErrorCategory.INVALID_TOKEN,
            endpoint="dashboard/CheckToken",
        ),
    ):
        with patch.object(api, "renew_via_bio", return_value=True) as renew:
            api.ensure_auth()
            renew.assert_called_once()


def test_refuses_non_https_host(tmp_path):
    tm = TokenManager(tmp_path)
    with pytest.raises(WebtopApiError) as exc:
        WebtopApi(token="x", api_base="http://evil.example/api", token_manager=tm)
    assert exc.value.category == ErrorCategory.CONFIGURATION_ERROR
