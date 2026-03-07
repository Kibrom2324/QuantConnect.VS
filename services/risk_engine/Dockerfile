# ─── services/risk_engine/Dockerfile ─────────────────────────────────────────
# Multi-stage build: install deps in a builder stage, copy only what is needed.

FROM python:3.11-slim AS builder

WORKDIR /build

# Install build essentials (needed by some C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# System-wide install (not --user) so packages are accessible to any OS user
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

WORKDIR /app

# curl needed for HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy globally installed packages from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
ENV PYTHONPATH=/app
ENV PORT=8004

# Copy only the required source tree
COPY shared/        ./shared/
COPY configs/       ./configs/
COPY services/risk_engine/  ./services/risk_engine/
COPY services/__init__.py   ./services/__init__.py

# Non-root user for security
RUN groupadd -r apex && useradd -r -g apex apex
USER apex

EXPOSE 8004

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["python", "-m", "services.risk_engine.main"]
