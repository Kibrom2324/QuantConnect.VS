"""
shared/core/metrics.py — Centralised Prometheus metric definitions.

All services import their metrics from here so names, labels, and bucket
boundaries are consistent across the platform.

Usage:
    from shared.core.metrics import (
        PIPELINE_STALE, SIGNAL_SCORE, KILL_SWITCH_STATE,
        POSITION_MISMATCH, DAILY_LOSS_PCT, ORDER_LATENCY,
        start_metrics_server,
    )

    # At service startup:
    start_metrics_server(port=8001)

    # Emit:
    PIPELINE_STALE.labels(service="data_ingestion").set(0)
    SIGNAL_SCORE.labels(symbol="NVDA", alpha="rsi").set(0.42)
    ORDER_LATENCY.observe(0.35)
"""
from __future__ import annotations

import os
import threading

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

# ---------------------------------------------------------------------------
# apex_pipeline_stale{service}
#   Gauge: 1 = pipeline has not received a message in the last 60 seconds.
#   Alert: PipelineStale fires if > 0 for 2 minutes during market hours
#          (UTC 14:00–21:00 Mon–Fri).
# ---------------------------------------------------------------------------
PIPELINE_STALE = Gauge(
    "apex_pipeline_stale",
    "1 if no Kafka message received in the last 60 seconds",
    labelnames=["service"],
)

# ---------------------------------------------------------------------------
# apex_signal_score{symbol, alpha}
#   Gauge: latest normalised score ∈ [-1, +1] per alpha per symbol.
# ---------------------------------------------------------------------------
SIGNAL_SCORE = Gauge(
    "apex_signal_score",
    "Latest composite or per-alpha signal score in [-1, +1]",
    labelnames=["symbol", "alpha"],
)

# ---------------------------------------------------------------------------
# apex_kill_switch_state
#   Gauge: 1 = trading ACTIVE, 0 = HALTED.
#   Alert: KillSwitchHalted fires immediately when == 0.
# ---------------------------------------------------------------------------
KILL_SWITCH_STATE = Gauge(
    "apex_kill_switch_state",
    "1 = trading active, 0 = kill switch engaged (halted)",
)

# ---------------------------------------------------------------------------
# apex_position_mismatch_total
#   Counter: incremented whenever a position divergence is detected during
#   the 60-second reconciliation cycle in ExecutionAgent.
#   Alert: PositionMismatch fires on any increment.
# ---------------------------------------------------------------------------
POSITION_MISMATCH = Counter(
    "apex_position_mismatch_total",
    "Total number of position reconciliation mismatches detected",
)

# ---------------------------------------------------------------------------
# apex_daily_loss_pct
#   Gauge: current day's realised + unrealised loss as a fraction of NAV.
#   Positive value = loss (e.g., 0.02 means 2% loss).
#   Alert: DailyLossBreached fires if > 0.03.
# ---------------------------------------------------------------------------
DAILY_LOSS_PCT = Gauge(
    "apex_daily_loss_pct",
    "Current daily loss as a fraction of NAV (0.03 = 3%)",
)

