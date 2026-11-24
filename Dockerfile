FROM python:3.11.9-slim

WORKDIR /app

COPY . .

ENV PYTHONUNBUFFERED=1
ENV WEBTOP_CLOUD=1

CMD ["python", "server.py"]
