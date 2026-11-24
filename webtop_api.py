# -*- coding: utf-8 -*-
"""Webtop SmartSchool HTTP API client (no Playwright / no browser scrape)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "webtop_state.json"
TOKEN_FILE = ROOT / "webtop_token.txt"
BIO_FILE = ROOT / "bio_credentials.json"
API_BASE = "https://webtopserver.smartschool.co.il/server/api"
WEB_ORIGIN = "https://webtop.smartschool.co.il"
ON_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("WEBTOP_CLOUD") == "1")


def _load_config() -> dict:
    cfg = {
        "username": os.environ.get("WEBTOP_USERNAME", ""),
        "password": os.environ.get("WEBTOP_PASSWORD", ""),
        "api_base": os.environ.get("WEBTOP_API_BASE", API_BASE),
        "token": os.environ.get("WEBTOP_TOKEN", "").strip(),
    }
    local = ROOT / "config.json"
    if local.exists():
        try:
            file_cfg = json.loads(local.read_text(encoding="utf-8"))
            for key in ("username", "password", "api_base", "token"):
                if not cfg.get(key) and file_cfg.get(key):
                    cfg[key] = file_cfg[key]
        except Exception:
            pass
    return cfg


CONFIG = _load_config()


def bootstrap_token_from_env() -> None:
    """Load token/bio/state from Render env into local files."""
    import base64

    bio = os.environ.get("WEBTOP_BIO_JSON", "").strip()
    if bio:
        try:
            data = json.loads(bio)
            if isinstance(data, dict) and data.get("bioLogin"):
                BIO_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print("BOOTSTRAP bio from WEBTOP_BIO_JSON", flush=True)
        except Exception as exc:
            print(f"BOOTSTRAP bio invalid: {exc}", flush=True)

    tok = _clean_token(os.environ.get("WEBTOP_TOKEN", ""))
    if tok:
        TOKEN_FILE.write_text(tok, encoding="utf-8")
        print("BOOTSTRAP token from WEBTOP_TOKEN", flush=True)

    b64 = os.environ.get("WEBTOP_STATE_B64", "").strip()
    raw = os.environ.get("WEBTOP_STATE_JSON", "").strip()
    try:
        if b64:
            data = json.loads(base64.b64decode(b64).decode("utf-8"))
            STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print("BOOTSTRAP wrote webtop_state.json from WEBTOP_STATE_B64", flush=True)
        elif raw:
            data = json.loads(raw)
            STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print("BOOTSTRAP wrote webtop_state.json from WEBTOP_STATE_JSON", flush=True)
    except Exception as exc:
        print(f"BOOTSTRAP state env invalid: {exc}", flush=True)


def _clean_token(value: str) -> str:
    t = (value or "").strip().strip('"').strip("'").replace("\r", "").replace("\n", "")
    if "%" in t:
        t = unquote(t)
    return t.strip()


bootstrap_token_from_env()


def extract_web_token() -> str:
    env = _clean_token(os.environ.get("WEBTOP_TOKEN") or CONFIG.get("token") or "")
    if env:
        return env
    if TOKEN_FILE.exists():
        t = _clean_token(TOKEN_FILE.read_text(encoding="utf-8"))
        if t:
            return t
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for c in data.get("cookies") or []:
                if c.get("name") == "webToken" and c.get("value"):
                    return c["value"]
        except Exception:
            pass
    raise RuntimeError(
        "חסר webToken. הגדירי WEBTOP_TOKEN, או העתיקי מ־webtop_state.json, "
        "או הריצי save_token_from_state.py אחרי התחברות בדפדפן."
    )


def save_token(token: str) -> None:
    TOKEN_FILE.write_text(_clean_token(token), encoding="utf-8")
    print("TOKEN saved webtop_token.txt", flush=True)


def load_bio() -> dict | None:
    raw = os.environ.get("WEBTOP_BIO_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("bioLogin"):
                return data
        except Exception:
            pass
    if BIO_FILE.exists():
        try:
            data = json.loads(BIO_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("bioLogin"):
                return data
        except Exception:
            pass
    return None


def save_bio(data: dict) -> None:
    BIO_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BIO saved bio_credentials.json", flush=True)


class WebtopApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _header_get(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get(name)
        if value:
            return str(value)
        value = headers.get(name.lower())
        if value:
            return str(value)
    except Exception:
        pass
    return ""


def _body_kind(content_type: str, text: str) -> str:
    ct = (content_type or "").lower()
    if "json" in ct:
        return "json"
    if "html" in ct:
        return "html"
    stripped = (text or "").lstrip()[:32].lower()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if stripped.startswith("<!doctype") or stripped.startswith("<html"):
        return "html"
    if not stripped:
        return "empty"
    return "other"


def _token_fingerprint(token: str) -> str:
    """SHA-256 of token value — comparable across hosts, not reversible to secret."""
    if not token:
        return "(empty)"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _env_configured(*names: str) -> bool:
    for name in names:
        if (os.environ.get(name) or "").strip():
            return True
    return False


def _proxy_flags() -> dict[str, bool]:
    """Whether proxy-related env vars are set — never logs URLs or credentials."""
    return {
        "http_proxy": _env_configured("HTTP_PROXY", "http_proxy"),
        "https_proxy": _env_configured("HTTPS_PROXY", "https_proxy"),
        "all_proxy": _env_configured("ALL_PROXY", "all_proxy"),
        "no_proxy": _env_configured("NO_PROXY", "no_proxy"),
    }


def _log_connectivity_diagnostics(*, api_base: str, token: str) -> None:
    """Compare local vs Render runtime — no secrets, no auth/header changes."""
    proxies = _proxy_flags()
    print(
        "CONNECTIVITY diag "
        f"python={sys.version.split()[0]!r} "
        f"openssl={ssl.OPENSSL_VERSION!r} "
        f"api_base={api_base!r} "
        f"http_proxy_configured={proxies['http_proxy']} "
        f"https_proxy_configured={proxies['https_proxy']} "
        f"all_proxy_configured={proxies['all_proxy']} "
        f"no_proxy_configured={proxies['no_proxy']} "
        f"token_sha256={_token_fingerprint(token)} "
        f"token_len={len(token)} "
        f"cloud={ON_CLOUD} "
        f"platform={sys.platform!r}",
        flush=True,
    )


def _log_http_diagnostics(
    endpoint: str,
    status: int | None,
    headers: Any,
    body_text: str = "",
) -> None:
    """Safe HTTP diagnostics — never logs body/token/cookie contents."""
    content_type = _header_get(headers, "Content-Type")
    print(
        "HTTP diag "
        f"endpoint={endpoint} "
        f"status={status} "
        f"server={_header_get(headers, 'Server')!r} "
        f"via={_header_get(headers, 'Via')!r} "
        f"x-cache={_header_get(headers, 'X-Cache')!r} "
        f"x-amz-cf-id={_header_get(headers, 'X-Amz-Cf-Id')!r} "
        f"x-amz-cf-pop={_header_get(headers, 'X-Amz-Cf-Pop')!r} "
        f"age={_header_get(headers, 'Age')!r} "
        f"cf-ray={_header_get(headers, 'CF-RAY')!r} "
        f"cf-mitigated={_header_get(headers, 'CF-Mitigated')!r} "
        f"content-type={content_type!r} "
        f"body_kind={_body_kind(content_type, body_text)}",
        flush=True,
    )


class WebtopApi:
    def __init__(self, token: str | None = None, *, api_base: str | None = None):
        if token is not None:
            self.token = _clean_token(token)
        else:
            try:
                self.token = extract_web_token()
            except RuntimeError:
                self.token = ""
        self.api_base = (api_base or CONFIG.get("api_base") or API_BASE).rstrip("/")
        self.child: dict[str, Any] | None = None
        self.study_year: int | None = None
        self._last_auth_error = ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/plain, */*",
            "language": "he",
            "Origin": WEB_ORIGIN,
            "Referer": WEB_ORIGIN + "/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if self.token:
            headers["Cookie"] = f"webToken={self.token}"
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def post(self, path: str, body: Any = None) -> Any:
        endpoint = path if path.startswith("http") else path.lstrip("/")
        url = path if path.startswith("http") else f"{self.api_base}/{endpoint}"
        payload = {} if body is None else body
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=raw, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=45) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", None) or resp.getcode()
                _log_http_diagnostics(endpoint, status, resp.headers, text)
        except HTTPError as exc:
            err_text = ""
            try:
                err_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            _log_http_diagnostics(endpoint, exc.code, exc.headers, err_text)
            raise WebtopApiError(
                f"{endpoint} HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise WebtopApiError(f"{endpoint} network error: {exc.reason}") from exc
        except Exception as exc:
            raise WebtopApiError(f"{endpoint} failed: {exc}") from exc
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise WebtopApiError(
                f"{endpoint} non-JSON response (body_kind={_body_kind('', text)})",
            ) from exc
        if isinstance(data, dict) and "status" in data:
            if data.get("status") is not True:
                raise WebtopApiError(
                    data.get("errorDescription")
                    or data.get("message")
                    or f"{endpoint} status=false",
                    payload=data,
                )
            return data.get("data")
        return data

    def check_token(self) -> bool:
        if not self.token:
            print("AUTH CheckToken succeeded=False reason=no_token", flush=True)
            return False
        try:
            self.post("dashboard/CheckToken", {})
            print("AUTH CheckToken succeeded=True", flush=True)
            return True
        except WebtopApiError as exc:
            print(
                f"AUTH CheckToken succeeded=False status={exc.status} detail={exc}",
                flush=True,
            )
            self._last_auth_error = str(exc)
            return False

    def renew_via_bio(self) -> bool:
        """Mint a fresh webToken from bioLogin — no CAPTCHA."""
        bio = load_bio()
        if not bio:
            print("AUTH token_renewal succeeded=False reason=no_bio", flush=True)
            return False
        body = {
            "id": bio["bioLogin"],
            "param1": str(bio.get("mode", "2")),
            "param2": bio.get("deviceId") or "",
            "param3": bio.get("selectedUser") or "",
            "param4": bio.get("uniqueId") or "",
            "param5": str(bool(bio.get("isMobile", True))).lower(),
        }
        try:
            data = self.post("user/loginByBio", body)
        except WebtopApiError as exc:
            print(
                f"AUTH token_renewal succeeded=False status={exc.status} detail={exc}",
                flush=True,
            )
            self._last_auth_error = str(exc)
            return False
        if not isinstance(data, dict):
            print("AUTH token_renewal succeeded=False reason=non_dict", flush=True)
            return False
        token = data.get("token") or data.get("Token")
        if not token:
            print(
                f"AUTH token_renewal succeeded=False reason=no_token_field "
                f"keys={sorted(str(k) for k in data)}",
                flush=True,
            )
            return False
        self.token = _clean_token(token)
        save_token(self.token)
        ok = self.check_token()
        print(f"AUTH token_renewal succeeded={ok}", flush=True)
        return ok

    def ensure_auth(self) -> None:
        """Prefer live token; otherwise renew automatically via bio."""
        self._last_auth_error = ""
        has_token = bool(self.token)
        has_bio = bool(load_bio())
        _log_connectivity_diagnostics(api_base=self.api_base, token=self.token)
        print(
            f"AUTH start has_token={has_token} token_len={len(self.token)} "
            f"has_bio={has_bio} cloud={ON_CLOUD}",
            flush=True,
        )
        if self.check_token():
            try:
                self.post("dashboard/CheckBackgroundToken", {})
            except WebtopApiError:
                pass
            return
        # Fallback probe — sometimes CheckToken is flaky but dashboard works
        try:
            self.init_dashboard()
            print("AUTH CheckToken failed but InitDashboard OK — continuing", flush=True)
            return
        except WebtopApiError as exc:
            self._last_auth_error = self._last_auth_error or str(exc)

        print("AUTH token missing/expired — trying bio auto-renew…", flush=True)
        if self.renew_via_bio():
            return
        detail = self._last_auth_error or "סיבה לא ידועה"
        if not has_token and not has_bio:
            raise WebtopApiError(
                "ב־Render חסר WEBTOP_TOKEN (או WEBTOP_BIO_JSON). "
                "העתיקי מ־webtop_token.txt במחשב ל־Environment → WEBTOP_TOKEN → Save."
            )
        raise WebtopApiError(
            "Webtop דחה את הבקשה מהשרת. הסיבה לא נקבעה אוטומטית — "
            "בדקי בלוגי Render שורות HTTP diag "
            "(endpoint, status, server, cf-ray, cf-mitigated, content-type, body_kind) "
            "ו־AUTH CheckToken / token_renewal. "
            f"has_token={has_token} token_len={len(self.token)} has_bio={has_bio} "
            f"detail={detail}"
        )

    def init_dashboard(self) -> dict:
        return self.post("dashboard/InitDashboard", {}) or {}

    def ensure_child(self) -> dict:
        if self.child:
            return self.child
        dash = self.init_dashboard()
        kids = dash.get("childrens") or dash.get("children") or []
        if not kids:
            raise WebtopApiError("לא נמצא תלמיד ב־InitDashboard")
        self.child = kids[0]
        return self.child

    def pupil_settings(self) -> dict:
        data = self.post("PupilCard/GetSettingsList", {}) or {}
        year = data.get("currentStudyYear")
        if isinstance(year, int):
            self.study_year = year
        return data

    def menu_counters(self) -> dict:
        return self.post("Menu/GetMenuCounters", {}) or {}

    def unread_notifications_preview(self) -> dict:
        return self.post("Menu/GetPreviewUnreadNotifications", {}) or {}

    def messages_inbox(self, page: int = 1) -> list:
        data = self.post(
            "messageBox/GetMessagesInbox",
            {
                "PageId": page,
                "LabelId": 0,
                "HasRead": None,
                "SearchQuery": "",
            },
        )
        return data if isinstance(data, list) else []

    def discipline_events(self) -> dict:
        child = self.ensure_child()
        return (
            self.post(
                "dashboard/GetPupilDiciplineEvents",
                {"id": child["id"], "ClassCode": child.get("classCode")},
            )
            or {}
        )

    def lessons_and_homework(self, *, week_index: int = 0) -> list:
        child = self.ensure_child()
        settings = self.pupil_settings()
        year = self.study_year or settings.get("currentStudyYear") or 2026
        data = self.post(
            "PupilCard/GetPupilLessonsAndHomework",
            {
                "studentID": child["id"],
                "classCode": child.get("classCode"),
                "classNum": child.get("classNum"),
                "studyYear": year,
                "weekIndex": week_index,
                "moduleID": 11,
                "viewType": 0,
            },
        )
        return data if isinstance(data, list) else []


_NO_HW = frozenset(
    v.casefold()
    for v in (
        "אין",
        "אין שיעורי בית",
        "אין שעורי בית",
        "לא הוזן",
        "ללא",
        "ללא שיעורי בית",
        "none",
        "n/a",
        "-",
        "--",
        ".",
        "",
    )
)


def is_no_homework(text: str) -> bool:
    t = " ".join((text or "").split()).strip().strip(".!:;־-").casefold()
    return t in _NO_HW


def parse_iso_date(value: str) -> str:
    """Return dd/mm or dd/mm/yyyy-ish short form for UI."""
    if not value:
        return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return f"{m.group(3)}/{m.group(2)}"
    m2 = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if m2:
        return f"{m2.group(1)}/{m2.group(2)}"
    return value


def extract_homework_from_weeks(weeks: list[list]) -> list[dict]:
    """Flatten PupilCard week payloads into {date, subject, text}."""
    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for days in weeks:
        for day in days or []:
            if not isinstance(day, dict):
                continue
            date = parse_iso_date(day.get("date") or "")
            for hour in day.get("hoursData") or []:
                if not isinstance(hour, dict):
                    continue
                for entry in hour.get("scheduale") or []:
                    if not isinstance(entry, dict):
                        continue
                    text = (entry.get("homeWork") or "").strip()
                    if is_no_homework(text):
                        continue
                    subject = (entry.get("subject_name") or "אחר").strip()
                    key = (date, subject, text)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"date": date, "subject": subject, "text": text})
    return items
