# services/signal_provider/Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/signal_provider/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/signal_provider/ ./services/signal_provider/
COPY shared/ ./shared/

ENV PYTHONPATH=/app
ENV PORT=8002
EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["python", "services/signal_provider/main.py"]
