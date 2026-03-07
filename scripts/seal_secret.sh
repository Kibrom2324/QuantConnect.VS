#!/usr/bin/env bash
# ─── scripts/seal_secret.sh ───────────────────────────────────────────────────
# Helper for managing Kubernetes Sealed Secrets via kubeseal.
#
# Prerequisites:
#   1. kubeseal CLI:  https://github.com/bitnami-labs/sealed-secrets
#      Linux:  curl -sSL https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/kubeseal-linux-amd64.tar.gz | tar xz -C /usr/local/bin kubeseal
#      macOS:  brew install kubeseal
#   2. kubectl configured against your target cluster
#   3. sealed-secrets-controller running in kube-system (see deploy/k8s/base/sealed-secrets-controller.yaml)
#
# Usage:
#   ./scripts/seal_secret.sh bootstrap   — install controller + fetch cert
#   ./scripts/seal_secret.sh fetch-cert  — refresh local public cert
#   ./scripts/seal_secret.sh seal-all    — seal all secrets from .env → generated YAML
#   ./scripts/seal_secret.sh seal KEY VALUE SECRET_NAME KEY_NAME   — seal a single value
#   ./scripts/seal_secret.sh verify      — list SealedSecret resources in apex namespace
#
# Generated file: deploy/k8s/base/sealed-secret-apex-generated.yaml
#   → Safe to commit (encrypted). Cluster-specific: do NOT share across clusters.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

NAMESPACE="apex"
CERT_PATH="deploy/k8s/base/sealed-secrets-cert.pem"
# OUT_FILE is the committed SealedSecret manifest — seal-all writes here directly.
# The file contains placeholder REPLACE strings before sealing; after seal-all
# it contains cluster-specific ciphertext (safe to commit).
OUT_FILE="deploy/k8s/base/sealed-secret-apex.yaml"
ENV_FILE="${ENV_FILE:-.env}"

# ─── Helpers ──────────────────────────────────────────────────────────────────

log()  { printf '\033[0;32m[seal_secret]\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m[seal_secret ERROR]\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[0;33m[seal_secret WARN]\033[0m %s\n' "$*"; }

require_cmd() { command -v "$1" &>/dev/null || err "'$1' not found — install it first."; }

read_env_var() {
    local var="$1"
    local val
    val="$(grep -E "^${var}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
    [[ -z "$val" ]] && err "Variable '$var' not found or empty in $ENV_FILE"
    echo "$val"
}

seal_value() {
    local raw="$1"
    local secret_name="$2"
    local key_name="$3"
    printf '%s' "$raw" | kubeseal --raw \
        --namespace "$NAMESPACE" \
        --name "$secret_name" \
        --from-file=/dev/stdin \
        --cert "$CERT_PATH" \
        --scope namespace-wide \
        2>/dev/null
}

# ─── Commands ─────────────────────────────────────────────────────────────────

cmd_bootstrap() {
    require_cmd kubectl
    require_cmd kubeseal
    log "Installing sealed-secrets controller…"
    kubectl apply -f deploy/k8s/base/sealed-secrets-controller.yaml
    log "Waiting for controller to become ready (up to 60s)…"
    kubectl -n kube-system rollout status deployment/sealed-secrets-controller --timeout=60s
    cmd_fetch_cert
    log "Bootstrap complete. Run: ./scripts/seal_secret.sh seal-all"
}

cmd_fetch_cert() {
    require_cmd kubeseal
    log "Fetching public certificate from controller…"
    kubeseal --fetch-cert --controller-namespace kube-system \
        --controller-name sealed-secrets-controller > "$CERT_PATH"
    log "Certificate saved to $CERT_PATH"
    log "NOTE: commit $CERT_PATH alongside your SealedSecrets."
}

