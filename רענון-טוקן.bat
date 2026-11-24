@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo === רענון WEBTOP_TOKEN ===
echo ייפתח Chrome. סמני CAPTCHA ולחצי כניסה.
echo בסוף הטוקן יועתק ללוח — הדביקי ב-Render ל-WEBTOP_TOKEN.
echo.
python -c "import playwright" 2>nul || (
  echo מתקין Playwright...
  pip install playwright
  playwright install chromium
)
python get_webtop_token.py
echo.
pause
