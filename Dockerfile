# Образ с Chromium нужной версии — сборщик ходит на Авто.ру обычным браузером
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 DB_PATH=/app/data/kiravto.db REPORT_PATH=/app/data/report.html
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kiravto ./kiravto
COPY tests ./tests
COPY pytest.ini .env.example ./
COPY .claude ./.claude
CMD ["python", "-m", "kiravto", "collector"]
