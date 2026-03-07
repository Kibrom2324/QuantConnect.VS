"""
APEX Risk Engine — services/risk_engine/engine.py

Fixes implemented in this file
───────────────────────────────
  CF-5    CVaR formula: historical simulation — mean of worst 5%
          (was Gaussian approximation, statistically invalid for fat tails)
  CF-6    Redis crash → fail-closed: trading_enabled=False, never fail-open
  Bug-A   Config path: "configs/limits.yaml" (not "config/limits.yaml")
  HI-1    UnifiedPortfolioRisk.evaluate() is now called in RiskEngine.evaluate()
  HI-2    _correlation_history is now populated and used in evaluate()
  HI-9    PositionState duplicate to_dict/from_dict removed — single definition kept
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import redis.asyncio as aioredis
import structlog
import yaml

logger = structlog.get_logger(__name__)

# ─── Position State ──────────────────────────────────────────────────────────
# HI-9 FIX 2026-02-27: Only ONE definition of to_dict / from_dict.
#   (Previously there were two dataclass methods; Python silently used the last
#    one, making the first invisible.  Single authoritative definition below.)

@dataclass
class PositionState:
    symbol:       str
    quantity:     float
    avg_price:    float
    market_value: float
    unrealised_pnl: float = 0.0
    side:         str     = "LONG"   # "LONG" | "SHORT" | "FLAT"
    opened_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # HI-9 FIX: single canonical to_dict / from_dict
    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "quantity":       self.quantity,
            "avg_price":      self.avg_price,
            "market_value":   self.market_value,
            "unrealised_pnl": self.unrealised_pnl,
            "side":           self.side,
            "opened_at":      self.opened_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PositionState":
        opened_at = d.get("opened_at")
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at)
        return cls(
            symbol         = d["symbol"],
            quantity       = float(d["quantity"]),
            avg_price      = float(d["avg_price"]),
            market_value   = float(d["market_value"]),
            unrealised_pnl = float(d.get("unrealised_pnl", 0.0)),
            side           = d.get("side", "LONG"),
            opened_at      = opened_at or datetime.now(timezone.utc),
        )


# ─── Risk Decision ────────────────────────────────────────────────────────────

@dataclass
class RiskDecision:
    approved:  bool
    reason:    str = ""
    metadata:  dict = field(default_factory=dict)

    @classmethod
    def approve(cls) -> "RiskDecision":
        return cls(approved=True)

    @classmethod
    def block(cls, reason: str, **metadata: Any) -> "RiskDecision":
        return cls(approved=False, reason=reason, metadata=metadata)


# ─── Portfolio Risk Result ────────────────────────────────────────────────────

@dataclass
class PortfolioRiskResult:
    should_block: bool
    reason:       str = ""
    metrics:      dict = field(default_factory=dict)


# ─── Unified Portfolio Risk ───────────────────────────────────────────────────
# HI-1 FIX 2026-02-27: evaluate() is no longer dead code — it is called from
#   RiskEngine.evaluate() below.
# HI-2 FIX 2026-02-27: _correlation_history is populated from incoming signals
#   and used in evaluate() to detect high cross-asset correlation (crowding risk).

class UnifiedPortfolioRisk:
    """
    Cross-asset portfolio risk assessor.
    Tracks rolling return correlations between held symbols and blocks
    trades when the portfolio is dangerously concentrated in correlated
    positions (crowding risk).
    """

    MAX_CORRELATION_WINDOW = 60   # bars to track
    CORRELATION_BLOCK_THRESHOLD = 0.85  # block if avg pairwise corr > this

    def __init__(self) -> None:
        # HI-2 FIX: history is populated in update_returns() and used in evaluate()
        self._correlation_history: dict[str, list[float]] = {}

    def update_returns(self, symbol: str, ret: float) -> None:
        """Feed a new bar return for symbol into the rolling window."""
        hist = self._correlation_history.setdefault(symbol, [])
        hist.append(ret)
        if len(hist) > self.MAX_CORRELATION_WINDOW:
            hist.pop(0)

    def evaluate(self, positions: dict[str, PositionState]) -> PortfolioRiskResult:
        """
        HI-1 FIX: This method is now called from RiskEngine.evaluate().

        Compute the average pairwise Pearson correlation of active positions.
        Block if the average exceeds CORRELATION_BLOCK_THRESHOLD (herding/crowding).
        """
        active_symbols = [s for s, p in positions.items() if p.quantity != 0]

        if len(active_symbols) < 2:
            return PortfolioRiskResult(should_block=False, reason="single_asset_no_corr_check")

        # Build return matrix — only symbols with enough history
        min_bars = 20
        series_map = {
            sym: self._correlation_history[sym]
            for sym in active_symbols
            if sym in self._correlation_history
            and len(self._correlation_history[sym]) >= min_bars
        }

        if len(series_map) < 2:
            return PortfolioRiskResult(should_block=False, reason="insufficient_history")

        symbols  = list(series_map.keys())
        min_len  = min(len(v) for v in series_map.values())
        matrix   = np.array([series_map[s][-min_len:] for s in symbols])

        # Pearson correlation matrix
        corr     = np.corrcoef(matrix)
        n        = len(symbols)
        pairs    = [(corr[i, j]) for i in range(n) for j in range(i + 1, n)]
        avg_corr = float(np.mean(pairs)) if pairs else 0.0

        metrics = {
            "avg_pairwise_corr": avg_corr,
            "symbols_checked":   symbols,
            "n_pairs":           len(pairs),
        }

        if avg_corr > self.CORRELATION_BLOCK_THRESHOLD:
            return PortfolioRiskResult(
                should_block=True,
                reason=(
                    f"high_portfolio_correlation avg={avg_corr:.3f} "
                    f"> threshold={self.CORRELATION_BLOCK_THRESHOLD}"
                ),
                metrics=metrics,
            )

        return PortfolioRiskResult(should_block=False, metrics=metrics)


# ─── Risk Engine ─────────────────────────────────────────────────────────────

class RiskEngine:
    """
    Central risk gate.  Every order from ExecutionAgent must pass through
    evaluate() before being sent to Alpaca.

    Fixes applied
    ─────────────
    Bug-A  Config path is "configs/limits.yaml" (directory is configs/, not config/)
    CF-5   CVaR uses historical simulation: mean of the worst 5% of returns
    CF-6   Any Redis error → trading_enabled = False (fail-closed, never fail-open)
    HI-1   UnifiedPortfolioRisk.evaluate() is called in evaluate()
    HI-2   _correlation_history populated via update_correlation_history()
    HI-9   PositionState has one canonical to_dict/from_dict
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis_url         = redis_url
        self._redis: aioredis.Redis | None = None
        self.trading_enabled    = False   # CF-6: default-safe; set True only after validate()
        self._positions:  dict[str, PositionState] = {}
        self._daily_pnl:  float = 0.0
        self._return_history: list[float] = []   # portfolio-level returns for CVaR
        self._unified_portfolio_risk = UnifiedPortfolioRisk()

        # Bug-A FIX 2026-02-27: "configs/" not "config/"
        self._limits_path = (
            Path(__file__).parent.parent.parent / "configs" / "limits.yaml"
        )
        self._limits: dict = self._load_limits()

    # ─── Startup / Shutdown ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to Redis and load kill-switch state."""
        try:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            stored = await self._redis.get("apex:kill_switch")
            if stored == "true":
                self.trading_enabled = False
                logger.critical("kill_switch_active_at_startup")
            else:
                self.trading_enabled = True
                logger.info("risk_engine_started", redis=self._redis_url)
        except Exception as e:
            # CF-6 FIX: Redis unavailable at startup → fail-closed
            logger.critical("redis_unavailable_at_startup", error=str(e))
            self.trading_enabled = False

    async def stop(self) -> None:
        if self._redis:
            await self._redis.aclose()

    # ─── Limits ──────────────────────────────────────────────────────────────

    def _load_limits(self) -> dict:
        """
        Bug-A FIX: loads from configs/limits.yaml.
        Falls back to safe defaults so a missing file never silently
        allows oversized positions.
        """
        defaults = {
            "max_position_pct": 0.02,
            "max_daily_loss_pct": 0.05,
            "per_symbol": {},
        }
        if self._limits_path.exists():
            try:
                with open(self._limits_path) as fh:
                    loaded = yaml.safe_load(fh) or {}
                defaults.update(loaded)
                logger.info("limits_loaded", path=str(self._limits_path))
            except Exception as e:
                logger.warning("limits_load_failed_using_defaults", error=str(e))
        else:
            logger.warning(
                "limits_yaml_not_found_using_defaults",
                path=str(self._limits_path),
            )
        return defaults

    def _max_position_pct(self, symbol: str) -> float:
        """Return per-symbol cap, falling back to global default."""
        per_sym = self._limits.get("per_symbol", {})
        return float(per_sym.get(symbol, {}).get("max_position_pct",
               self._limits.get("max_position_pct", 0.02)))

    # ─── Redis helpers (CF-6) ─────────────────────────────────────────────────

    async def _redis_get(self, key: str) -> str | None:
        """
        CF-6 FIX 2026-02-27: Any Redis error → halt trading (fail-closed).
        Never swallow Redis errors and continue as if nothing happened.
        """
        try:
            return await self._redis.get(key)
        except Exception as e:
            logger.critical(
                "redis_failure_halt_trading",
                key=key,
                error=str(e),
            )
            self.trading_enabled = False   # CF-6: fail-closed — NEVER fail-open
            raise RuntimeError(f"Redis failure — trading halted for safety: {e}") from e

    async def _redis_set(self, key: str, value: str) -> None:
        """CF-6 FIX: fail-closed on any Redis write error."""
        try:
            await self._redis.set(key, value)
        except Exception as e:
            logger.critical(
                "redis_write_failure_halt_trading",
                key=key,
                error=str(e),
            )
            self.trading_enabled = False   # CF-6: fail-closed — NEVER fail-open
            raise RuntimeError(f"Redis failure — trading halted for safety: {e}") from e

    # ─── CVaR (CF-5) ─────────────────────────────────────────────────────────

    def compute_cvar_95(self, returns: list[float]) -> float:
        """
        CF-5 FIX 2026-02-27: Historical simulation CVaR (Expected Shortfall).

        Method: take the worst 5% of observed portfolio returns and return
        their mean.  This is the statistically valid non-parametric CVaR.

        Previous implementation used a Gaussian approximation which
        underestimates tail risk for fat-tailed equity return distributions.

        Returns a positive number representing the expected loss magnitude
        (e.g. 0.023 means "expect to lose 2.3% in the worst 5% of days").
        """
        if len(returns) < 20:
            return 0.0

        arr = np.asarray(returns, dtype=float)
        cutoff = np.percentile(arr, 5)                     # 5th-percentile threshold
        tail   = arr[arr <= cutoff]                        # worst 5% of observations
        cvar   = float(-np.mean(tail)) if len(tail) > 0 else 0.0  # positive = loss
        return max(cvar, 0.0)

    def update_return_history(self, portfolio_return: float) -> None:
        """Feed a new bar portfolio return for CVaR calculation."""
        self._return_history.append(portfolio_return)
        if len(self._return_history) > 500:
            self._return_history.pop(0)

    # ─── Correlation history (HI-2) ──────────────────────────────────────────

    def update_correlation_history(self, symbol: str, bar_return: float) -> None:
        """HI-2 FIX: Feed per-symbol returns into UnifiedPortfolioRisk."""
        self._unified_portfolio_risk.update_returns(symbol, bar_return)

    # ─── Main evaluation ──────────────────────────────────────────────────────

    async def evaluate(
        self,
        symbol:       str,
        signal_side:  str,       # "BUY" | "SELL"
        quantity:     float,
        portfolio_value: float,
        market_price: float = 0.0,
        current_positions: dict[str, PositionState] | None = None,
    ) -> RiskDecision:
        """
        Gate every order through all risk checks.

        HI-1 FIX: UnifiedPortfolioRisk.evaluate() is now called here.
        CF-6 FIX: Redis errors propagate as halt-trading exceptions.
        CF-5 FIX: CVaR uses historical simulation.
        Bug-A FIX: limits loaded from configs/limits.yaml.
        Tier-1 FIX: trade_value = quantity * market_price (was always == portfolio_value).
        """
        if not self.trading_enabled:
            return RiskDecision.block(reason="trading_disabled_or_kill_switch")

        # ── Kill switch check (Redis-backed) ──────────────────────────────────
        kill = await self._redis_get("apex:kill_switch")   # raises on Redis error (CF-6)
        if kill == "true":
            self.trading_enabled = False
            return RiskDecision.block(reason="kill_switch_active")

        # ── Daily loss limit ──────────────────────────────────────────────────
        max_loss = self._limits.get("max_daily_loss_pct", 0.05)
        if portfolio_value > 0 and (-self._daily_pnl / portfolio_value) >= max_loss:
            return RiskDecision.block(
                reason=f"daily_loss_limit_breached pnl={self._daily_pnl:.2f}"
            )

        # ── Position size limit ───────────────────────────────────────────────
        # Tier-1 FIX: use actual notional value (qty * price).
        # Skip size check when market_price is unavailable (price=0) rather than
        # silently passing an always-zero or always-portfolio_value comparison.
        max_pct = self._max_position_pct(symbol)
        if market_price <= 0:
            logger.warning(
                "position_size_check_skipped_no_price",
                symbol=symbol, quantity=quantity,
            )
        trade_value = quantity * market_price
        if market_price > 0 and portfolio_value > 0 and (trade_value / portfolio_value) > max_pct:
            return RiskDecision.block(
                reason=(
                    f"position_size_exceeded symbol={symbol} "
                    f"proposed={trade_value/portfolio_value:.3f} "
                    f"limit={max_pct:.3f}"
                )
            )

        # ── CVaR check (CF-5) ─────────────────────────────────────────────────
        cvar = self.compute_cvar_95(self._return_history)
        cvar_limit = self._limits.get("max_cvar_95", 0.04)   # default 4%
        if cvar > cvar_limit:
            return RiskDecision.block(
                reason=f"cvar_limit_breached cvar={cvar:.4f} limit={cvar_limit:.4f}"
            )

        # ── Portfolio correlation check (HI-1 + HI-2) ────────────────────────
        positions = current_positions or self._positions
        portfolio_result = self._unified_portfolio_risk.evaluate(positions)  # HI-1 FIX
        if portfolio_result.should_block:
            return RiskDecision.block(
                reason=portfolio_result.reason,
                **portfolio_result.metrics,
            )

        return RiskDecision.approve()

    # ─── Position tracking ────────────────────────────────────────────────────

    def record_fill(
        self,
        symbol:    str,
        quantity:  float,
        price:     float,
        side:      str,
        realised_pnl: float = 0.0,
    ) -> None:
        """Update position book and daily P&L after a fill."""
        self._daily_pnl += realised_pnl

        if side == "SELL" and symbol in self._positions:
            pos = self._positions[symbol]
            pos.quantity -= quantity
            pos.unrealised_pnl += realised_pnl
            if pos.quantity <= 0:
                del self._positions[symbol]
        else:
            mv = quantity * price
            if symbol in self._positions:
                pos = self._positions[symbol]
                total_qty = pos.quantity + quantity
                pos.avg_price   = (pos.avg_price * pos.quantity + price * quantity) / total_qty
                pos.quantity    = total_qty
                pos.market_value = mv
            else:
                self._positions[symbol] = PositionState(
                    symbol=symbol, quantity=quantity,
                    avg_price=price, market_value=mv,
                    side=side,
                )

    def reset_daily_pnl(self) -> None:
        """Call at market open each day."""
        prev = self._daily_pnl
        self._daily_pnl = 0.0
        logger.info("daily_pnl_reset", previous_pnl=prev)
