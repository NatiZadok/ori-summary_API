# ori-summary_API — סיכום אורי דרך Webtop API

איסוף נתונים ב־HTTP API (בלי Playwright בעדכון עצמו).

## חידוש אוטומטי (בלי ללחוץ בכל עדכון)

1. **פעם אחת במחשב:** `רענון-טוקן.bat`  
   Chrome → CAPTCHA → כניסה.  
   נשמרים `webToken` + `bio_credentials.json` (~שנה).
2. מה שהועתק ללוח — הדביקי ב־Render ל־**`WEBTOP_BIO_JSON`** (JSON מלא).
3. מכאן: **עדכן עכשיו** מחדש טוקן לבד דרך `loginByBio` — בלי CAPTCHA.

כש־bio פג (~שנה) — רק אז שוב `רענון-טוקן.bat`.

## Render Environment

| משתנה | תפקיד |
|--------|--------|
| `APP_USER` / `APP_PASSWORD` | Basic Auth לדף |
| `WEBTOP_BIO_JSON` | **מומלץ** — חידוש אוטומטי ~שנה |
| `WEBTOP_TOKEN` | אופציונלי / גיבוי קצר |

Runtime: **Docker**.

### חשוב: 403 מ־Render
Webtop/Cloudflare **חוסם IP של שרתי ענן** (HTTP 403), גם עם טוקן תקף.  
מקומית (IP ביתי) העדכון עובד.  
לכן «עדכן עכשיו» בענן לא יכול למשוך ישירות מ־Webtop — צריך עדכון מהמחשב בבית (או מנהרה לבית).

## מקומי

`הפעל-סיכום.bat` → http://127.0.0.1:8765/ → עדכן עכשיו  
(או כפתור 🔑 רענון טוקן להגדרה מחדש)