# ---------------------------------------------------------------------------
# apex_order_latency_seconds
#   Histogram: wall-clock time from Kafka signal consume to Alpaca fill.
#   Buckets chosen for HFT-adjacent latency profile.
# ---------------------------------------------------------------------------
ORDER_LATENCY = Histogram(
    "apex_order_latency_seconds",
    "Time in seconds from signal Kafka consume to Alpaca order fill",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# ---------------------------------------------------------------------------
# apex_circuit_breaker_open{service}
#   Gauge: 1 = circuit breaker open (execution paused), 0 = closed.
# ---------------------------------------------------------------------------
CIRCUIT_BREAKER_OPEN = Gauge(
    "apex_circuit_breaker_open",
    "1 = circuit breaker is open (execution paused for back-off period)",
    labelnames=["service"],
)

# ---------------------------------------------------------------------------
# apex_orders_total{symbol, side, status}
#   Counter: total orders submitted, labelled by outcome.
# ---------------------------------------------------------------------------
ORDERS_TOTAL = Counter(
    "apex_orders_total",
    "Total orders submitted to Alpaca",
    labelnames=["symbol", "side", "status"],
)

# ---------------------------------------------------------------------------
# apex_kafka_messages_total{service, topic, result}
#   Counter: messages consumed, labelled by processing result.
# ---------------------------------------------------------------------------
KAFKA_MESSAGES_TOTAL = Counter(
    "apex_kafka_messages_total",
    "Total Kafka messages consumed",
    labelnames=["service", "topic", "result"],
)

# ---------------------------------------------------------------------------
# apex_stale_messages_dropped_total{service}
#   Counter: messages rejected because (now - signal_timestamp) > 30s.
# ---------------------------------------------------------------------------
STALE_MESSAGES_DROPPED = Counter(
    "apex_stale_messages_dropped_total",
    "Total Kafka messages dropped because they exceeded the staleness gate",
    labelnames=["service"],
)

# ---------------------------------------------------------------------------
# apex_model_inference_latency_seconds{symbol}
#   Histogram: TFT inference wall-clock time per symbol.
# ---------------------------------------------------------------------------
MODEL_INFERENCE_LATENCY = Histogram(
    "apex_model_inference_latency_seconds",
    "Time in seconds for TFT model inference per symbol",
    labelnames=["symbol"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

# ---------------------------------------------------------------------------
# apex_feature_freshness_seconds{symbol}
#   Gauge: age in seconds of the latest feature vector for each symbol.
# ---------------------------------------------------------------------------
FEATURE_FRESHNESS = Gauge(
    "apex_feature_freshness_seconds",
    "Age of the most recent feature vector in seconds",
    labelnames=["symbol"],
)

# ---------------------------------------------------------------------------
# Phase 0: apex_calibration_brier_score
#   Gauge: Brier score of the isotonic calibrator (lower = better).
#   Updated by model_monitor from Redis feedback key.
# ---------------------------------------------------------------------------
CALIBRATION_BRIER = Gauge(
    "apex_calibration_brier_score",
    "Brier score of the isotonic calibrator (lower is better)",
)

# ---------------------------------------------------------------------------
# Phase 1: apex_net_edge_bps
#   Histogram: Distribution of net-of-cost edge at signal time.
# ---------------------------------------------------------------------------
NET_EDGE_BPS = Histogram(
    "apex_net_edge_bps",
    "Distribution of net-of-cost edge in basis points at signal time",
    buckets=[-50, -20, -10, -5, 0, 5, 10, 20, 50, 100, 200],
)

# ---------------------------------------------------------------------------
# Phase 3: apex_regime_state{symbol}
#   Gauge: Current regime label per symbol.
# ---------------------------------------------------------------------------
REGIME_STATE = Gauge(
    "apex_regime_state",
    "Current regime label (0=unknown, 1=up, 2=down, 3=range, 4=volatile)",
    labelnames=["symbol"],
)

# ---------------------------------------------------------------------------
# Phase 3: apex_model_weight{model}
#   Gauge: Current adaptive weight per model.
# ---------------------------------------------------------------------------
MODEL_WEIGHT = Gauge(
    "apex_model_weight",
    "Current adaptive weight per model in the ensemble",
    labelnames=["model"],
)

# ---------------------------------------------------------------------------
# Phase 4: apex_ood_rate
#   Gauge: Fraction of predictions flagged OOD in last hour.
# ---------------------------------------------------------------------------
OOD_RATE = Gauge(
    "apex_ood_rate",
    "Fraction of predictions flagged OOD in the last hour",
)

# ---------------------------------------------------------------------------
# Phase 4: apex_veto_precision
#   Gauge: Fraction of vetoed trades that would have lost.
# ---------------------------------------------------------------------------
VETO_PRECISION = Gauge(
    "apex_veto_precision",
    "Fraction of vetoed trades that would have lost (correct vetoes)",
)

# ---------------------------------------------------------------------------
# Phase 5: apex_feedback_lag_seconds
#   Gauge: Time from fill to feedback incorporation.
# ---------------------------------------------------------------------------
FEEDBACK_LAG = Gauge(
    "apex_feedback_lag_seconds",
    "Time in seconds from fill to feedback incorporation",
)

# ---------------------------------------------------------------------------
# Phase 5: apex_cost_estimation_error_bps
#   Gauge: |estimated - realized| cost, rolling mean.
# ---------------------------------------------------------------------------
COST_ESTIMATION_ERROR = Gauge(
    "apex_cost_estimation_error_bps",
    "Absolute difference between estimated and realized cost in bps",
)

# ---------------------------------------------------------------------------
# Metrics HTTP server
# ---------------------------------------------------------------------------
_server_started = threading.Event()


def start_metrics_server(
    port: int | None = None,
    addr: str = "0.0.0.0",
) -> None:
    """
    Start the Prometheus HTTP scrape endpoint.

    The port is resolved from:
      1. The *port* argument (if given)
      2. METRICS_PORT env var
      3. Default: 9100

    Safe to call multiple times — only starts once per process.
    """
    if _server_started.is_set():
        return
    resolved = int(port or os.getenv("METRICS_PORT", "9100"))
    start_http_server(resolved, addr=addr)
    _server_started.set()
    import structlog  # noqa: PLC0415
    structlog.get_logger(__name__).info(
        "metrics_server_started", port=resolved, addr=addr
    )
