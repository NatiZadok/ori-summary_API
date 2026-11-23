# Official Playwright image — Chromium + system libs ready for Render
FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV WEBTOP_CLOUD=1
# Render sets PORT; server.py already reads it

CMD ["python", "server.py"]
