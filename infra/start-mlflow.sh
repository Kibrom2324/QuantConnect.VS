#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  start-mlflow.sh  —  Start the APEX MLflow tracking server
#  Port: 5001 (host) → 5000 (container)
#  Backend: SQLite  /mlflow/mlflow.db
#  Network: apex_default
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

CONTAINER_NAME="apex-mlflow"
NETWORK="apex_net"
IMAGE="ghcr.io/mlflow/mlflow:latest"
DATA_DIR="${HOME}/.apex/mlflow"

# ── Ensure data directory exists ──────────────────────────────────
mkdir -p "${DATA_DIR}/artifacts"

# ── Remove stale container if any ────────────────────────────────
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "▷  Stopping existing ${CONTAINER_NAME} container..."
  docker stop  "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker rm    "${CONTAINER_NAME}" >/dev/null 2>&1 || true
fi

# ── Ensure network exists ─────────────────────────────────────────
if ! docker network ls --format '{{.Name}}' | grep -q "^${NETWORK}$"; then
  echo "▷  Creating Docker network: ${NETWORK}"
  docker network create "${NETWORK}"
fi

# ── Start MLflow ──────────────────────────────────────────────────
echo "▷  Starting MLflow on http://localhost:5001 ..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  --network "${NETWORK}" \
  -p 5001:5000 \
  -v "${DATA_DIR}:/mlflow" \
  -u "$(id -u):$(id -g)" \
  "${IMAGE}" \
  mlflow server \
    --host 0.0.0.0 \
    --port 5000 \
    --backend-store-uri "sqlite:////mlflow/mlflow.db" \
    --default-artifact-root /mlflow/artifacts \
    --serve-artifacts

echo "✓  MLflow started  →  http://localhost:5001"
echo "   Logs: docker logs -f ${CONTAINER_NAME}"
