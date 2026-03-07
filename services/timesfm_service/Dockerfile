# ── APEX TimesFM Inference Service ───────────────────────────────────────────
# python:3.11-slim base keeps the image lean while supporting PyTorch CPU/GPU.
FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Build context is workspace root (context: ..)  — paths are relative to that.
COPY services/timesfm_service/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Pre-download TimesFM model weights at build time ─────────────────────────
# This avoids a 2-minute download on every container start-up.
# The HuggingFace cache lives in /root/.cache/huggingface inside the image.
RUN python -c "\
import timesfm; \
_ = timesfm.TimesFm( \
    hparams=timesfm.TimesFmHparams(backend='cpu', per_core_batch_size=32, horizon_len=1), \
    checkpoint=timesfm.TimesFmCheckpoint(huggingface_repo_id='google/timesfm-1.0-200m-pytorch'), \
); \
print('TimesFM weights cached successfully') \
"

# ── Application code ──────────────────────────────────────────────────────────
COPY services/timesfm_service/ ./services/timesfm_service/
COPY services/model_manager/   ./services/model_manager/
COPY shared/                   ./shared/

# ── Python path so imports resolve ───────────────────────────────────────────
ENV PYTHONPATH=/app

# ── Expose port ───────────────────────────────────────────────────────────────
EXPOSE 8010

# ── Health check ──────────────────────────────────────────────────────────────
# 120 s start_period allows the model to fully initialise on startup
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8010/health || exit 1

# ── Entry point ───────────────────────────────────────────────────────────────
CMD ["python", "services/timesfm_service/main.py"]
