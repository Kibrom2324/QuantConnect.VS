# APEX - Autonomous Predictive Execution Platform

## 🚀 Overview

APEX is an enhanced algorithmic trading platform built on top of QuantConnect LEAN, featuring:

- **Multi-Algorithm Support**: SMA Crossover, Multi-Indicator Ensemble, Framework-based approaches
- **Advanced Risk Management**: ATR-based position sizing, regime detection, composite risk models
- **Signal Intelligence**: Bayesian ensemble weighting, confidence calibration, regime filtering
- **Enhanced Execution**: VWAP execution models, universe selection, portfolio optimization
- **Real-time API**: FastAPI signal provider with SQLite persistence
- **Comprehensive Reporting**: Enhanced metrics analysis and tracking

## 📁 Project Structure

```
QuantConnect.VS/
├── apex                              # CLI wrapper script
├── run_strategy.sh                   # Enhanced strategy runner
├── Lean/
│   ├── Algorithm.Python/
│   │   ├── SMACrossoverAlgorithm.py     # Original SMA strategy
│   │   ├── APEXEnsembleAlgorithm.py     # Multi-indicator ensemble ⭐
│   │   └── APEXFrameworkAlgorithm.py    # Framework-based strategy ⭐
│   └── [LEAN engine files]
└── MyProject/
    ├── apex_cli.py                   # APEX management CLI ⭐
    ├── signal_provider_api.py        # FastAPI signal service ⭐
    ├── backtest_reporter.py          # Enhanced metrics reporter ⭐
    ├── requirements.txt              # Python dependencies
    └── main.py                       # Original template
```

## 🛠️ Quick Start

### 1. Install Dependencies
```bash
# Activate virtual environment and install dependencies
source lean_venv/bin/activate
pip install fastapi uvicorn pandas numpy requests pydantic
```

### 2. Check System Status
```bash
./apex status
```

### 3. Run Your First APEX Strategy
```bash
# Run the multi-indicator ensemble algorithm (recommended)
./apex run ensemble

# Or run the framework-based algorithm with Signal API
./apex run framework --start-api

# Or run the original SMA algorithm
./apex run sma
```

### 4. View Results
The script will automatically:
- Run the backtest in LEAN
- Generate enhanced metrics analysis
- Create QuantConnect HTML report
- Serve results on http://localhost:8766

## 📊 Algorithm Comparison

| Feature | SMA | Ensemble | Framework |
|---------|-----|----------|-----------|
| **Signals** | 1 (SMA Cross) | 5 (RSI, EMA, MACD, Stochastic, Trend) | 3 Alpha Models |
| **Position Sizing** | Fixed 100% | ATR-based volatility scaling | Confidence-weighted |
| **Risk Management** | None | Regime detection + tracking | Composite (Drawdown + Trailing Stop) |
| **Execution** | Market orders | Smart rebalancing | VWAP execution |
| **Universe** | SPY only | SPY only | Dynamic ETF constituents (20 stocks) |
| **Learning** | None | Bayesian accuracy tracking | Framework-driven |

## 🎯 Key Features Implemented

### 1. APEXEnsembleAlgorithm
- **Multi-Signal Ensemble**: RSI, EMA Cross, MACD, Stochastic, SMA Trend
- **Bayesian Weight Updates**: Tracks prediction accuracy per alpha source
- **ATR-Based Position Sizing**: Risk-adjusted position sizing based on volatility
- **Regime Detection**: Bull/Bear/Sideways market classification
- **Smart Rebalancing**: Daily rebalancing with significant change thresholds

### 2. APEXFrameworkAlgorithm  
- **Universe Selection**: Top 20 liquid SPY constituents (market cap > $1B, price > $5)
- **Alpha Models**: Separate RSI, EMA Cross, and MACD alpha generators
- **Portfolio Construction**: Confidence-weighted allocation (max 10% per position)
- **VWAP Execution**: Volume-weighted average price execution to minimize slippage
- **Composite Risk**: Maximum drawdown (10%) + trailing stop (5%) protection

### 3. Signal Provider API
- **FastAPI Service**: REST API for signal consumption and backtest results
- **Real-time Endpoints**: `/signals/{symbol}`, `/backtests`, `/universe`, `/health`
- **SQLite Persistence**: Local storage for signals and metrics
- **API Documentation**: Auto-generated docs at `/docs`

### 4. Enhanced Reporting
- **APEX Backtest Reporter**: Comprehensive metrics analysis beyond QuantConnect defaults
- **Risk Metrics**: VaR, CVaR, Sortino ratio, Calmar ratio, volatility analysis
- **Trading Metrics**: Detailed win/loss analysis, profit factor, largest trades
- **Ensemble Metrics**: Signal accuracy tracking, regime detection performance

### 5. APEX CLI
- **Unified Interface**: Single command for running strategies, viewing signals, checking status
- **Multi-Algorithm Support**: Easy switching between SMA, ensemble, and framework strategies
- **API Integration**: Direct access to signal data and backtest results
- **System Management**: Status checking, dependency installation, service management

## 🔧 CLI Commands

