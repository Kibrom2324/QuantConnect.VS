FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/feature_engineering/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/feature_engineering/ ./services/feature_engineering/
COPY services/shared/              ./services/shared/

ENV PYTHONPATH=/app
WORKDIR /app/services/feature_engineering

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
