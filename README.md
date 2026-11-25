# ori-summary_API — סיכום אורי דרך Webtop API

איסוף נתונים ב־HTTP API (בלי Playwright בעדכון עצמו).

## מצב נוכחי (חשוב)

מקומית (Windows) העדכון עובד עם טוקן תקף.  
מ־Render וספקי ענן אחרים, Webtop/CloudFront החזיר היסטורית **HTTP 403 HTML** גם עם אותו טוקן.

ענף `fix/webtop-403-phase1-2` מתקן פגמי יישום (טוקן, סיווג שגיאות, אבחון, אבטחת שרת) ומסווג דחייה כ־`EXTERNAL_ACCESS_REJECTED` במקום «טוקן פג».  
זה **לא** עוקף מדיניות גישה של Webtop.

ראו: `ROOT_CAUSE_ANALYSIS.md`, `CONNECTIVITY_COMPARISON.md`.

## משתני סביבה (Render)

| משתנה | חובה | תפקיד |
|--------|------|--------|
| `APP_USER` / `APP_PASSWORD` | כן בענן | Basic Auth (fail-closed) |
| `WEBTOP_TOKEN` | כן (או bio) | עוגיית `webToken` |
| `WEBTOP_BIO_JSON` | אופציונלי | חידוש bio — **רק** לכשלי auth/expiry, לא ל־403 HTML |
| `WEBTOP_FORCE_ENV_TOKEN=1` | לא | סיבוב מכוון: העדף env על הקובץ |
| `WEBTOP_DIAGNOSTICS=1` | לא | אבחון מורחב בלוגי שרת בלבד |
| `WEBTOP_CLOUD` | אוטומטי ב־Docker | `1`/`true` בלבד נחשב True (`false` לא מפעיל) |

סובבו סיסמאות/טוקנים שנחשפו בעבר לפני פרודקשן.

## מקומי — אימות

```powershell
cd C:\Users\nben-har\ori-summary_API
python -m pip install -r requirements-dev.txt
python -m pytest -q
$env:WEBTOP_DIAGNOSTICS = "1"
python diagnose_webtop.py
python update_data.py
python server.py
# http://127.0.0.1:8765/  + /healthz
```

חידוש טוקן חד־פעמי: `רענון-טוקן.bat` (Chrome + CAPTCHA).

## Deploy (ידני — ללא push/deploy אוטומטי מהחקירה)

1. Review + merge/push של הענף לפי החלטתכם.
2. Render Docker service קיים — אחרי deploy בדקו Logs ל־`CONNECTIVITY diag` / `HTTP diag`.
3. `WEBTOP_DIAGNOSTICS=1` זמנית להשוואה, ואז כבו.

Health: `/healthz` (ללא סודות).

## בדיקות

```powershell
python -m pytest -q
```

הבדיקות משתמשות ב־mocks/סינתטי בלבד — בלי Webtop חי ובלי נתוני תלמיד אמיתיים.
