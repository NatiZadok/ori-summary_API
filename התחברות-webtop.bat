@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo === התחברות Webtop (Chrome רגיל) ===
echo אם האימות נכשל — לחצי F5 בחלון Chrome ואז סמני שוב.
echo.
python login_webtop.py
echo.
pause