cmd_seal_all() {
    require_cmd kubeseal
    [[ -f "$CERT_PATH" ]] || err "Certificate not found at $CERT_PATH — run: ./scripts/seal_secret.sh fetch-cert"
    [[ -f "$ENV_FILE" ]]  || err ".env file not found at $ENV_FILE — copy .env.example and fill in values"

    log "Reading secrets from $ENV_FILE…"

    ALPACA_API_KEY=$(read_env_var "ALPACA_API_KEY")
    ALPACA_SECRET_KEY=$(read_env_var "ALPACA_SECRET_KEY")
    POSTGRES_USER=$(read_env_var "POSTGRES_USER")
    # TIMESCALEDB_PASSWORD is the canonical name used everywhere.
    # Accept POSTGRES_PASSWORD as a fallback for backwards compatibility.
    TIMESCALEDB_PASSWORD=$(
        { read_env_var "TIMESCALEDB_PASSWORD" 2>/dev/null; } || \
        { read_env_var "POSTGRES_PASSWORD" 2>/dev/null; } || \
        err "Neither TIMESCALEDB_PASSWORD nor POSTGRES_PASSWORD found in $ENV_FILE"
    )
    ANTHROPIC_API_KEY=$(read_env_var "ANTHROPIC_API_KEY")
    POLYGON_API_KEY=$(read_env_var "POLYGON_API_KEY")
    REDIS_PASSWORD=$(read_env_var "REDIS_PASSWORD" 2>/dev/null || echo "")

    log "Sealing apex-alpaca-secret…"
    ENC_ALPACA_KEY=$(seal_value "$ALPACA_API_KEY"    "apex-alpaca-secret" "api_key")
    ENC_ALPACA_SEC=$(seal_value "$ALPACA_SECRET_KEY" "apex-alpaca-secret" "secret_key")

    log "Sealing apex-db-secret…"
    ENC_PG_USER=$(seal_value "$POSTGRES_USER"         "apex-db-secret" "username")
    ENC_PG_PASS=$(seal_value "$TIMESCALEDB_PASSWORD"  "apex-db-secret" "password")

    log "Sealing apex-api-secret…"
    ENC_ANTHROPIC=$(seal_value "$ANTHROPIC_API_KEY" "apex-api-secret" "anthropic_api_key")
    ENC_POLYGON=$(seal_value "$POLYGON_API_KEY"   "apex-api-secret" "polygon_api_key")

    log "Sealing apex-redis-secret…"
    ENC_REDIS_PASS=$(seal_value "${REDIS_PASSWORD:-}" "apex-redis-secret" "password")

    log "Writing generated manifest to $OUT_FILE…"
    cat > "$OUT_FILE" << YAML
# AUTO-GENERATED by ./scripts/seal_secret.sh seal-all — $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Safe to commit. Cluster-specific: regenerate when cluster key changes.
# Re-generate: ./scripts/seal_secret.sh seal-all
---
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: apex-alpaca-secret
  namespace: ${NAMESPACE}
  annotations:
    sealedsecrets.bitnami.com/namespace-wide: "true"
spec:
  encryptedData:
    api_key: ${ENC_ALPACA_KEY}
    secret_key: ${ENC_ALPACA_SEC}
  template:
    metadata:
      name: apex-alpaca-secret
      namespace: ${NAMESPACE}
    type: Opaque
---
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: apex-db-secret
  namespace: ${NAMESPACE}
  annotations:
    sealedsecrets.bitnami.com/namespace-wide: "true"
spec:
  encryptedData:
    username: ${ENC_PG_USER}
    password: ${ENC_PG_PASS}
  template:
    metadata:
      name: apex-db-secret
      namespace: ${NAMESPACE}
    type: Opaque
---
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: apex-api-secret
  namespace: ${NAMESPACE}
  annotations:
    sealedsecrets.bitnami.com/namespace-wide: "true"
spec:
  encryptedData:
    anthropic_api_key: ${ENC_ANTHROPIC}
    polygon_api_key: ${ENC_POLYGON}
  template:
    metadata:
      name: apex-api-secret
      namespace: ${NAMESPACE}
    type: Opaque
---
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: apex-redis-secret
  namespace: ${NAMESPACE}
  annotations:
    sealedsecrets.bitnami.com/namespace-wide: "true"
spec:
  encryptedData:
    password: ${ENC_REDIS_PASS}
  template:
    metadata:
      name: apex-redis-secret
      namespace: ${NAMESPACE}
    type: Opaque
YAML

    log "Generated: $OUT_FILE"
    log ""
    log "Next steps:"
    log "  1. kubectl apply -f $OUT_FILE"
    log "  2. kubectl -n $NAMESPACE get secrets"
    log "  3. Verify: ./scripts/seal_secret.sh verify"
    log "  4. Commit $OUT_FILE (encrypted values are safe to commit)"
}

cmd_seal_single() {
    [[ $# -lt 4 ]] && err "Usage: seal KEY VALUE SECRET_NAME KEY_NAME"
    local raw="$2" secret_name="$3" key_name="$4"
    require_cmd kubeseal
    [[ -f "$CERT_PATH" ]] || err "Certificate not found at $CERT_PATH"
    log "Sealing '$key_name' in secret '$secret_name'…"
    seal_value "$raw" "$secret_name" "$key_name"
    echo ""  # newline after the encrypted blob
}

cmd_verify() {
    require_cmd kubectl
    log "SealedSecrets in namespace '$NAMESPACE':"
    kubectl -n "$NAMESPACE" get sealedsecrets 2>/dev/null || \
        warn "No SealedSecrets found (CRD may not be installed yet)"
    log ""
    log "Plain Secrets (decrypted by controller):"
    kubectl -n "$NAMESPACE" get secrets 2>/dev/null || true
}

# Rotate: re-seal all secrets from updated .env and re-apply to cluster
# Usage: ./scripts/seal_secret.sh rotate
cmd_rotate() {
    log "Rotating secrets — reading from $ENV_FILE…"
    cmd_seal_all
    require_cmd kubectl
    log "Applying rotated secrets to cluster…"
    kubectl apply -f "$OUT_FILE"
    log ""
    log "Rotation complete.  Pods will pick up new secret values on next restart."
    log "To restart all apex pods: kubectl -n $NAMESPACE rollout restart deployment"
}

# ─── Dispatch ─────────────────────────────────────────────────────────────────

CMD="${1:-help}"
case "$CMD" in
    bootstrap)   cmd_bootstrap ;;
    fetch-cert)  cmd_fetch_cert ;;
    seal-all)    cmd_seal_all ;;
    seal)        cmd_seal_single "$@" ;;
    rotate)      cmd_rotate ;;
    verify)      cmd_verify ;;
    *)
        echo "Usage: $0 {bootstrap|fetch-cert|seal-all|seal KEY VALUE SECRET_NAME KEY_NAME|rotate|verify}"
        echo ""
        echo "  bootstrap   Install controller + fetch public cert (run once per cluster)"
        echo "  fetch-cert  Refresh local public certificate"
        echo "  seal-all    Seal all secrets from .env → deploy/k8s/base/sealed-secret-apex.yaml"
        echo "  seal        Seal a single key and print the encrypted value"
        echo "  rotate      Re-seal all secrets from .env and apply to cluster"
        echo "  verify      List SealedSecrets and decrypted Secrets in cluster"
        ;;
esac
