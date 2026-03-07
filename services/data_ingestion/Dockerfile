FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/data_ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/data_ingestion/ ./services/data_ingestion/
COPY services/shared/ ./services/shared/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s \
  --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8001/health || exit 1

CMD ["python", "services/data_ingestion/main.py"]
