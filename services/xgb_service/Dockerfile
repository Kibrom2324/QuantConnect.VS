# services/xgb_service/Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/xgb_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/xgb_service/ ./services/xgb_service/
COPY shared/ ./shared/

# Model artifacts volume-mounted at runtime
VOLUME ["/app/models"]

ENV PYTHONPATH=/app
ENV PORT=8007
EXPOSE 8007

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["python", "services/xgb_service/main.py"]