```bash
# System management
./apex status                    # Show system status
./apex list                      # List available algorithms
./apex install                   # Install Python dependencies

# Running strategies
./apex run sma                   # Run SMA crossover
./apex run ensemble             # Run multi-indicator ensemble (default)
./apex run framework            # Run framework-based strategy
./apex run framework --start-api # Run with Signal API

# Signal analysis (requires API)
./apex signals                  # Show recent signals
./apex signals SPY              # Show latest signal for SPY
./apex signals --regime BULL    # Filter by regime

# Performance analysis (requires API)
./apex backtests                # Show recent backtest results
./apex universe                 # Show current trading universe

# API management
./apex start-api                # Start Signal Provider API
```

## 📈 Signal Intelligence Features

### Bayesian Ensemble Weights
Instead of fixed weights (TFT=0.5, RSI=0.2, etc.), APEX tracks the prediction accuracy of each alpha source over a rolling 500-bar window and updates weights using Bayesian updating:

```python
# Each alpha source tracks successes and total attempts
alpha_accuracy = {"rsi": 15.0, "ema": 18.0, "macd": 12.0}
alpha_total = {"rsi": 25.0, "ema": 30.0, "macd": 25.0}

# Calculate Bayesian-updated weights
weights = {alpha: accuracy/total for alpha, accuracy, total in zip(...)}
ensemble_score = sum(weight * signal for weight, signal in zip(weights.values(), signals.values()))
```

### ATR-Based Position Sizing
Position size scales inversely with volatility for risk parity:

```python
atr_val = self.atr.current.value
price = self.securities[self.spy].price
volatility_factor = min(2.0, 0.01 / (atr_val / price))
target_weight = signal_strength * volatility_factor * 0.5  # Max 50%
```

### Regime Detection
Market regime affects signal scaling:
- **Bull Market**: Strong uptrend (SMA-200 slope > 2%, low volatility)
- **Bear Market**: Downtrend (SMA-200 slope < -2%) → Reduces long signals by 70%
- **Sideways**: Choppy markets → Reduces all signals by 30%

## 🔍 API Endpoints

### Signals
- `GET /signals/{symbol}` - Latest signal for specific symbol
- `GET /signals?min_confidence=0.6&regime=BULL` - Filtered signals
- `POST /signals/mock/{symbol}` - Generate mock signal (testing)

### Backtests  
- `GET /backtests` - Recent backtest results
- `POST /backtests` - Store new backtest (called by algorithms)

### Universe
- `GET /universe` - Current trading universe
- `POST /universe` - Update universe selection

### System
- `GET /health` - API and database health check
- `GET /` - API information and available endpoints

## 📊 Enhanced Metrics

The APEX Backtest Reporter calculates metrics beyond QuantConnect's defaults:

| Category | Metrics |
|----------|---------|
| **Performance** | Total/Annual Return, Sharpe, Sortino, Calmar ratios |
| **Risk** | Max Drawdown, Volatility, VaR-95, CVaR-95, Beta, Alpha |
| **Trading** | Win Rate, Profit Factor, Average Win/Loss, Largest trades |
| **Ensemble** | Signal accuracy, Regime detection accuracy, Alpha weights |

## 🚨 Known Issues & Fixes

### CF-3 Annualization Fix
The reporter includes validation for proper annualization factors:
- **Daily bars**: √252 ≈ 15.87
- **Minute bars**: √(252 × 390) ≈ 313.5
- **Issue**: Using √252 on minute data understates Sharpe ratio by ~20×

### Production Readiness
This implementation focuses on **algorithmic enhancement** rather than production deployment. For live trading, additional fixes would be needed:
- CF-1: Walk-forward selection bias
- CF-2: Embargo gap enforcement  
- CF-4: Normalization persistence
- HI-1: Risk model integration
- Redis fail-safe mechanisms

## 🔮 Next Steps

1. **Add More Alpha Sources**: Sentiment analysis, options flow, macro indicators
2. **Improve Portfolio Construction**: Black-Litterman model, sector constraints
3. **Enhanced Execution**: Implement VWAP algorithms, smart order routing
4. **Live Trading Integration**: Alpaca API, risk controls, monitoring
5. **MLflow Integration**: Experiment tracking, model versioning
6. **Grafana Dashboards**: Real-time signal monitoring, performance tracking

## 📚 References

- **APEX Documents**: See `/exter` folder for comprehensive architecture blueprints
- **QuantConnect LEAN**: https://github.com/QuantConnect/Lean
- **Algorithm Framework**: https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework

## 🚀 Getting Started Examples

### Run the Ensemble Algorithm
```bash
./apex run ensemble
# ✅ Multi-indicator signals with ATR sizing
# 📊 Enhanced metrics and regime detection
# 🌐 Serves report on http://localhost:8766
```

### Compare All Strategies
```bash
./apex run sma        # Baseline SMA crossover
./apex run ensemble   # Enhanced multi-indicator
./apex run framework  # Advanced framework approach
```

### Start the Signal API
```bash
./apex run framework --start-api
# 🚀 Runs backtest + starts API on :8000
# 📊 View signals: ./apex signals
# 🩺 Check health: ./apex status
```

---

## 🎉 Summary

APEX transforms a basic SMA crossover into a sophisticated multi-signal trading platform with:
- **5× more signals** (RSI, EMA, MACD, Stochastic, SMA)
- **Risk-adjusted position sizing** (ATR-based)
- **Intelligent ensemble weighting** (Bayesian updates)
- **Professional execution** (VWAP models)
- **Real-time signal API** (FastAPI + SQLite)
- **Enhanced analytics** (Beyond QuantConnect defaults)

Ready to **deploy, backtest, and iterate** on sophisticated algorithmic trading strategies! 🚀