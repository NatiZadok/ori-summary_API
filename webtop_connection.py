# -*- coding: utf-8 -*-
"""Webtop connection status + Render auth PoC.

Official Israeli Webtop does not publish a third-party OAuth/app-authorize flow
(webtop.zendesk.com is end-user help only). This module:

- Does NOT collect or store Webtop usernames/passwords.
- Prefers authentication on the official website in the user's browser.
- Runs a minimal proof-of-concept session probe using the existing client.
- Keeps the legacy TokenManager / WEBTOP_TOKEN path until a verified replacement exists.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from session_store import (
    EnvLegacyHintStore,
    SupabaseSessionStore,
    default_session_store,
)
from webtop_api import (
    ON_CLOUD,
    ErrorCategory,
    TokenManager,
    WebtopApi,
    WebtopApiError,
)

OFFICIAL_WEBTOP_LOGIN = "https://webtop.smartschool.co.il"
OFFICIAL_OAUTH_AVAILABLE = False  # No published partner OAuth for IL Webtop

STATUS_LABELS = {
    "connected": "מחובר ל-Webtop",
    "needs_login": "נדרשת התחברות",
    "expired": "ההתחברות פגה",
    "reconnecting": "מתחבר מחדש",
    "access_rejected": "גישה נדחתה",
    "updated": "עודכן לאחרונה",
    "unknown": "מצב לא ידוע",
    "poc_blocked": "אין אימות צד־שלישי נתמך",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connection_capabilities() -> dict[str, Any]:
    supabase = SupabaseSessionStore()
    return {
        "official_login_url": OFFICIAL_WEBTOP_LOGIN,
        "official_oauth_available": OFFICIAL_OAUTH_AVAILABLE,
        "password_collection_allowed": False,
        "cloud": ON_CLOUD,
        "legacy_env_path_enabled": True,  # keep until replacement verified
        "storage": {
            "file_store_durable": default_session_store().durable(),
            "file_store_backend": default_session_store().backend_name(),
            "render_env_durable": True,
            "supabase_optional_configured": supabase.configured(),
            "note": (
                "Render Free filesystem is ephemeral. "
                "Do not treat webtop_token.txt in the container as permanent storage."
            ),
        },
        "poc": {
            "goal": "Prove authorized Webtop session validation works from Render egress",
            "interactive_captcha_on_server": False,
            "requires_user_browser_for_login": True,
        },
    }


def _status_from_category(category: ErrorCategory | None, *, had_token: bool) -> str:
    if category is None:
        return "connected"
    if category == ErrorCategory.EXTERNAL_ACCESS_REJECTED:
        return "access_rejected"
    if category in (
        ErrorCategory.INVALID_TOKEN,
        ErrorCategory.CONFIRMED_EXPIRED_TOKEN,
        ErrorCategory.AUTHENTICATION_REQUIRED,
    ):
        return "expired" if had_token else "needs_login"
    if category == ErrorCategory.MISSING_TOKEN:
        return "needs_login"
    return "unknown"


def run_auth_poc(*, live_probe: bool = True) -> dict[str, Any]:
    """
    Minimal proof of concept:
    1) Confirm no official OAuth.
    2) Optionally probe existing session via establish_session() (same client as production).
    3) Classify 401-class vs 403-class failures without logging secrets.
    """
    caps = connection_capabilities()
    store = default_session_store()
    legacy = EnvLegacyHintStore().load_session_meta()
    tm = TokenManager()
    token, source = tm.load()
    result: dict[str, Any] = {
        "poc": True,
        "at": _now_iso(),
        "cloud": ON_CLOUD,
        "official_oauth_available": OFFICIAL_OAUTH_AVAILABLE,
        "legacy_env": legacy,
        "token_present": bool(token),
        "token_source": source if token else "none",
        "token_len": len(token),
        "live_probe": live_probe,
        "probe_ok": None,
        "status": "poc_blocked" if not OFFICIAL_OAUTH_AVAILABLE else "unknown",
        "status_label": STATUS_LABELS["poc_blocked"],
        "error_category": None,
        "http_status": None,
        "message": (
            "Webtop ישראל אינו מפרסם ממשק OAuth/אימות צד־שלישי נתמך. "
            "התחברות חייבת להתרחש באתר הרשמי (כולל CAPTCHA). "
            "לא נשמרות סיסמאות Webtop באפליקציה."
        ),
        "acceptance_gate": {
            "dynamic_auth_without_WEBTOP_TOKEN": False,
            "authorized_auth_from_render_verified": False,
            "reason": "PoC not yet green on Render",
        },
        "capabilities": caps,
    }

    if not live_probe:
        store.save_session_meta(
            {
                "status": result["status"],
                "last_check_at": result["at"],
                "last_error_category": None,
                "cloud": ON_CLOUD,
                "poc_result": "skipped_live_probe",
                "official_oauth_available": False,
                "message": result["message"],
            }
        )
        return result

    if not token:
        result["status"] = "needs_login"
        result["status_label"] = STATUS_LABELS["needs_login"]
        result["probe_ok"] = False
        result["error_category"] = ErrorCategory.MISSING_TOKEN.value
        result["message"] += " אין סשן זמין לבדיקה."
        result["acceptance_gate"]["reason"] = "No session material available for probe"
        store.save_session_meta(
            {
                "status": result["status"],
                "last_check_at": result["at"],
                "last_error_category": result["error_category"],
                "cloud": ON_CLOUD,
                "poc_result": "missing_token",
                "official_oauth_available": False,
                "message": result["message"],
            }
        )
        return result

    api = WebtopApi(token=token, token_manager=tm)
    try:
        api.establish_session()
        result["probe_ok"] = True
        result["status"] = "connected"
        result["status_label"] = STATUS_LABELS["connected"]
        result["message"] = "בדיקת סשן הצליחה (CheckToken + CheckBackgroundToken + InitDashboard)."
        if ON_CLOUD:
            result["acceptance_gate"]["authorized_auth_from_render_verified"] = True
            result["acceptance_gate"]["reason"] = "Cloud probe succeeded"
            # Still false: dynamic acquisition without env/file token was not demonstrated.
            result["acceptance_gate"]["dynamic_auth_without_WEBTOP_TOKEN"] = False
        store.save_session_meta(
            {
                "status": "connected",
                "last_check_at": result["at"],
                "last_success_at": result["at"],
                "last_error_category": None,
                "cloud": ON_CLOUD,
                "poc_result": "session_ok",
                "official_oauth_available": False,
                "message": result["message"],
            }
        )
        return result
    except WebtopApiError as exc:
        status = _status_from_category(exc.category, had_token=True)
        result["probe_ok"] = False
        result["status"] = status
        result["status_label"] = STATUS_LABELS.get(status, STATUS_LABELS["unknown"])
        result["error_category"] = exc.category.value
        result["http_status"] = exc.status
        if exc.category == ErrorCategory.EXTERNAL_ACCESS_REJECTED:
            result["message"] = (
                "Webtop דחה את הגישה מהשרת (HTTP 403 / CloudFront HTML). "
                "זה שונה מטוקן לא תקף (401). לא מבוצעים ניסיונות אימות חוזרים."
            )
            result["acceptance_gate"]["reason"] = (
                "EXTERNAL_ACCESS_REJECTED from cloud egress — cannot complete dynamic auth on Render"
            )
        else:
            result["message"] = exc.user_message()
            result["acceptance_gate"]["reason"] = f"Session probe failed: {exc.category.value}"
        store.save_session_meta(
            {
                "status": status,
                "last_check_at": result["at"],
                "last_error_category": exc.category.value,
                "cloud": ON_CLOUD,
                "poc_result": "session_failed",
                "official_oauth_available": False,
                "message": result["message"],
            }
        )
        return result


def get_connection_status() -> dict[str, Any]:
    """Safe status for UI — no tokens/secrets."""
    store = default_session_store()
    meta = store.load_session_meta()
    legacy = EnvLegacyHintStore().load_session_meta()
    caps = connection_capabilities()
    status = meta.get("status") or (
        "needs_login"
        if not (legacy.get("legacy_env_token_present") or legacy.get("legacy_env_bio_present"))
        else "unknown"
    )
    return {
        "status": status,
        "status_label": STATUS_LABELS.get(status, STATUS_LABELS["unknown"]),
        "last_check_at": meta.get("last_check_at"),
        "last_success_at": meta.get("last_success_at"),
        "last_error_category": meta.get("last_error_category"),
        "message": meta.get("message")
        or "לחצו «התחברות ל-Webtop» למידע. אימות מתבצע באתר הרשמי בלבד.",
        "official_login_url": OFFICIAL_WEBTOP_LOGIN,
        "official_oauth_available": OFFICIAL_OAUTH_AVAILABLE,
        "cloud": ON_CLOUD,
        "legacy_env_token_present": bool(legacy.get("legacy_env_token_present")),
        "legacy_env_bio_present": bool(legacy.get("legacy_env_bio_present")),
        "storage_durable": store.durable(),
        "buttons": {
            "connect": True,
            "reconnect": True,
            "disconnect": True,
            "update": True,
        },
        "capabilities": caps,
    }


def connect_info() -> dict[str, Any]:
    """Instructions for authorized connection — never asks for Webtop password in-app."""
    return {
        "ok": True,
        "official_oauth_available": OFFICIAL_OAUTH_AVAILABLE,
        "login_url": OFFICIAL_WEBTOP_LOGIN,
        "collect_password_in_app": False,
        "steps_he": [
            "התחברו באתר הרשמי של Webtop (כולל CAPTCHA אם נדרש).",
            "אין להזין סיסמת Webtop באפליקציית הסיכום.",
            "כרגע אין ממשק OAuth נתמך לחיבור אפליקציות צד־שלישי.",
            "במחשב מקומי: ניתן להשתמש ב«רענון טוקן» ללכידת סשן מורשית.",
            "ב־Render: קריאות API מכתובות ענן עלולות להידחות ב־HTTP 403 גם עם סשן תקף.",
        ],
        "cloud": ON_CLOUD,
        "next": "run_poc_probe",
    }


def disconnect_runtime() -> dict[str, Any]:
    """Clear local session meta only — does not wipe Render env secrets."""
    store = default_session_store()
    store.clear_session_meta()
    return {
        "ok": True,
        "status": "needs_login",
        "status_label": STATUS_LABELS["needs_login"],
        "message": (
            "נותק סטטוס החיבור המקומי. "
            "סודות Render Environment (אם קיימים) יש לנקות ידנית בלוח הבקרה."
        ),
    }
