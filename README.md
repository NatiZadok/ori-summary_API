# ori-summary_API — סיכום אורי דרך Webtop API

עותק של פרויקט הסיכום המשפחתי, עם **איסוף נתונים ב־HTTP API**  
(`webtopserver.smartschool.co.il`) במקום סריקת דפדפן (Playwright).

## מה השתנה לעומת ori-summary

| לפני | עכשיו |
|------|--------|
| Playwright + CAPTCHA | `webToken` + קריאות API |
| `update_webtop.py` | `update_data.py` |
| Docker Playwright | Python slim |

## הפעלה מקומית

1. `רענון-טוקן.bat` (או בדף: **רענון טוקן**) — Chrome + CAPTCHA פעם אחת; הטוקן נשמר ומועתק ללוח
2. `הפעל-סיכום.bat` → http://127.0.0.1:8765/
3. **עדכן עכשיו**

אפשר גם ידנית: DevTools → Cookies → `webToken`, או קובץ `webtop_token.txt`.

## Render

| משתנה | תפקיד |
|--------|--------|
| `APP_USER` / `APP_PASSWORD` | Basic Auth לדף |
| `WEBTOP_TOKEN` | **מומלץ** — עוגיית webToken |
| `WEBTOP_STATE_B64` | חלופה — storage עם webToken |

Runtime: **Docker** (`Dockerfile` רגיל, בלי Playwright).

## הערות

- התחברות מאפס עדיין דורשת CAPTCHA ב־Webtop — לכן משתמשים ב־token קיים.
- Token רגיל נמשך שעות; עם remember-me / bio יכול להחזיק הרבה יותר.
- חתימות בחלון דפדפן לא חלק מזרימת ה־API (placeholder בדשבורד).
