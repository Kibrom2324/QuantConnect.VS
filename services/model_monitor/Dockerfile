FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN pip install --no-cache-dir httpx redis prometheus_client structlog

COPY shared/                    ./shared/
COPY services/model_monitor/    ./services/model_monitor/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8020

CMD ["python", "services/model_monitor/main.py"]
