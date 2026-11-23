# -*- coding: utf-8 -*-
"""Webtop SmartSchool HTTP API client (no Playwright / no browser scrape)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "webtop_state.json"
TOKEN_FILE = ROOT / "webtop_token.txt"
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
    """Prefer WEBTOP_TOKEN; else WEBTOP_STATE_B64 / webtop_state.json cookie webToken."""
    import base64

    tok = os.environ.get("WEBTOP_TOKEN", "").strip()
    if tok:
        TOKEN_FILE.write_text(tok, encoding="utf-8")
        print("BOOTSTRAP token from WEBTOP_TOKEN", flush=True)
        return

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


bootstrap_token_from_env()


def extract_web_token() -> str:
    env = (os.environ.get("WEBTOP_TOKEN") or CONFIG.get("token") or "").strip()
    if env:
        return unquote(env) if "%" in env else env
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return unquote(t) if "%" in t else t
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
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    print("TOKEN saved webtop_token.txt", flush=True)


class WebtopApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class WebtopApi:
    def __init__(self, token: str | None = None, *, api_base: str | None = None):
        self.token = (token or extract_web_token()).strip()
        self.api_base = (api_base or CONFIG.get("api_base") or API_BASE).rstrip("/")
        self.child: dict[str, Any] | None = None
        self.study_year: int | None = None

    def _headers(self) -> dict[str, str]:
        return {
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
            "Cookie": f"webToken={self.token}",
            "Authorization": f"Bearer {self.token}",
        }

    def post(self, path: str, body: Any = None) -> Any:
        url = path if path.startswith("http") else f"{self.api_base}/{path.lstrip('/')}"
        payload = {} if body is None else body
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=raw, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=45) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise WebtopApiError(f"{path} failed: {exc}") from exc
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise WebtopApiError(f"{path} non-JSON response") from exc
        if isinstance(data, dict) and "status" in data:
            if data.get("status") is not True:
                raise WebtopApiError(
                    data.get("errorDescription")
                    or data.get("message")
                    or f"{path} status=false",
                    payload=data,
                )
            return data.get("data")
        return data

    def check_token(self) -> bool:
        try:
            self.post("dashboard/CheckToken", {})
            return True
        except WebtopApiError:
            return False

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
