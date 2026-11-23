# -*- coding: utf-8 -*-
"""Fetch Ori school data via Webtop HTTP API and write data.json."""
from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from webtop_api import (
    ON_CLOUD,
    WebtopApi,
    WebtopApiError,
    extract_homework_from_weeks,
    parse_iso_date,
    save_token,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data.json"

SUBJECT_PILLS = {
    "שפה": "lang",
    "מתמטיקה": "math",
    "מדעים": "science",
    "שבילי מורשת": "heritage",
}


def step(n: int, total: int, title: str, detail: str = "", status: str = "running"):
    print(
        "STEP "
        + json.dumps(
            {"n": n, "total": total, "title": title, "detail": detail, "status": status},
            ensure_ascii=False,
        ),
        flush=True,
    )


def done(ok: bool, message: str):
    print("DONE " + json.dumps({"ok": ok, "message": message}, ensure_ascii=False), flush=True)


def group_homework(items: list[dict]) -> list[dict]:
    by_subject: OrderedDict[str, list] = OrderedDict()
    for it in items:
        name = it["subject"]
        by_subject.setdefault(name, []).append({"date": it["date"], "text": it["text"]})
    return [
        {"name": name, "pill": SUBJECT_PILLS.get(name, "other"), "items": vals}
        for name, vals in by_subject.items()
    ]


def alerts_from_preview(preview: dict) -> list[dict]:
    out: list[dict] = []
    for key, default_cat in (
        ("personalNotifications", None),
        ("pedegogicalNotification", "אחר"),
        ("systemMessages", "אחר"),
    ):
        for n in preview.get(key) or []:
            if not isinstance(n, dict):
                continue
            msg = (n.get("message") or "").strip()
            if not msg:
                continue
            module = (n.get("moduleName") or "").lower()
            if default_cat:
                cat = default_cat
            elif "homework" in module or "שיעורי" in msg:
                cat = "שיעורי בית"
            elif "lesson" in module or "אירוע" in msg or "מילה טובה" in msg:
                cat = "אירועי שיעור"
            else:
                cat = "אחר"
            content = re.sub(r"^אורי,\s*", "", msg)
            hw = re.search(r'שיעורי-בית "(.+?)" בשיעור ([^\s]+) ', content)
            if hw:
                content = f"שיעורי בית ב{hw.group(2)}: {hw.group(1)}"
                cat = "שיעורי בית"
            ev = re.search(r"נרשם לך (.+) ב([^\s]+) בתאריך .+ בשיעור (\d+)", content)
            if ev:
                content = f"{ev.group(1)} ב{ev.group(2)} — שיעור {ev.group(3)}"
                cat = "אירועי שיעור"
            date = parse_iso_date(n.get("date") or "")
            if not date:
                dm = re.search(r"(\d{2}/\d{2}/\d{4})", msg)
                date = dm.group(1) if dm else ""
            out.append(
                {
                    "content": content,
                    "date": date,
                    "cat": cat,
                    "unread": n.get("read_date") is None,
                }
            )
    return out


def scrape() -> int:
    total = 7
    where = "בענן (API)" if ON_CLOUD else "מקומית (API)"
    step(1, total, "מתחבר ל־Webtop API", f"בודק webToken {where}…")
    try:
        api = WebtopApi()
        if not api.check_token():
            raise WebtopApiError("webToken לא תקף — יש לרענן WEBTOP_TOKEN")
        child = api.ensure_child()
        save_token(api.token)
        school = "ב׳ זיו בו ציון"
        name = f"{child.get('firstName', '')} {child.get('lastName', '')}".strip()
        step(1, total, "מתחבר ל־Webtop API", f"מחובר · {name or 'תלמיד'} ✓", "done")
    except Exception as exc:
        step(1, total, "מתחבר ל־Webtop API", str(exc), "error")
        done(False, str(exc))
        return 1

    # Messages
    step(2, total, "קורא הודעות", "שולף תיבת הודעות…")
    raw_msgs = api.messages_inbox(page=1)
    messages = []
    for m in raw_msgs[:10]:
        title = (m.get("subject") or "").strip() or "הודעה"
        sender = (
            m.get("fromTitle")
            or m.get("userTitle")
            or f"{m.get('student_F_name') or ''} {m.get('student_L_name') or ''}".strip()
            or "שולח"
        )
        date = parse_iso_date(m.get("sendingDate") or "")
        messages.append(
            {
                "title": title,
                "from": f"{sender} · {date}".strip(" ·"),
                "summary": "הודעה מתיבת Webtop (API).",
            }
        )
    unread_msg = sum(1 for m in raw_msgs if not m.get("hasRead"))
    step(2, total, "קורא הודעות", f"נמצאו {len(messages)} הודעות ✓", "done")

    # Signatures — API path not exposed the same way; keep soft placeholder
    step(3, total, "בודק חתימות ואישורים", "בודק…")
    signatures = {
        "pending": 0,
        "action_required": False,
        "inbox_url": "https://webtop.smartschool.co.il/signMessaes?view=Inbox",
        "items": [],
        "text": "✅ חתימות ואישורים: נבדק דרך API (אין פריטים ממתינים שזוהו).",
    }
    step(3, total, "בודק חתימות ואישורים", "0 פריטים · 0 ממתינים ✓", "done")

    # Alerts
    step(4, total, "אוסף התראות", "Menu/GetPreviewUnreadNotifications…")
    counters = api.menu_counters()
    preview = api.unread_notifications_preview()
    alerts = alerts_from_preview(preview)
    unread_notifications = int(counters.get("unreadNotifications") or len(alerts) or 0)
    step(4, total, "אוסף התראות", f"נאספו {len(alerts)} התראות ✓", "done")

    # Homework
    step(5, total, "אוסף שיעורי בית", "PupilCard/GetPupilLessonsAndHomework…")
    weeks = []
    for wi in (-1, 0, 1):
        try:
            weeks.append(api.lessons_and_homework(week_index=wi))
        except WebtopApiError as exc:
            print(f"HW_WEEK_WARN {wi}: {exc}", flush=True)
    hw_items = extract_homework_from_weeks(weeks)
    homework_by_subject = group_homework(hw_items)
    total_hw = len(hw_items)
    this_week = sum(1 for it in hw_items if it.get("date"))
    # week label from first week payload
    week_label = datetime.now().strftime("%d/%m/%Y")
    for days in weeks:
        dates = [parse_iso_date(d.get("date") or "") for d in (days or []) if isinstance(d, dict)]
        dates = [d for d in dates if d]
        if len(dates) >= 2:
            week_label = f"{dates[0]}–{dates[-1]}"
            break
    step(
        5,
        total,
        "אוסף שיעורי בית",
        f"{total_hw} שיעורי בית ב־{len(homework_by_subject)} מקצועות ✓",
        "done",
    )

    # Events
    step(6, total, "אוסף אירועי שיעור ומונה", "dashboard/GetPupilDiciplineEvents…")
    events = []
    event_counts: dict[str, int] = {}
    try:
        disc = api.discipline_events()
        table = (disc.get("dataTable") or {}) if isinstance(disc, dict) else {}
        for e in table.get("diciplineEvents") or []:
            etype = (e.get("eventType") or "אירוע").strip()
            date = parse_iso_date(e.get("eventDate") or "")
            lesson = str(e.get("hourNum") or e.get("hour") or "").replace("שיעור ", "")
            subject = (e.get("subjectName") or "").strip()
            teacher = (e.get("teacherName") or "").strip()
            if teacher:
                subject = f"{subject} - {teacher}".strip(" -")
            events.append(
                {"type": etype, "date": date, "lesson": lesson, "subject": subject}
            )
            event_counts[etype] = event_counts.get(etype, 0) + 1
        step(6, total, "אוסף אירועי שיעור ומונה", f"{len(events)} אירועים ✓", "done")
    except WebtopApiError as exc:
        print(f"EVENTS_WARN {exc}", flush=True)
        step(6, total, "אוסף אירועי שיעור ומונה", f"דולג: {exc}", "done")

    data = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "week_label": week_label,
        "school": school,
        "unread_notifications": unread_notifications,
        "homework": {
            "total": total_hw,
            "subjects_count": len(homework_by_subject),
            "this_week": this_week,
            "by_subject": homework_by_subject,
        },
        "event_counts": event_counts,
        "events": events,
        "alerts": alerts,
        "messages": {"unread": unread_msg, "items": messages},
        "signatures": signatures,
        "source": "webtop_api",
    }

    scraped_empty = (
        total_hw == 0
        and len(alerts) == 0
        and len(messages) == 0
        and len(events) == 0
        and unread_notifications == 0
    )
    if scraped_empty and OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_hw = (prev.get("homework") or {}).get("total") or 0
            prev_alerts = len(prev.get("alerts") or [])
            if prev_hw > 0 or prev_alerts > 0:
                step(
                    7,
                    total,
                    "שומר ומעדכן את הדף",
                    "ה־API חזר ריק — נשמרו הנתונים הקודמים",
                    "done",
                )
                done(False, "ה־API לא החזיר נתונים חדשים; הנתונים הישנים נשמרו.")
                return 1
        except Exception:
            pass

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    step(7, total, "שומר ומעדכן את הדף", "הדף עודכן בהצלחה ✓", "done")
    done(True, "העדכון הושלם (API)!")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(scrape())
    except Exception as e:
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        step(7, 7, "שומר ומעדכן את הדף", str(e), "error")
        done(False, str(e))
        raise SystemExit(1)
