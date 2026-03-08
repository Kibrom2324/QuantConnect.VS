# ─── services/signal_generator/Dockerfile ────────────────────────────────────

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /install /usr/local
ENV PYTHONPATH=/app

COPY shared/                        ./shared/
COPY configs/                       ./configs/
COPY services/__init__.py           ./services/__init__.py
COPY services/signal_generator/     ./services/signal_generator/
COPY services/graceful_shutdown.py  ./services/graceful_shutdown.py

RUN groupadd -r apex && useradd -r -g apex apex
USER apex

CMD ["python", "-m", "services.signal_generator.main"]
