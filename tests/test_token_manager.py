# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from webtop_api import ErrorCategory, TokenManager, WebtopApiError, env_flag, normalize_token


def test_normalize_preserves_opaque_token():
    raw = "  W_T_abc+/=XYZ  "
    assert normalize_token(raw) == "W_T_abc+/=XYZ"
    assert normalize_token('"W_T_quoted"') == "W_T_quoted"


def test_env_flag_false_string(monkeypatch):
    monkeypatch.setenv("WEBTOP_CLOUD", "false")
    assert env_flag("WEBTOP_CLOUD") is False
    monkeypatch.setenv("WEBTOP_CLOUD", "1")
    assert env_flag("WEBTOP_CLOUD") is True


def test_token_precedence_file_over_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("WEBTOP_FORCE_ENV_TOKEN", raising=False)
    monkeypatch.setenv("WEBTOP_TOKEN", "env-token-value")
    tm = TokenManager(tmp_path)
    tm.token_file.write_text("file-token-value", encoding="utf-8")
    token, source = tm.load()
    assert token == "file-token-value"
    assert source == "file"


def test_token_force_env_rotation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WEBTOP_FORCE_ENV_TOKEN", "1")
    monkeypatch.setenv("WEBTOP_TOKEN", "fresh-env-token")
    tm = TokenManager(tmp_path)
    tm.token_file.write_text("stale-file-token", encoding="utf-8")
    token, source = tm.load()
    assert token == "fresh-env-token"
    assert source == "env_forced"


def test_save_verified_atomic(tmp_path: Path):
    tm = TokenManager(tmp_path)
    tm.save_verified("verified-token")
    assert tm.token_file.read_text(encoding="utf-8") == "verified-token"
    token, source = tm.load()
    assert token == "verified-token"
    assert source == "runtime"


def test_bootstrap_does_not_overwrite_file_without_force(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("WEBTOP_FORCE_ENV_TOKEN", raising=False)
    monkeypatch.setenv("WEBTOP_TOKEN", "env-should-not-overwrite")
    monkeypatch.delenv("WEBTOP_BIO_JSON", raising=False)
    monkeypatch.delenv("WEBTOP_STATE_B64", raising=False)
    monkeypatch.delenv("WEBTOP_STATE_JSON", raising=False)
    tm = TokenManager(tmp_path)
    tm.token_file.write_text("existing-file-token", encoding="utf-8")
    tm.bootstrap_optional_files()
    assert tm.token_file.read_text(encoding="utf-8") == "existing-file-token"


def test_missing_token_category(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("WEBTOP_TOKEN", raising=False)
    monkeypatch.delenv("WEBTOP_FORCE_ENV_TOKEN", raising=False)
    tm = TokenManager(tmp_path)
    token, source = tm.load()
    assert token == ""
    assert source == "none"
