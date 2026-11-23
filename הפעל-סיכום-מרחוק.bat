@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo === סיכום אורי — מצב עדכון מרחוק ===
echo 1^) השרת המקומי ייפתח לגישה חיצונית
echo 2^) בחלון נוסף הריצי מנהרה, למשל:
echo      cloudflared tunnel --url http://127.0.0.1:8765
echo 3^) העתיקי את הכתובת https://....trycloudflare.com
echo 4^) ב-Render ^> Environment שימי:
echo      HOME_UPDATE_URL = הכתובת מהמנהרה
echo      UPDATE_TOKEN   = אותו סוד כמו כאן למטה (אם משתמשים^)
echo.

REM אפשר לשנות את הסוד המשותף עם Render:
if "%UPDATE_TOKEN%"=="" set UPDATE_TOKEN=ori-family-secret
set ALLOW_REMOTE=1

start "" http://127.0.0.1:8765/
python server.py
pause
