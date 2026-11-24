@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo === הגדרה חד-פעמית: טוקן + חידוש אוטומטי (bio) ===
echo אחרי זה העדכונים בענן לא דורשים לחיצות.
echo ייפתח Chrome — סמני CAPTCHA ולחצי כניסה פעם אחת.
echo.
python -c "import playwright" 2>nul || (
  echo מתקין Playwright...
  pip install playwright
  playwright install chromium
)
python get_webtop_token.py
echo.
pause
