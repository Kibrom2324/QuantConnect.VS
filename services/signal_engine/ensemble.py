"""
APEX Ensemble Scorer — services/signal_engine/ensemble.py

Fixes implemented in this file
───────────────────────────────
  Step-5C  TFT staleness gate: reject TFT signal if it is older than
           TFT_SIGNAL_MAX_AGE_SECONDS (default 600 s / 10 min).
           Previously a stale TFT prediction could dominate ensemble weighting
           during model restarts or Redis lag spikes.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Step-5C FIX 2026-02-27: maximum age for a TFT signal before it is discarded
TFT_SIGNAL_MAX_AGE_SECONDS: float = float(
    os.environ.get("TFT_SIGNAL_MAX_AGE_SECONDS", "600")
)

# Ensemble weights — can be overridden via env vars for live tuning
# LLM weight is drawn from the factor allocation; set to 0 to disable.
WEIGHT_TFT    = float(os.environ.get("ENSEMBLE_WEIGHT_TFT",    "0.35"))
WEIGHT_XGB    = float(os.environ.get("ENSEMBLE_WEIGHT_XGB",    "0.30"))
WEIGHT_FACTOR = float(os.environ.get("ENSEMBLE_WEIGHT_FACTOR", "0.20"))
WEIGHT_LLM    = float(os.environ.get("ENSEMBLE_WEIGHT_LLM",    "0.15"))

# Phase 0: prediction lineage feature flag
ENABLE_PREDICTION_LINEAGE: bool = (
    os.environ.get("ENABLE_PREDICTION_LINEAGE", "false").lower() == "true"
)


class EnsembleScorer:
    """
    Combines TFT (temporal fusion transformer), XGBoost, and factor-model
    sub-signals into a single scalar score.

    Step-5C FIX: TFT score is zeroed out (and ensemble reweighted) when the
    TFT prediction timestamp is older than TFT_SIGNAL_MAX_AGE_SECONDS.
    """

    # ─── Main entry point ──────────────────────────────────────────────────

    def score(self, payload: dict[str, Any]) -> tuple[float | None, list[str]]:
        """
        Parameters
        ----------
        payload : dict with keys:
            tft_score        : float
            tft_ts           : ISO-8601 timestamp of TFT prediction
            xgb_score        : float
            factor_score     : float
            llm_score        : float  (optional — from local Ollama agent)

        Returns
        -------
        tuple of (score, prediction_ids):
            score : float or None if critical sub-signals are missing.
            prediction_ids : list of UUID hex strings, one per model component
                             (empty list if ENABLE_PREDICTION_LINEAGE is False).
        """
        xgb_score    = payload.get("xgb_score")
        factor_score = payload.get("factor_score")
        llm_score    = payload.get("llm_score")
        tft_score, tft_weight = self._tft_with_staleness_gate(payload)

        # Phase 0: generate prediction IDs for lineage tracking
        prediction_ids: list[str] = []

        # Build list of (weight, score) for available sub-signals only.
        # Tier-1 FIX: missing XGB or factor → degraded score using present signals,
        # not None (which would silently drop the symbol from execution).
        components: list[tuple[float, float]] = []
        if tft_score is not None:
            components.append((tft_weight, float(tft_score)))
            prediction_ids.append(uuid.uuid4().hex)
        if xgb_score is not None:
            components.append((WEIGHT_XGB, float(xgb_score)))
            prediction_ids.append(uuid.uuid4().hex)
        if factor_score is not None:
            components.append((WEIGHT_FACTOR, float(factor_score)))
            prediction_ids.append(uuid.uuid4().hex)
        if llm_score is not None:
            components.append((WEIGHT_LLM, float(llm_score)))
            prediction_ids.append(uuid.uuid4().hex)

        if not components:
            logger.warning(
                "ensemble_no_scores_available",
                symbol=payload.get("symbol"),
            )
            return None, []

        missing = []
        if xgb_score is None:
            missing.append("xgb")
        if factor_score is None:
            missing.append("factor")
        if missing:
            logger.warning(
                "ensemble_degraded_missing_scores",
                symbol=payload.get("symbol"),
                missing=missing,
                using=[c for c in ("tft", "xgb", "factor")
                       if c not in missing and (c != "tft" or tft_score is not None)],
            )

        total_weight = sum(w for w, _ in components)
        if total_weight == 0:
            return 0.0, prediction_ids

        combined = sum(w * s for w, s in components) / total_weight

        logger.debug(
            "ensemble_scored",
            symbol=payload.get("symbol"),
            tft_weight=tft_weight,
            n_components=len(components),
            llm_included=llm_score is not None,
            combined=combined,
            prediction_ids=prediction_ids,
        )
        return combined, prediction_ids if ENABLE_PREDICTION_LINEAGE else []

    # ─── TFT staleness gate ─────────────────────────────────────────────────

    def _tft_with_staleness_gate(
        self, payload: dict[str, Any]
    ) -> tuple[float | None, float]:
        """
        Step-5C FIX 2026-02-27: Return (tft_score, effective_weight).
        If the TFT prediction is older than TFT_SIGNAL_MAX_AGE_SECONDS,
        zero the weight — effectively removing TFT from the ensemble for
        this bar.

        Returns
        -------
        (score, weight) where weight is 0.0 when signal is stale.
        """
        tft_score = payload.get("tft_score")
        tft_ts    = payload.get("tft_ts")

        if tft_score is None or tft_ts is None:
            logger.debug("tft_score_absent_weight_zeroed", symbol=payload.get("symbol"))
            return None, 0.0

        try:
            pred_time = datetime.fromisoformat(tft_ts)
            if pred_time.tzinfo is None:
                pred_time = pred_time.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - pred_time).total_seconds()
        except (ValueError, TypeError) as e:
            logger.warning(
                "tft_ts_parse_failed_weight_zeroed",
                tft_ts=tft_ts,
                error=str(e),
            )
            return None, 0.0

        if age_seconds > TFT_SIGNAL_MAX_AGE_SECONDS:
            logger.warning(
                "tft_signal_stale_weight_zeroed",     # Step-5C FIX identifier
                symbol=payload.get("symbol"),
                age_seconds=age_seconds,
                max_age=TFT_SIGNAL_MAX_AGE_SECONDS,
            )
            return None, 0.0

        return float(tft_score), WEIGHT_TFT
