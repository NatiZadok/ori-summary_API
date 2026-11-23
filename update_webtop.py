# -*- coding: utf-8 -*-
"""Fetch Webtop data for Ori and write data.json. Emits STEP lines for the UI."""
from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from webtop_auth import (
    CONFIG,
    ON_CLOUD,
    STATE_FILE,
    _session_probe,
    close_browser,
    ensure_logged_in,
    goto_authed,
    open_context,
    save_state,
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
    payload = {"n": n, "total": total, "title": title, "detail": detail, "status": status}
    print("STEP " + json.dumps(payload, ensure_ascii=False), flush=True)


def done(ok: bool, message: str):
    print("DONE " + json.dumps({"ok": ok, "message": message}, ensure_ascii=False), flush=True)


def parse_hw_from_alerts(alerts: list[dict]) -> list[dict]:
    by_subject: OrderedDict[str, list] = OrderedDict()
    seen = set()
    for a in alerts:
        if a.get("cat") != "שיעורי בית":
            continue
        content = a["content"]
        m = re.search(r"שיעורי בית ב([^:：]+)[:：]\s*(.+)", content)
        if not m:
            continue
        subject = m.group(1).strip()
        text = m.group(2).strip()
        date = (a.get("date") or "").split()[-1] if a.get("date") else ""
        date = re.sub(r"[()]", "", date.split("(")[0]).strip() if "/" in (a.get("date") or "") else date
        dm = re.search(r"(\d{2}/\d{2})", a.get("date") or "")
        date = dm.group(1) if dm else date
        key = (subject, text)
        if key in seen:
            continue
        seen.add(key)
        by_subject.setdefault(subject, []).append({"date": date, "text": text})
    result = []
    for name, items in by_subject.items():
        result.append({
            "name": name,
            "pill": SUBJECT_PILLS.get(name, "other"),
            "items": items,
        })
    return result


def summarize_message(title: str, body: str, signed: str = "") -> str:
    body = (body or "").strip()
    if body and body != "ללא תוכן":
        summary = body[:180] + ("…" if len(body) > 180 else "")
    else:
        summary = "ללא טקסט בגוף ההודעה."
    if signed:
        summary += f" {signed}"
    if title:
        return summary
    return summary


def scrape():
    total_steps = 7
    base = CONFIG["base_url"].rstrip("/")

    # עם סשן שמור — headless גם מקומית (מהיר, בלי חלון שנתקע). בלי סשן — headed ל־CAPTCHA.
    headed = (not ON_CLOUD) and (not STATE_FILE.exists())
    detail = (
        "מתחבר בענן (סשן שמור / headless)…"
        if ON_CLOUD
        else (
            "מתחבר עם סשן שמור…"
            if STATE_FILE.exists()
            else "מתחבר (אם יופיע CAPTCHA — לחצי בחלון ואז כניסה)…"
        )
    )
    step(1, total_steps, "מתחבר ל־Webtop", detail)
    with sync_playwright() as p:
        browser, context = open_context(p, headed=headed)
        page = context.new_page()
        ensure_logged_in(page, context, headed=headed)
        save_state(context)
        step(1, total_steps, "מתחבר ל־Webtop", "התחברות הצליחה ✓", "done")

        # Messages
        step(2, total_steps, "קורא הודעות", "נכנס לתיבת הודעות…")
        goto_authed(page, context, "/Messages?view=Inbox", headed=headed)
        try:
            page.get_by_role("button", name="סגירה").click(timeout=2000)
        except Exception:
            pass
        options = page.locator('[role="option"]')
        msg_count = options.count()
        messages = []
        for i in range(min(msg_count, 10)):
            opt = options.nth(i)
            label = opt.inner_text().replace("\n", " · ")
            opt.click()
            page.wait_for_timeout(800)
            panel = page.evaluate(
                """() => {
                const t = document.body.innerText;
                const i = t.indexOf('מאת:');
                return i >= 0 ? t.substring(i, i+1200) : '';
            }"""
            )
            lines = [ln.strip() for ln in panel.splitlines() if ln.strip()]
            title = ""
            from_line = ""
            body = ""
            signed = ""
            for ln in lines:
                if ln.startswith("מאת:"):
                    from_line = ln.replace("מאת:", "").strip()
                elif "נחתם" in ln:
                    signed = ln
                elif ln in ("ללא תוכן",) or ln.startswith("(.") or "KB" in ln:
                    continue
                elif not title and from_line and ln and "מנהל" not in ln and "אל:" not in ln and not re.match(r"\d{2}/\d{2}/\d{4}", ln):
                    if "docx" in ln.lower() or "pdf" in ln.lower():
                        continue
                    title = ln
                elif title and not body and ln not in (title,) and "docx" not in ln.lower() and "pdf" not in ln.lower():
                    if re.match(r"\d{2}/\d{2}/\d{4}", ln) or ln.startswith("שם התלמיד"):
                        continue
                    body = ln
            # fallback from option label
            if not title:
                parts = label.split("·")
                title = parts[-1].strip() if parts else label
            date_m = re.search(r"(\d{2}/\d{2}/\d{4})", label)
            from_short = from_line.split("(")[0].strip() if from_line else label.split(",")[0]
            date_s = date_m.group(1) if date_m else ""
            messages.append({
                "title": title,
                "from": f"{from_short} · {date_s}".strip(" ·"),
                "summary": summarize_message(title, body, signed),
            })
        unread_msg = 0
        try:
            badge = page.locator('button[aria-label*="תיבת הודעות"]').first.get_attribute("aria-label") or ""
            um = re.search(r"(\d+)", badge)
            unread_msg = int(um.group(1)) if um else 0
        except Exception:
            pass
        step(2, total_steps, "קורא הודעות", f"נמצאו {len(messages)} הודעות ✓", "done")

        # Signatures
        step(3, total_steps, "בודק חתימות ואישורים", "פותח חתימות…")
        inbox_url = f"{base}/signMessaes?view=Inbox"
        goto_authed(page, context, "/signMessaes?view=Inbox", headed=headed)
        sig_opts = page.locator('[role="option"]')
        sig_n = sig_opts.count()
        sig_items = []
        for i in range(min(sig_n, 10)):
            t = sig_opts.nth(i).inner_text().split("\n")
            title = next((x.strip() for x in reversed(t) if x.strip() and not re.match(r"\d{2}/\d{2}", x.strip())), t[-1] if t else sig_opts.nth(i).inner_text())
            sig_opts.nth(i).click()
            page.wait_for_timeout(700)
            signed_txt = page.evaluate(
                """() => {
                const t = document.body.innerText;
                const m = t.match(/נחתם ב[^\\n]*/);
                return m ? m[0] : '';
            }"""
            )
            link = page.url if "msgId=" in page.url else inbox_url
            needs_action = "נחתם" not in (signed_txt or "")
            sig_items.append({
                "title": title.strip(),
                "status": signed_txt or ("ממתין לחתימה" if needs_action else "סטטוס לא ידוע"),
                "needs_action": needs_action,
                "link": link,
            })
        pending = sum(1 for s in sig_items if s["needs_action"])
        action_required = pending > 0
        if sig_n == 0:
            sig_html = "✅ חתימות ואישורים: אין פריטים כרגע. אין פעולה נדרשת."
        elif not action_required:
            numbered = " · ".join(f"{i+1}) {s['title']} ({s['status']})" for i, s in enumerate(sig_items))
            sig_html = f"✅ אין פעולה נדרשת — כל החתימות הושלמו ({sig_n})<br />{numbered}"
        else:
            pending_list = " · ".join(s["title"] for s in sig_items if s["needs_action"])
            sig_html = f"⚠️ פעולה נדרשת: {pending} חתימות ממתינות — {pending_list}"
        signatures = {
            "pending": pending,
            "action_required": action_required,
            "inbox_url": inbox_url,
            "items": sig_items,
            "text": sig_html,
        }
        step(3, total_steps, "בודק חתימות ואישורים", f"{sig_n} פריטים · {pending} ממתינים ✓", "done")

        # Notifications (Webtop UI: role=listitem בתוך aria-label=טבלת התראות — לא <a>)
        step(4, total_steps, "אוסף התראות", "טוען את כל ההתראות…")
        goto_authed(page, context, "/notification", headed=headed)
        try:
            page.wait_for_selector('[aria-label="טבלת התראות"] [role="listitem"], [role="listitem"]', timeout=10000)
        except Exception:
            pass
        unread_notifications = 0
        try:
            raw = page.locator('a[href="/notification"]').inner_text()
            um = re.search(r"(\d+)", raw)
            unread_notifications = int(um.group(1)) if um else 0
        except Exception:
            pass
        if not unread_notifications:
            try:
                um = re.search(r"התראות\s*(\d+)", page.locator("body").inner_text()[:800])
                unread_notifications = int(um.group(1)) if um else 0
            except Exception:
                pass
        alerts = page.evaluate(
            """() => {
            const root = document.querySelector('[aria-label=\"טבלת התראות\"]');
            const items = root
              ? [...root.querySelectorAll('[role=\"listitem\"]')]
              : [...document.querySelectorAll('[role=\"listitem\"]')];
            return items.map(el => {
              const name = (el.getAttribute('aria-label') || el.innerText || '').trim();
              const unread = name.includes('לא נקראה');
              let cat = 'אחר';
              if (name.includes('נושאי שיעור ושיעורי-בית') || name.includes('שיעורי-בית')) cat = 'שיעורי בית';
              else if (name.includes('אירועי שיעור')) cat = 'אירועי שיעור';
              const dateMatch = name.match(/יום [^,\\n]+, \\d{2}\\/\\d{2}\\/\\d{4}(?: \\(\\d{2}:\\d{2}\\))?/);
              let content = name.replace(/^לא נקראה[,\\s]*/, '');
              content = content.split('\\n')[0] || content;
              content = content.replace(/, יום .*/, '').replace(/, אירועי שיעור.*/, '').replace(/, נושאי שיעור.*/, '');
              content = content.replace(/^אורי,\\s*/, '').trim();
              const hw = content.match(/שיעורי-בית \"(.+?)\" בשיעור ([^\\s]+) /);
              if (hw) content = `שיעורי בית ב${hw[2]}: ${hw[1]}`;
              const ev = content.match(/נרשם לך (.+) ב([^\\s]+) בתאריך .+ בשיעור (\\d+)/);
              if (ev) content = `${ev[1]} ב${ev[2]} — שיעור ${ev[3]}`;
              return { content, date: dateMatch ? dateMatch[0].replace('יום ', '') : '', cat, unread };
            }).filter(x => x.content);
        }"""
        )
        if not unread_notifications:
            unread_notifications = sum(1 for a in alerts if a.get("unread")) or len(alerts)
        step(4, total_steps, "אוסף התראות", f"נאספו {len(alerts)} התראות ✓", "done")

        # Homework from student card + alerts
        step(5, total_steps, "אוסף שיעורי בית", "נכנס לכרטיס תלמיד…")
        goto_authed(page, context, "/Student_Card/11", headed=headed)
        week_text = page.evaluate(
            """() => {
            const t = document.body.innerText;
            const m = t.match(/\\d{2}\\/\\d{2}\\/\\d{4}\\s*-\\s*\\d{2}\\/\\d{2}\\/\\d{4}/);
            return m ? m[0] : '';
        }"""
        )
        this_week_hw = page.evaluate(
            """() => {
            const text = document.querySelector('main')?.innerText || '';
            // Rough parse: lines after headers - collect subject+homework pairs from week tables is hard;
            // return empty and rely on alerts grouping; week count from rows containing homework after subject
            return text;
        }"""
        )
        homework_by_subject = parse_hw_from_alerts(alerts)
        # count this week items (dates in current week string)
        this_week = 0
        if week_text:
            # extract dd/mm from homework and check if in range - simple: count dates matching week days from alerts in week
            pass
        # From student card text, count non-empty homework cells this week via regex on known pattern
        # Fallback: count homework alerts with dates 15/11-20/11 if week matches
        for subj in homework_by_subject:
            for it in subj["items"]:
                d = it.get("date") or ""
                if d.startswith("15/") or d.startswith("16/") or d.startswith("17/") or d.startswith("18/") or d.startswith("19/") or d.startswith("20/"):
                    if "/11" in d:
                        this_week += 1
        total_hw = sum(len(s["items"]) for s in homework_by_subject)
        week_label = week_text.replace(" - ", "–") if week_text else datetime.now().strftime("%d/%m/%Y")
        step(5, total_steps, "אוסף שיעורי בית", f"{total_hw} שיעורי בית ב־{len(homework_by_subject)} מקצועות ✓", "done")

        # Events + counter (UI tabs may differ — never fail the whole update here)
        step(6, total_steps, "אוסף אירועי שיעור ומונה", "פותח מונה אירועים…")
        event_counts = {}
        events = []
        try:
            goto_authed(page, context, "/Student_Card/4", headed=headed)
            for tab_name in ("מונה אירועים", "מונה"):
                try:
                    page.get_by_role("tab", name=tab_name).click(timeout=5000)
                    page.wait_for_timeout(1000)
                    break
                except Exception:
                    try:
                        page.get_by_text(tab_name, exact=False).first.click(timeout=3000)
                        page.wait_for_timeout(1000)
                        break
                    except Exception:
                        continue
            event_counts = page.evaluate(
                """() => {
                const rows = [
                  ...document.querySelectorAll('table[aria-label*=\"מונה\"] tr, table tr, [role=\"row\"]')
                ];
                const out = {};
                rows.forEach(r => {
                  const cells = [...r.querySelectorAll('td, [role=\"cell\"], [role=\"gridcell\"]')]
                    .map(c => c.innerText.trim())
                    .filter(Boolean);
                  const parts = cells.length >= 2 ? cells : (r.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                  if (parts.length >= 2 && /\\d/.test(parts[1]) && parts[0] && parts[0].length < 40 && parts[0] !== 'סוג האירוע') {
                    const n = parseInt(parts[1], 10);
                    if (!isNaN(n)) out[parts[0]] = n;
                  }
                });
                return out;
            }"""
            )
            clicked_detail = False
            for tab_name in ("פירוט אירועים", "פירוט"):
                try:
                    page.get_by_role("tab", name=tab_name).click(timeout=5000)
                    clicked_detail = True
                    break
                except Exception:
                    try:
                        page.get_by_text(tab_name, exact=False).first.click(timeout=3000)
                        clicked_detail = True
                        break
                    except Exception:
                        continue
            if clicked_detail:
                page.wait_for_timeout(1500)
            events = page.evaluate(
                """() => {
                const rows = [
                  ...document.querySelectorAll('table[aria-label*=\"אירועי שיעור\"] tbody tr, table tbody tr, [role=\"row\"]')
                ];
                const out = [];
                rows.forEach(r => {
                  let cells = [...r.querySelectorAll('td, [role=\"cell\"], [role=\"gridcell\"]')]
                    .map(c => c.innerText.trim())
                    .filter(Boolean);
                  if (cells.length < 2) {
                    cells = (r.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                  }
                  if (cells[0] === 'סוג האירוע') return;
                  if (cells.length >= 4 && cells[0] && (cells[1] || '').includes('/')) {
                    out.push({
                      type: cells[0],
                      date: cells[1].replace('יום ', ''),
                      lesson: (cells[2] || '').replace('שיעור ', ''),
                      subject: cells[3],
                    });
                  }
                });
                return out;
            }"""
            )
            if not event_counts:
                for e in events:
                    event_counts[e["type"]] = event_counts.get(e["type"], 0) + 1
            step(6, total_steps, "אוסף אירועי שיעור ומונה", f"{len(events)} אירועים ✓", "done")
        except Exception as exc:
            print(f"EVENTS_WARN {exc}", flush=True)
            step(6, total_steps, "אוסף אירועי שיעור ומונה", f"דולג (UI השתנה): {exc}", "done")
            event_counts = event_counts or {}
            events = events or []

        school = "ב׳ זיו בו ציון"
        try:
            school = page.locator("text=הורה").first.inner_text()
            school = school.replace("הורה", "").strip()
        except Exception:
            pass

        data = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "week_label": week_label,
            "school": school or "ב׳ זיו בו ציון",
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
        }

        # אל תמחק נתונים טובים אם הסריקה בענן חזרה ריקה (UI/timeout)
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
                    print(f"EMPTY_SCRAPE keeping previous data.json | {_session_probe(page)}", flush=True)
                    try:
                        page.screenshot(path=str(ROOT / "empty_scrape_debug.png"), full_page=True)
                    except Exception:
                        pass
                    close_browser(browser, context)
                    step(
                        7,
                        total_steps,
                        "שומר ומעדכן את הדף",
                        "הסריקה חזרה ריקה — נשמרו הנתונים הקודמים",
                        "done",
                    )
                    done(
                        False,
                        "לא נמשכו נתונים מ־Webtop (דפים ריקים / סשן חלש). הנתונים הישנים נשמרו — התחברי מקומית עם CAPTCHA ורענני סשן.",
                    )
                    return 1
            except Exception:
                pass

        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        close_browser(browser, context)
        step(7, total_steps, "שומר ומעדכן את הדף", "הדף עודכן בהצלחה ✓", "done")
        done(True, "העדכון הושלם!")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(scrape())
    except Exception as e:
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        step(7, 7, "שומר ומעדכן את הדף", str(e), "error")
        done(False, str(e))
        raise SystemExit(1)
