# סיכום אורי — בית ספר ומשפחה

דף סיכום לאורי (כיתה ב׳) עם שיעורי בית, התראות, הודעות, מערכת שעות, חופשות ולוח משפחתי.

## הפעלה מקומית

1. `config.json` עם משתמש/סיסמה ל־Webtop (או העתקה מ־`config.example.json`)
2. התקנה:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```
3. `הפעל-סיכום.bat` → http://127.0.0.1:8765/

## Render — הכל בענן (בלי להשאיר מחשב דלוק)

### Environment
| משתנה | חובה | תפקיד |
|--------|------|--------|
| `APP_USER` / `APP_PASSWORD` | מומלץ | סיסמת כניסה לדף המשפחתי |
| `WEBTOP_USERNAME` / `WEBTOP_PASSWORD` | כן* | התחברות ל־Webtop |
| `WEBTOP_STATE_JSON` | מומלץ מאוד | סשן שמור אחרי התחברות מקומית |

\* או `config.json` בריפו (פחות מומלץ אם הריפו ציבורי).

### Deploy מומלץ: Docker (Playwright עובד יציב)
ב־Render → Settings → **Language / Runtime** בחרי **Docker** (משתמש ב־`Dockerfile` שבריפו).

אם נשארים ב־Native Python:
- **Build Command:** `pip install -r requirements.txt && playwright install chromium`
- **Start Command:** `python server.py`

### איך לחבר סשן (חשוב ל־Cloudflare)
1. במחשב: הריצי עדכון מקומי מוצלח פעם אחת (`הפעל-סיכום.bat` → עדכן עכשיו).
2. פתחי את `webtop_state.json` שהנוצר, העתיקי **את כל התוכן**.
3. ב־Render → Environment → הוסיפי `WEBTOP_STATE_JSON` = התוכן המלא.
4. Deploy מחדש → בקישור של Render לחצי **עדכן עכשיו**.

כשהסשן תקף — העדכון רץ לגמרי בענן.  
כש־Cloudflare מבטל את הסשן — צריך לרענן את `WEBTOP_STATE_JSON` שוב (פעם בכמה שבועות / כשעדכון נכשל).

## הערות

- חתימות בחלון דפדפן נשארות מקומיות בלבד.
- אל תחשפי סיסמאות בריפו ציבורי — העדיפי Environment ב־Render + Private repo.
