# services/tft_service/Dockerfile
# Use slim base without GPU by default; GPU layer applied via docker-compose deploy.resources
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY services/tft_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/tft_service/ ./services/tft_service/
COPY services/model_manager/ ./services/model_manager/
COPY shared/ ./shared/

# Model artefacts volume-mounted at runtime
VOLUME ["/app/models"]

ENV PYTHONPATH=/app
ENV PORT=8005
EXPOSE 8005

# Allow 90 s start-period for model loading
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["python", "services/tft_service/main.py"]
