# APEX System — Complete Tutorial (Parts 1–7)

A full deep-dive into the APEX trading system from first principles.  
Covers quant finance, machine learning, infrastructure, pipeline, model training,
production design, and the Twitter/TFT sentiment pipeline — with real code, real
numbers, ASCII diagrams, and gotcha callouts throughout.

---

## Table of Contents

- [Part 1 — Quant Finance Foundations](#part-1--quant-finance-foundations)
- [Part 2 — ML Models Explained](#part-2--ml-models-explained)
- [Part 3 — Infrastructure Explained](#part-3--infrastructure-explained)
- [Part 4 — Pipeline End to End](#part-4--pipeline-end-to-end)
- [Part 5 — Model Training Deep Dive](#part-5--model-training-deep-dive)
- [Part 6 — Production System Design](#part-6--production-system-design)
- [Part 7 — Twitter/TFT Sentiment Pipeline](#part-7--twittertft-sentiment-pipeline)

---

# Part 1 — Quant Finance Foundations

## 1.1 What is Algorithmic Trading?

### Simple version (age 12)
Imagine you had a robot that watched the stock market 24/7, spotted patterns that humans always miss, and placed buy/sell orders in milliseconds. That's algorithmic trading. Instead of a human saying "I *feel* like Apple stock will go up," a program says "historical data shows that when these 21 specific conditions align, Apple goes up 53% of the time in the next 15 minutes — so buy."

### Technical version
Algorithmic trading is the systematic execution of a trading strategy using automated rules derived from quantitative analysis. The strategy is a function of observable market data (prices, volume, news, social media) that outputs a position decision (long, short, flat) at each time step.

The fundamental claim of quantitative trading is that **markets are not perfectly efficient** — meaning prices don't always instantly reflect all available information. Edges exist because:

1. **Behavioral inefficiency** — humans are emotional; panic selling and FOMO buying create predictable patterns
2. **Structural inefficiency** — market rules (circuit breakers, settlement delays, index rebalancing) create mechanical price dislocations
3. **Information processing lag** — not all participants can process all information equally fast

---

## 1.2 Alpha, Sharpe Ratio, Hit Rate, Drawdown

### Alpha

**Simple:** Alpha is extra profit that isn't just because the whole market went up. If the market goes up 10% and your strategy goes up 15%, your alpha is approximately 5%. If the market goes up 10% and your strategy goes up 9%, your alpha is approximately -1%.

**Technical:** From CAPM (Capital Asset Pricing Model):

```
R_strategy = α + β × R_market + ε

R_strategy  = your strategy's return
R_market    = market return (S&P 500 proxy)
β           = your exposure to market risk
α (alpha)   = excess return unexplained by market beta
ε           = random noise
```

Alpha is the intercept term. Positive alpha means your strategy generates returns **independent of** market direction. This is what every quant is hunting.

---

### Sharpe Ratio

**Simple:** The Sharpe ratio answers: "How much reward do I get per unit of risk?" A strategy that makes 20% per year with very smooth gains is better than one that makes 20% but swings wildly up and down. Sharpe measures smoothness-adjusted returns. Higher is better. Above 1.0 is decent. Above 2.0 is very good. Above 3.0 is elite.

**Technical:**

```
Sharpe = (R̄ - Rf) / σ × √(ann_factor)

R̄         = mean return per bar
Rf         = risk-free rate (T-bill rate, ~5% annual → ~0.0013% per minute)
σ          = standard deviation of returns per bar
ann_factor = trading periods per year (annualizes the ratio)
```

**APEX-specific (minute bars):**

```
ann_factor = √(252 trading days × 390 minutes per day)
           = √98,280
           ≈ 313.5

Common mistake (CF-3 bug): using √252 = 15.87 instead
→ understates Sharpe by a factor of 313.5 / 15.87 ≈ 20×
→ ENS_v4 would show Sharpe 0.77 instead of 15.44 if CF-3 not fixed
```

---

### Hit Rate

**Simple:** Out of 100 trades, what percentage are winners? If 55 are winners and 45 are losers, your hit rate is 55%.

**Technical:** Hit rate alone is meaningless without **payoff ratio**:

```
Payoff ratio = avg_win / avg_loss

Expected value = (hit_rate × avg_win) - (1 - hit_rate) × avg_loss

Strategy A: hit_rate=60%, avg_win=$50, avg_loss=$100
  EV = 0.60×50 - 0.40×100 = 30 - 40 = -$10  ← LOSING despite 60% hit rate

Strategy B: hit_rate=40%, avg_win=$200, avg_loss=$50
  EV = 0.40×200 - 0.60×50 = 80 - 30 = +$50  ← WINNING despite 40% hit rate
```

**ENS_v4:** hit_rate = 51.73% with avg_win/loss ratio ≈ 1.4 → positive expected value.

---

### Drawdown

**Simple:** Drawdown is how far you fell from your best point. If your account grew to $110,000 and then fell to $95,000, your drawdown is $15,000 or 13.6%.

**Technical:**

```
Drawdown(t) = (Peak_equity_up_to_t - Current_equity_t) / Peak_equity_up_to_t

Max Drawdown = max over all t of Drawdown(t)

Example:
  Day 1:  $100,000  ← new peak
  Day 5:  $112,000  ← new peak
  Day 8:  $98,000   ← drawdown = ($112k - $98k) / $112k = 12.5%
  Day 10: $115,000  ← new peak, drawdown resets to 0
  Day 12: $105,000  ← drawdown = ($115k - $105k) / $115k = 8.7%
  Max drawdown = 12.5%
```

APEX limit: 3% **daily** loss limit (not the same as max drawdown — triggers kill switch intraday).

---

## 1.3 Signals, Features, and Labels

These three words are used constantly. Know the difference cold.

```
RAW MARKET DATA (prices, volume, tweets)
    │
    │  feature engineering
    ▼
FEATURES — derived quantities computed from raw data
    │
    │  signal logic (threshold, crossover, model output)
    ▼
SIGNAL — directional opinion: BUY / SELL / HOLD
    │
    │  risk management
    ▼
POSITION — actual shares held


LABEL — the "right answer" used during training
         = what actually happened after the signal
         e.g., fwd_return_15min > 0.05% → label = 1 (UP)
              fwd_return_15min < 0.00% → label = 0 (DOWN)
```

**Real APEX example:**

```
Raw data:    AAPL close=[449.80, 450.10, 450.85, 451.20, 450.60]
Feature:     RSI_14 = 67.3  (computed from close prices)
Signal:      RSI crosses above 65 → BUY signal (confidence=0.72)
Label:       Did AAPL go up 5+ bps in next 15 min? YES → label=1

At training time: we have labels (historical data, we know what happened)
At inference time: we don't have labels (future unknown), so signal IS our bet
```

---

## 1.4 Five Market Pattern Sources

APEX exploits five distinct pattern categories:

| Pattern | What it is | APEX signal |
|---|---|---|
| **Momentum** | Stocks going up tend to keep going up (short term) | EMA crossover, MACD histogram |
| **Mean reversion** | After extreme moves, prices revert toward average | RSI overbought/oversold, Bollinger Band %B |
| **Volume confirmation** | Price moves on high volume are more reliable | volume_ratio > 1.5 amplifies signal |
| **Volatility regimes** | Low-volatility periods → mean reversion works better; high-volatility → momentum works better | ATR_14 regime gate |
| **Sentiment** | Crowd opinion shifts price before it's fully reflected | Twitter/FinBERT weighted score |

---

## Quiz — Part 1

**Q1.** A strategy has a hit rate of 48% but an average win of $300 and average loss of $80. Is this strategy profitable? Show your calculation.

**Q2.** APEX uses `ann_factor = √(252×390)` for minute data. If you computed Sharpe using `√252` instead, and got Sharpe = 0.77, what would the correct value be?

**Q3.** ENS_v4 has Sharpe = 15.44. The S&P 500 has Sharpe ≈ 0.5. What does this mean? What should make you suspicious about a Sharpe of 15.44?

---

# Part 2 — ML Models Explained

## 2.1 XGBoost

### Simple version
XGBoost is like a team of 500 very simple decision trees. Each tree is weak on its own — like asking 500 different friends "will this stock go up?" None of them is very good alone. But each new tree looks at where all the previous trees were **wrong** and tries specifically to fix those mistakes. After 500 trees, the combined answer is surprisingly accurate.

### Technical version

**Gradient Boosting** is an ensemble method that builds trees sequentially, each fitting the **residual errors** of the previous ensemble:

```
Tree 1: predicts raw signal
  Prediction: [0.6, 0.4, 0.7, 0.3, 0.5]
  True label: [1,   0,   1,   0,   1  ]
  Residuals:  [0.4, 0.4, 0.3, 0.3, 0.5]  ← where it was wrong

Tree 2: trained specifically on residuals
  Adds corrections to Tree 1's predictions

Tree 3: trained on Tree1+Tree2 residuals
  ...

Final = Tree1 + η×Tree2 + η²×Tree3 + ...   (η = learning rate = 0.05 in APEX)
```

**XGB-specific advantages:**
- Column subsampling: each tree sees random subset of features → reduces overfitting
- L1/L2 regularization: penalizes complex trees
- Histogram-based splitting: faster than exhaustive split search
- Handles missing values natively (unlike LSTM)

**APEX XGBoost config:**
```
n_estimators = 500
max_depth    = 6        (max tree depth — controls complexity)
learning_rate = 0.05
subsample    = 0.8      (80% of rows per tree)
colsample    = 0.8      (80% of features per tree)
Input:       21 features × current bar (no temporal memory)
Output:      P(up) ∈ [0, 1]
```

**Weakness:** XGBoost sees only the current bar snapshot. It has no memory of what happened 30 bars ago. That's LSTM's job.

---

## 2.2 LSTM (Long Short-Term Memory)

### Simple version
Imagine you're reading a mystery novel. To understand the clue on page 200, you need to remember something that happened on page 12. A regular neural network forgets page 12 by the time it gets to page 200. LSTM has a special "memory cell" that can choose what to remember and what to forget as it reads the book.

### Technical version

LSTM solves the vanishing gradient problem in standard RNNs. It has three gates controlling information flow:

```
Input sequence: [bar_t-31, bar_t-30, ..., bar_t-1, bar_t]
                Each bar = 21 features

For each timestep, 3 gates operate:

FORGET GATE:  f_t = σ(W_f × [h_{t-1}, x_t] + b_f)
  "How much of the previous cell state should I keep?"
  σ → value between 0 (forget everything) and 1 (keep everything)

INPUT GATE:   i_t = σ(W_i × [h_{t-1}, x_t] + b_i)
  g_t = tanh(W_g × [h_{t-1}, x_t] + b_g)
  "What new information should I add to the cell state?"

OUTPUT GATE:  o_t = σ(W_o × [h_{t-1}, x_t] + b_o)
  h_t = o_t × tanh(C_t)
  "What should I output based on the cell state?"

Cell state update:
  C_t = f_t × C_{t-1} + i_t × g_t
        ↑ forget old    ↑ add new
```

**What LSTM learns in APEX:**

```
Scenario: AAPL trending up for 20 bars on increasing volume

Bar t-20: RSI=52, volume_ratio=1.1
Bar t-15: RSI=58, volume_ratio=1.3
Bar t-10: RSI=64, volume_ratio=1.6
Bar t-5:  RSI=69, volume_ratio=2.1
Bar t:    RSI=71, volume_ratio=2.4

LSTM cell state remembers: "RSI and volume have been increasing for 20 bars"
This TEMPORAL ACCELERATION pattern → stronger BUY signal
XGBoost at bar t only sees RSI=71 — misses the acceleration context
```

**APEX LSTM config:**
```
Input shape:  [batch, 32, 21]  (32 bar lookback, 21 features)
Architecture: 2× LSTM layers (hidden=128), dropout=0.3
Output:       scalar logit → sigmoid → P(up)
Class:        ApexLSTM in services/model_training/train_lstm.py
```

---

## 2.3 TimesFM (Foundation Model)

### Simple version
ChatGPT was trained on billions of web pages and can answer questions about almost anything. TimesFM did the same but with billions of time series (electricity usage, web traffic, stock prices) instead of text. When you ask it to predict a new time series, it already "knows" patterns from those billions of examples — even without being retrained on your specific data.

### Technical version

TimesFM is a **decoder-only transformer** for time series, released by Google Research (2024). Key innovations:

```
PATCH-BASED INPUT:
  Instead of processing bar-by-bar, it chunks the series into patches:
  
  Raw:     [p1, p2, p3, ..., p32]   (32 bars)
  Patches: [p1-p8 | p9-p16 | p17-p24 | p25-p32]  (4 patches of 8)
  
  Each patch is embedded as a single token → 4 tokens input
  (vs 32 tokens for bar-by-bar → 8× faster attention computation)

ZERO-SHOT CAPABILITY:
  Pre-trained on 100B+ time series data points
  Can forecast AAPL minutes bars without retraining on AAPL specifically
  "Transfer knowledge" from patterns seen in other time series

OUTPUT: Quantile predictions P10, P50, P90
  P50 = median forecast
  P90-P10 = uncertainty band  → if wide, signal is less reliable
```

**Weakness:** TimesFM is univariate by design. It processes ONE series at a time. It can't see RSI while predicting price. The ensemble meta-learner compensates by combining TimesFM's price forecast with XGB/LSTM's feature-aware predictions.

---

## 2.4 The Ensemble (ENS_v4)

### Simple version
You're trying to decide if it will rain tomorrow. You ask three friends: a meteorologist (XGBoost — knows many facts, no time memory), a historian who's tracked weather for years (LSTM — has temporal memory), and a world-traveler who's seen weather patterns globally (TimesFM — pre-trained on global patterns). A fourth smart person (the meta-learner) asks all three and combines their answers, giving more weight to whoever has been right recently.

### Technical version

**Stacking** (the technique ENS_v4 uses):

```
LEVEL 0 — Base models (run in parallel):
  XGBoost   → prediction p_xgb  ∈ [0,1]
  LSTM      → prediction p_lstm ∈ [0,1]
  TimesFM   → prediction p_tfm  ∈ [0,1]

LEVEL 1 — Meta-learner (logistic regression on base predictions):
  Input:  [p_xgb, p_lstm, p_tfm, p_xgb×p_lstm, p_lstm×p_tfm, p_xgb²]
  Output: final_confidence ∈ [0,1]
  
  The cross-terms (p_xgb×p_lstm) let the meta-learner detect
  "when XGB and LSTM agree, that's extra strong signal"
```

**Why stacking beats individual models:**

```
Error decorrelation:
  When XGB is wrong, LSTM might be right (they see different things)
  When LSTM is wrong, TimesFM might compensate (different architecture)
  
  If errors are uncorrelated with correlation ρ:
  σ_ensemble² ≈ σ² × (1 + (n-1)×ρ) / n
  
  3 models, ρ≈0.3:
  σ_ensemble² ≈ σ² × (1 + 2×0.3) / 3 = σ² × 0.53
  → 47% reduction in variance → Sharpe improves by 1/√0.53 ≈ 1.37×
```

**ENS_v4 prediction flow (T+0 to signal emit):**

```
T+0ms    Bar closes (e.g., AAPL 14:32)
T+5ms    data_ingestion publishes to market.raw Kafka topic
T+20ms   feature_engineering computes 21 features, publishes to market.engineered
T+40ms   lean-alpha: RSI/EMA/MACD signals computed
T+80ms   XGBoost inference: 21 features → p_xgb
T+85ms   LSTM inference: [32×21] tensor → p_lstm
T+120ms  TimesFM: HTTP call to tft-service:8009 → p_tfm
T+180ms  Meta-learner: [p_xgb, p_lstm, p_tfm, cross_terms] → confidence
T+200ms  signal_engine publishes to alpha.signals
T+250ms  risk_engine: 5 checks → risk.approved
T+300ms  execution: Alpaca order submitted
```

**ENS_v4 vs ENS_v3 (+0.26 Sharpe):**
The root cause was GPU→CPU batch fallback during TimesFM inference. When the GPU wasn't available, TimesFM silently switched to CPU with a different batch size. This changed the normalization inside the model (batch norm statistics differ between batch sizes), degrading p_tfm calibration. The meta-learner was trained on well-calibrated p_tfm values and received miscalibrated ones in production. Fix: pinned CPU inference with explicit batch_size=1 for consistency.

---

## Quiz — Part 2

**Q1.** XGBoost and LSTM both receive the same 21 features at bar t. LSTM also has 31 prior bars of context. For which market pattern (from Part 1) would LSTM have the strongest advantage over XGBoost, and why?

**Q2.** ENS_v4's meta-learner receives inputs including `p_xgb × p_lstm`. In plain English, what market situation does a high value of this cross-term represent, and why is it more informative than just `p_xgb` and `p_lstm` separately?

**Q3.** TimesFM is a zero-shot model. What does zero-shot mean in this context? What is the trade-off vs fine-tuning it on AAPL specifically?

---

# Part 3 — Infrastructure Explained

## 3.1 The Full Infrastructure Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                    APEX Infrastructure                              │
│                                                                     │
│  Data Sources          Message Bus         Service Layer            │
│  ─────────────         ───────────         ─────────────            │
│  Polygon API      →    Kafka :9092    ←→   data_ingestion  :8001    │
│  yfinance             Topics:             feature_eng     :8002    │
│  Twitter/X            market.raw          lean-alpha      :8014    │
│                        market.engineered  signal_engine   :8015    │
│                        alpha.signals      tft-service     :8009    │
│                        signals.scored     risk-engine     :8004    │
│                        risk.approved      execution       :8005    │
│                                           exit_monitor    :8006    │
│  Storage               Observability      Supporting               │
│  ──────────            ─────────────      ──────────               │
│  TimescaleDB :15432    Prometheus :9090   mlflow          :5001    │
│  Redis       :16379    Grafana    :3000   pos-reconciler  internal │
│                        Dashboard  :3001   model_manager   internal │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3.2 Kafka — The Message Bus

### Simple version
Kafka is a conveyor belt in a factory. The data ingestion machine puts raw bars on the belt. The feature engineering machine picks them up, processes them, and puts engineered features on a second belt. The ML model picks those up. Each machine works at its own pace — if feature engineering is slow, bars queue on the belt rather than getting lost.

### Technical version

Kafka is a distributed log. Producers append messages to the end. Consumers read from an offset and advance it when done.

```
TOPIC: market.raw
Partition 0: |bar_1|bar_2|bar_3|bar_4|bar_5|bar_6|...
                                      ↑
                              consumer offset
                              (feature_engineering has read up to bar_4)

Consumer group: feature_engineering
  → offset 4 → reads bar_5, processes, commits offset 5
  → offset 5 → reads bar_6, processes, commits offset 6
```

**Bug-B: Kafka auto-commit danger:**

```
BROKEN (auto-commit=True):
  1. Consumer reads bar_5 from Kafka
  2. AUTO-COMMIT fires: offset advances to 6  ← committed immediately
  3. Service crashes during bar_5 processing
  4. Service restarts → reads from offset 6
  5. bar_5 is PERMANENTLY LOST

FIXED (auto-commit=False + manual commit):
  1. Consumer reads bar_5
  2. Process bar_5 (compute features)
  3. Publish to market.engineered
  4. ONLY NOW: consumer.commit()  ← offset advances to 6
  5. Service crashes → restarts → re-reads bar_5 → safe
```

**CF-7: flush before commit:**

```python
# WRONG (CF-7 before fix):
consumer.commit()          # mark bar as done
producer.flush()           # actually send downstream message
# If crash between commit and flush: bar committed but message never sent

# CORRECT (CF-7 fix):
producer.flush()           # ensure downstream message is delivered
consumer.commit()          # only then mark bar as consumed
```

**APEX Kafka topics flow:**
```
market.raw         (raw OHLCV from Polygon)
    ↓
market.engineered  (21 features computed)
    ↓
alpha.signals      (per-model directional signal)
    ↓
signals.scored     (ensemble combined score)
    ↓
risk.approved      (passed risk checks → submit order)
```

---

## 3.3 Redis — Four Roles

Redis is an in-memory key-value store running at `:16379`. APEX uses it for four distinct purposes:

### Role 1 — Model Registry
```
apex:models:all           → SET of all model IDs
apex:models:ENS_v4        → JSON blob with model metadata
apex:models:LSTM_v4       → JSON blob

JSON structure:
{
  "model_id": "ENS_v4",
  "model_type": "ensemble",
  "status": "live",
  "val_sharpe": 15.44,
  "val_hit_rate": 0.5173,
  "registered_at": "2026-02-28T14:23:11Z",
  "artifact_path": "/tmp/apex_models/ENS_v4/"
}
```

### Role 2 — Kill Switch (CF-6)
```
apex:kill_switch    → "1" = halt trading, missing/0 = trade

FAIL-CLOSED logic (CF-6 fix):
  try:
      val = redis.get("apex:kill_switch")
      return val != b"1"      # True = safe to trade
  except RedisError:
      return False             # Redis unreachable → HALT (fail-closed)
```

### Role 3 — Position State
```
apex:positions:AAPL  → {"shares": 100, "avg_price": 450.20, "entry_time": ...}
apex:positions:NVDA  → {"shares": 0, "avg_price": null}
```

### Role 4 — Bloom Filter (dedup)
```
apex:bloom:tweets    → probabilistic set of seen tweet content_hashes
                       Prevents same tweet being scored twice
                       1% false positive rate (can miss a duplicate)
                       False negatives: impossible (never marks unseen as seen)
```

**HI-8 fix — Redis persistence and healthcheck:**
```yaml
redis:
  command: redis-server --appendonly yes   # AOF: every write persisted to disk
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    retries: 3
```

---

## 3.4 TimescaleDB — Time-Series Database

TimescaleDB is PostgreSQL with time-series superpowers running at `:15432`.

**Tables:**

```
ohlcv              (symbol, time, open, high, low, close, volume)
features           (symbol, time, returns_1, rsi_14, ... 21 columns)
signals            (id, symbol, time, model, confidence, direction)
positions          (id, symbol, entry_time, exit_time, pnl, ...)
reddit_sentiment   (symbol, time, score, post_count, ...)
```

**Hypertables** — TimescaleDB auto-partitions by time:
```sql
-- Instead of one giant table, TimescaleDB creates "chunks"
SELECT create_hypertable('ohlcv', 'time', chunk_time_interval => INTERVAL '7 days');

Result:
  ohlcv_2024_01 (Jan 2024 data)
  ohlcv_2024_02 (Feb 2024 data)
  ...
  
Query: SELECT * FROM ohlcv WHERE time > NOW() - INTERVAL '1 day'
  → Only reads latest chunk (fast) instead of full table (slow)
```

**Continuous aggregates** — pre-computed OHLCV resolutions:
```sql
CREATE MATERIALIZED VIEW ohlcv_5m
WITH (timescaledb.continuous) AS
SELECT   symbol,
         time_bucket('5 minutes', time) AS bucket,
         first(open, time)  AS open,
         max(high)          AS high,
         min(low)           AS low,
         last(close, time)  AS close,
         sum(volume)        AS volume
FROM ohlcv GROUP BY symbol, bucket;

-- Same for 15m and 1h views
-- TimescaleDB refreshes incrementally (only new bars recomputed)
```

**MD-2 fix — retention policy:**
```sql
SELECT add_retention_policy('ohlcv', INTERVAL '2 years');
-- Automatically drops chunks older than 2 years
-- Prevents unbounded disk growth
```

---

## 3.5 MLflow — Experiment Tracking

MLflow runs at `:5001`. Every training run is recorded:

```
Experiment: apex_ensemble_training
└── Run: ENS_v4  (run_id: abc123)
    ├── Parameters
    │   ├── n_folds: 4
    │   ├── embargo_bars: 180
    │   ├── xgb_id: XGB_v2
    │   ├── lstm_id: LSTM_v4
    │   └── tft_id: TFT_v3
    ├── Metrics
    │   ├── val_sharpe: 15.44
    │   ├── val_hit_rate: 0.5173
    │   ├── fold_1_sharpe: 13.13
    │   ├── fold_2_sharpe: 16.34
    │   ├── fold_3_sharpe: 16.52
    │   └── fold_4_sharpe: 15.79
    ├── Tags
    │   ├── production_ready: true
    │   └── promoted_at: 2026-02-28T16:00:00Z
    └── Artifacts
        ├── fold_4/
        │   ├── meta_learner.pkl
        │   ├── scaler.pkl
        │   └── scaler_params.json
        └── feature_importance.json
```

---

## 3.6 Prometheus + Grafana — Observability

Prometheus (`:9090`) scrapes metrics from every service's `/metrics` endpoint every 15s.

**Key metric: HI-6 — PipelineStale alert:**
```yaml
# infra/prometheus/alerts.yml
- alert: PipelineStale
  expr: |
    (time() - apex_last_signal_timestamp) > 180
    AND
    (hour() >= 14 AND hour() <= 21)
    AND
    (day_of_week() >= 1 AND day_of_week() <= 5)
  for: 2m
  severity: critical
```

Before HI-6 fix: alert fired every night and weekend (1,440 false alerts per weekend).
After fix: alert only fires during market hours — zero alert fatigue.

---

## Quiz — Part 3

**Q1.** Redis stores model weights in RAM. What happens to all model metadata if you `docker compose down` and `docker compose up` without the AOF fix? How does `--appendonly yes` solve this?

**Q2.** Kafka consumer lag is at 847 messages on the `market.engineered` topic. What does this mean in plain terms for trade timeliness? At 1 bar per second ingestion rate, how stale would your signals be?

**Q3.** Why does TimescaleDB's chunking make a `WHERE time > NOW() - INTERVAL '1 day'` query dramatically faster than a standard PostgreSQL table would be?

---

# Part 4 — Pipeline End to End

## 4.1 Full Bar-to-Trade Journey

Walk-through for AAPL at 14:32 UTC on a trading day:

```
T+0ms     AAPL bar closes (14:32:00 UTC)
           open=450.10, high=451.20, low=449.80, close=450.85, volume=42,300

T+5ms     data_ingestion:8001
           Publishes JSON to Kafka topic: market.raw
           {symbol: AAPL, time: 14:32:00, ohlcv: {...}}
           Also writes to TimescaleDB ohlcv table

T+20ms    feature_engineering:8002
           Reads from market.raw
           Computes 21 features (see section 4.2)
           Publishes to market.engineered

T+40ms    lean-alpha:8014
           Reads from market.engineered
           Runs 6 standalone alpha modules:
             RSI signal:       RSI=67 → mild bullish (weight 0.15)
             EMA cross:        EMA20 > EMA50 → bullish (weight 0.20)
             MACD cross:       MACD crosses signal → bullish (weight 0.20)
             Stochastic:       %K crosses %D upward → weak bullish (0.10)
             Volume confirm:   vol_ratio=1.8 → amplify signal
             Sentiment:        Twitter score=+0.63 → bullish (0.15)

T+80ms    XGBoost inference
           Input: [returns_1=0.0017, rsi_14=67, ema_20=0.9987, ...]
           Output: p_xgb = 0.68

T+85ms    LSTM inference
           Input: tensor[32, 21] (last 32 bars × 21 features)
           Output: p_lstm = 0.72

T+120ms   TimesFM inference (HTTP to tft-service:8009)
           Input: last 32 close prices
           Output: p_tfm = 0.61

T+180ms   Meta-learner (ENS_v4)
           Input: [0.68, 0.72, 0.61, 0.68×0.72=0.49, 0.72×0.61=0.44, 0.68²=0.46]
           Output: final_confidence = 0.71

T+200ms   signal_engine:8015
           Publishes to signals.scored:
           {symbol: AAPL, direction: LONG, confidence: 0.71, time: 14:32:00}

T+250ms   risk_engine:8004
           Runs 5 checks (see section 4.3)
           All pass → publishes to risk.approved

T+300ms   execution:8005
           Reads from risk.approved
           Calls Alpaca paper API: POST /v2/orders
           {symbol: AAPL, qty: 44, side: buy, type: market}
           (44 shares = 2% position cap on $100k account / $450 price)
```

---

## 4.2 All 21 Features Explained

Computed by `feature_engineering:8002` from raw OHLCV:

```
RETURN FEATURES (what the price did)
  returns_1     close/close[t-1] - 1         1-bar return
  returns_5     close/close[t-5] - 1         5-bar return
  returns_15    close/close[t-15] - 1        15-bar return
  returns_60    close/close[t-60] - 1        60-bar return

TREND / MOMENTUM FEATURES
  rsi_14        RSI 14-period                overbought/oversold
  rsi_28        RSI 28-period                longer-term RSI
  ema_20        close / EMA(20) - 1          price vs short EMA
  ema_50        close / EMA(50) - 1          price vs medium EMA
  ema_200       close / EMA(200) - 1         price vs long EMA

MACD FEATURES
  macd          EMA(12) - EMA(26)            MACD line
  macd_signal   EMA(9) of macd               signal line
  macd_hist     macd - macd_signal           histogram (momentum of momentum)

VOLATILITY FEATURES
  bb_upper      (close - BB_upper) / ATR     position relative to upper band
  bb_lower      (close - BB_lower) / ATR     position relative to lower band
  bb_pct        (close - BB_lower) / (BB_upper - BB_lower)  %B oscillator
  atr_14        ATR(14) / close              normalized true range
  adx_14        ADX(14)                      trend strength (0=choppy, 100=strong)

OSCILLATOR FEATURES
  stoch_k       Stochastic %K                fast line (0-100)
  stoch_d       Stochastic %D                slow line = SMA(3) of %K

VOLUME FEATURES
  volume_ratio  volume / rolling_avg_volume(20)   unusual volume spike
  vwap_dev      (close - VWAP) / close            price vs volume-weighted avg
```

---

## 4.3 Risk Engine — Five Check Decision Tree

```
Signal arrives: AAPL LONG, confidence=0.71

CHECK 1: Kill Switch
  redis.get("apex:kill_switch") == "1"?
  NO → continue
  YES (or Redis unreachable) → REJECT "kill switch active"

CHECK 2: Daily Loss Limit
  daily_pnl < -3% of account value?
  NO ($0 loss today) → continue
  YES → REJECT "daily loss limit exceeded"

CHECK 3: Position Sizing
  current_position_value / account_value > 2%?
  NO (AAPL is flat) → continue (size = 2% max = 44 shares)
  YES → REJECT "position limit reached"

CHECK 4: CVaR (Conditional Value at Risk)
  Historical simulation on last 252 days:
    Sort daily returns from worst to best
    CVaR = mean of worst 5% = E[loss | loss > VaR_95]
  CVaR > 5% → REJECT "CVaR limit exceeded"
  CVaR = 1.8% → continue

CHECK 5: Minimum Confidence
  confidence > 0.55 threshold?
  YES (0.71 > 0.55) → continue
  NO → REJECT "confidence below threshold"

ALL CHECKS PASSED → publish to risk.approved
```

**CF-5 fix — historical CVaR vs Gaussian:**
```
WRONG (Gaussian CVaR):
  Assumes returns are normally distributed
  Real market returns have FAT TAILS
  Oct 1987: -22% in one day = 22-sigma event under Gaussian
  Gaussian CVaR would say this is "practically impossible"
  → Severely underestimates real tail risk

CORRECT (Historical simulation):
  Sort last 252 days of actual P&L values
  Worst 5% = 12.6 days
  CVaR = average loss of those 12.6 worst days
  Fully captures actual fat tails (no distribution assumption)
```

---

## 4.4 Exit Monitor and Position Reconciler

**Exit monitor** (`exit_monitor:8006`) runs every 30 seconds:
```python
for symbol, position in open_positions.items():
    current_price = get_latest_price(symbol)
    entry_price = position["avg_price"]
    
    pnl_pct = (current_price - entry_price) / entry_price
    hold_time = now() - position["entry_time"]
    
    # Take profit
    if pnl_pct > take_profit_pct:          # default: +0.5%
        submit_exit_order(symbol, "take_profit")
    
    # Stop loss
    elif pnl_pct < -stop_loss_pct:         # default: -0.25%
        submit_exit_order(symbol, "stop_loss")
    
    # Time-based exit
    elif hold_time > max_hold_time:        # default: 30 minutes
        submit_exit_order(symbol, "timeout")
```

**Position reconciler** runs every 60 seconds:
```
Problem: Redis says 100 AAPL shares. Alpaca reports 73.
Cause:   Partial fill (only 73 of 100 shares filled by market)

Reconciler:
  redis_pos = redis.get("apex:positions:AAPL")  → 100 shares
  alpaca_pos = alpaca_client.get_position("AAPL")  → 73 shares
  drift = abs(100 - 73) = 27 shares

  if drift > 10:
      log WARNING "AAPL position drift: redis=100, alpaca=73"
      redis.set("apex:positions:AAPL", 73)   # correct Redis state
```

---

## Quiz — Part 4

**Q1.** At T+120ms, TimesFM returns a prediction. At T+180ms, the meta-learner runs. What specifically does the meta-learner do with all three predictions that's more powerful than simply averaging them?

**Q2.** The exit monitor runs every 30 seconds. A stock gaps down 3% in 1 second on breaking news. The stop loss is 0.25%. What happens between T=0 (the gap) and T=30s (next exit monitor poll), and is this a problem?

**Q3.** Risk check 4 (CVaR) calculates the average of the worst 5% of historical daily returns. If your last 252 days had 12 days where daily loss exceeded 1%, what would CVaR = 1.8% mean, and why would CVaR = 4.9% trigger a rejection?

---

# Part 5 — Model Training Deep Dive

## 5.1 The Three Types of Data Leakage

Leakage is when information from the future accidentally enters your training data. The model learns to "cheat" on backtest but fails completely live.

### Type 1 — Label Leakage
```
EXAMPLE:
  You're predicting "will AAPL go up in the next 15 min?"
  Label = 1 if close[t+15] > close[t]
  
  LEAKED: You accidentally include close[t+15] as a FEATURE
  Model learns: "if close[t+15] > close[t], predict 1" → 100% accuracy!
  But close[t+15] doesn't exist at inference time → model is useless

FIX: All features must use data available at time t or earlier.
     Labels are strictly future data (only exist at training time).
```

### Type 2 — Scaler Leakage (CF-4)
```
EXAMPLE:
  You normalize features: x_normalized = (x - mean) / std
  
  LEAKED: You compute mean and std using ALL data (including future bars)
    x = [1, 2, 3, 100, 200, 300]   ← 100,200,300 are future OOS data
    mean = 101.0, std = 118.0
    
  At inference: mean and std aren't known yet!
  Features at training time are normalized differently than at inference
  → Model performance degrades in production
  
FIX (CF-4): Fit StandardScaler ONLY on in-sample data.
            Save scaler params to JSON sidecar for production use.
```

### Type 3 — Temporal Contamination (CF-1, CF-2)
```
TYPE 3A — CF-1: Fold selection bias
  You train 4 folds, pick the one with highest Sharpe = max(fold_sharpes)
  This requires knowing future fold results to pick the "best"
  In production you only know the most recent fold → use that
  FIX: return folds[-1]  (most recent, not max-Sharpe)

TYPE 3B — CF-2: Embargo gap too short
  Autocorrelation in minute bars:
  If you use bar t in training, bars t+1 to t+N are highly correlated
  An OOS bar at t+1 "leaks" information from t
  
  Autocorrelation decay:
    lag 1:   ρ=0.62  (very correlated with bar t)
    lag 21:  ρ=0.24  (1 trading hour, still correlated)
    lag 100: ρ=0.11  (partial correlation)
    lag 180: ρ=0.03  (near zero — safe)
  
  FIX: EMBARGO_BARS = 180 (was 21 — barely scratched the surface)
```

---

## 5.2 Walk-Forward Validation

```
DATA TIMELINE (1 year of minute bars = 252×390 = 98,280 bars)

|──────── Jan ────────|─── Feb ───|─── Mar ───|─── Apr ───|
████████████████████████░░░░░░░░░░░                          Fold 1
████████████████████████████████████░░░░░░░░░░░              Fold 2
████████████████████████████████████████████████░░░░░░░░░░░  Fold 3
(similar for Fold 4)

████ = in-sample (training data)
░░░░ = out-of-sample (validation — never seen during training)
  Gap between ████ and ░░░░ = 180-bar embargo

Fold 1: IS = Jan–Feb, OOS = first 3 weeks Mar
Fold 2: IS = Jan–Mar, OOS = first 3 weeks Apr
Fold 3: IS = Jan–Apr, OOS = first 3 weeks May
Fold 4: IS = Jan–May, OOS = first 3 weeks Jun

ENS_v4 Results:
  Fold 1: OOS Sharpe = 13.13
  Fold 2: OOS Sharpe = 16.34
  Fold 3: OOS Sharpe = 16.52
  Fold 4: OOS Sharpe = 15.79  ← this is what goes live (folds[-1])
  Average:            = 15.44  ← reported as val_sharpe
```

---

## 5.3 FoldScaler — Preventing Scaler Leakage

```
CORRECT scaler workflow (CF-4 fix):

For each fold:
  scaler = StandardScaler()
  scaler.fit(X_in_sample)        # ONLY fit on IS data
  X_is_scaled  = scaler.transform(X_in_sample)
  X_oos_scaled = scaler.transform(X_out_of_sample)  # use IS params for OOS

  Save scaler params:
  {
    "feature_means": [0.0012, 52.3, 0.9987, ...],
    "feature_stds":  [0.0089, 14.2, 0.0142, ...]
  }
  → saved to /tmp/apex_models/ENS_v4/fold_4/scaler_params.json

Production inference:
  Load scaler_params.json
  x_normalized = (x_raw - feature_means) / feature_stds
  → Same normalization as training, no leakage
```

---

## 5.4 Model Lifecycle

```
STATES:
  pending  → model files exist but not validated
  staged   → passed validation, ready for promotion
  live     → actively serving predictions
  archived → superseded by newer model

PROMOTION WORKFLOW:
  train_ensemble.py → redis.set("apex:models:ENS_v4", {..., status="staged"})
  
  Manual review:
    - Check fold Sharpes are consistent (not one outlier fold)
    - Check hit rate > 50.5%
    - Check val_sharpe > previous live model
    
  Promote:
    redis.set("apex:models:ENS_v4", {..., status="live"})
    redis.set("apex:models:ENS_v3", {..., status="archived"})
    signal_engine loads new model on next heartbeat (60s)

Redis keys during transition:
  apex:models:live_model     = "ENS_v4"   ← signal_engine reads this
  apex:models:ENS_v4         = {status: "live", ...}
  apex:models:ENS_v3         = {status: "archived", ...}
```

---

## Quiz — Part 5

**Q1.** CF-2 sets embargo to 180 bars. At the minute bar resolution, how many trading hours is that? Why does the 21-bar embargo in v1 fail even though 1 trading hour seems long?

**Q2.** Walk-forward fold 2 has OOS Sharpe = 16.34 and fold 3 has 16.52. CF-1 says to use folds[-1] (fold 4 = 15.79) not max-Sharpe (fold 3). Could you argue that fold 3 data is "better quality" because it has higher Sharpe? What's wrong with that argument?

**Q3.** You retrain ENS_v4 six months later on new data and get val_sharpe = 11.2 (vs original 15.44). Before concluding the model is "worse," what three alternative explanations should you investigate first?

---

# Part 6 — Production System Design

## 6.1 Why Microservices? (Not a Monolith)

### Simple version
Imagine you're running a lemonade stand. You could do everything yourself — squeeze lemons, take money, make change, pour cups. If you get sick, the whole stand stops. OR you hire specialists: one person squeezes, one takes money, one pours. If the pourer quits, the others keep working. That's microservices.

### Technical version

A monolith runs all logic in one process. One bug, one memory leak, one blocked thread — everything dies.

```
MONOLITH (bad for trading)
┌─────────────────────────────────────────────────────┐
│  data intake + feature eng + ML inference           │
│  + risk checks + order execution + exit monitor     │
│  + model registry + dashboard + DB writes          │
│                                                     │
│  ← one OOM error kills ALL of this →               │
└─────────────────────────────────────────────────────┘

MICROSERVICES (APEX design)
┌──────────┐   Kafka   ┌──────────────┐   Kafka   ┌───────────┐
│data_ingest│ ────────▶ │feature_eng   │ ────────▶ │lean-alpha │
│  :8001   │           │  :8002       │           │  :8014    │
└──────────┘           └──────────────┘           └───────────┘
     │                                                  │ Kafka
     ▼                                             ┌────▼──────┐
TimescaleDB                                        │signal_eng │
  :15432                                           │  :8015    │
                                                   └────┬──────┘
                                                        │ Kafka
                                                   ┌────▼──────┐
                                                   │risk_engine│
                                                   │  :8004    │
                                                   └────┬──────┘
                                                        │ Kafka
                                                   ┌────▼──────┐
                                                   │ execution │
                                                   │  :8005    │
                                                   └───────────┘
```

| Property | Monolith | Microservices |
|---|---|---|
| One service crashes | Everything stops | Others keep running |
| Redeploy execution logic | Restart data ingestion too | Deploy only execution |
| Scale ML inference | Scale everything | Scale only inference pod |
| Memory leak in FinBERT | Kills risk engine | Only tft-service affected |

> **Gotcha:** Network latency adds up. Each service hop adds 0.5–2ms. APEX has 6 hops = 3–12ms of pure overhead. Fine for minute-bar strategies, fatal for HFT.

---

## 6.2 Healthchecks, depends_on, restart policies

Three layers of protection:

### Layer 1 — Healthcheck
```yaml
redis:
  image: redis:7-alpine
  command: redis-server --appendonly yes
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    timeout: 5s
    retries: 3
    start_period: 10s
```

```
Container state timeline:
  t=0s   container starts
  t=2s   process starts, port opens  ← Docker says "running"
  t=8s   DB accepting connections    ← healthcheck passes = "healthy"

Without healthcheck: dependent service starts at t=2s → connection refused → crash
With healthcheck:    dependent service starts at t=8s → connects fine
```

### Layer 2 — depends_on
```yaml
risk-engine:
  depends_on:
    redis:
      condition: service_healthy
    timescaledb:
      condition: service_healthy
```

```
Full APEX dependency graph:
redis ──────────┐
timescaledb ────┼──▶ data_ingestion
kafka ──────────┘
data_ingestion ─────▶ feature_engineering ─────▶ lean-alpha ─────▶ signal_engine
                                                                          │
redis & timescaledb ─────────────────────────────────────────▶ risk-engine
                                                                          │
                                                                    execution
                                                                          │
                                                                   exit_monitor
```

### Layer 3 — Restart policy
```yaml
services:
  risk-engine:
    restart: unless-stopped   # restart on crash, respect explicit docker stop
```

> **Gotcha — restart loops:** If a service crashes at startup due to a config error, `restart: unless-stopped` loops forever. Check `docker logs <container> --tail 20` when a service shows many restarts.

---

## 6.3 Monitoring Layers

### Layer 1 — Prometheus + Grafana (live metrics)
```
Services expose metrics on /metrics:

data_ingestion:8001/metrics  →  apex_bars_ingested_total
                                apex_kafka_lag
                                apex_last_bar_age_seconds

risk_engine:8004/metrics  →  apex_kill_switch_state
                              apex_trades_rejected_total
                              apex_cvar_value
```

**HI-6 — PipelineStale alert (market-hours gated):**
```yaml
- alert: PipelineStale
  expr: |
    (time() - apex_last_signal_timestamp) > 180
    AND (hour() >= 14 AND hour() <= 21)
    AND (day_of_week() >= 1 AND day_of_week() <= 5)
  for: 2m
  severity: critical

Before HI-6: 1,440 false alerts per weekend → alert fatigue → real alerts ignored
After HI-6:  silence on weekends, fire only when it matters
```

### Layer 2 — scripts/health_check.sh
```bash
check_kafka_lag() {
    LAG=$(docker exec infra-kafka-1 kafka-consumer-groups.sh \
        --bootstrap-server localhost:9092 \
        --describe --group apex-consumers \
        | awk '{sum += $6} END {print sum}')
    [ "$LAG" -lt 100 ] || alert "Kafka lag too high: $LAG"
}

check_timescaledb_freshness() {
    AGE=$(psql $DB_URL -c \
        "SELECT extract(epoch FROM now()-max(time)) FROM ohlcv WHERE symbol='AAPL'" \
        -t | tr -d ' ')
    [ "$AGE" -lt 120 ] || alert "TimescaleDB stale: ${AGE}s"
}
```

### Layer 3 — scripts/paper_trading_monitor.py (daily P&L review)
```
=== APEX Paper Trading Report — 2026-03-03 ===
Total trades:        47
Win rate:            53.2%    (target: >= 52%)
Avg hold time:       17.3 min
Daily P&L:           +$312.44
Worst drawdown:      -$89.21
Kill switch events:  0
Daily loss limit:    -$150 / -$3000 cap  → 5.0% used
Model weight drift:  XGB: +0.02  LSTM: -0.01  TFT: +0.00  (OK)
```

### Layer 4 — MLflow (model health)
```
http://localhost:5001
  └── Experiment: apex_ensemble_training
      └── Run: ENS_v4 (active)
          ├── val_sharpe: 15.44  ← if drops below 1.2, retrain alarm
          ├── val_hit_rate: 0.517 ← if below 0.50, coin flip
          └── artifacts/fold_4/
```

---

## 6.4 What Can Go Wrong in Production and How to Defend

### 1. Silent data staleness
```
Symptom: Trades placed on 45-minute-old prices.
Defense: TimescaleDB freshness check in health_check.sh
         apex_last_bar_age_seconds > 120 → critical alert
```

### 2. Kafka consumer lag explosion
```
Timeline:
  t=0     LAG = 0    (healthy)
  t=5min  LAG = 47   (feature_eng slow — investigate)
  t=10min LAG = 312  (alert fires)
  t=15min LAG = 891  (kill switch)

Defense: LAG > 100 → alert, LAG > 500 → kill switch
```

### 3. Redis kill switch unreachable (CF-6)
```python
try:
    kill_switch = redis.get("apex:kill_switch")
    if kill_switch and kill_switch == b"1":
        return False   # halt
    return True        # trade
except RedisError:
    logger.error("Redis unreachable — fail-closed")
    return False       # HALT on uncertainty, never default to trading
```

### 4. Partial fill position drift
```
redis_position = 100 shares
alpaca_actual  = 73 shares
drift          = 27 shares → position_reconciler corrects every 60s
```

### 5. Model registry corruption on Redis crash
```
Defense:
  1. AOF: every write persisted before ACK
  2. JSON schema validation on deserialization
  3. Fallback: if JSON invalid → promote previous model version
```

### 6. Alpaca API timeout — CF-8
```python
client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
try:
    response = await client.post(url, json=order)
except httpx.TimeoutException:
    logger.error(f"Alpaca timeout on {symbol} — skipping")
    return None   # miss the trade, never retry (double-fill risk)
```

### 7. Graceful shutdown hang — CF-9
```python
async def shutdown():
    try:
        await asyncio.wait_for(flush_kafka(), timeout=10.0)
        await asyncio.wait_for(close_positions(), timeout=15.0)
        await asyncio.wait_for(close_db(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Shutdown step timed out — forcing exit")
```

### Defense summary table
```
┌──────────────────┬─────────────────────────────┬─────────────────────────────┐
│ Failure Mode     │ How You Detect It            │ How You Defend It           │
├──────────────────┼─────────────────────────────┼─────────────────────────────┤
│ Stale data       │ PipelineStale alert          │ health_check.sh freshness   │
│ Kafka lag        │ LAG > 100 alert              │ Kill switch at LAG > 500    │
│ Redis down       │ Redis healthcheck fails      │ Fail-closed kill switch     │
│ Partial fills    │ Position drift > 10 shares   │ position_reconciler         │
│ Model corruption │ JSON parse error             │ AOF + schema validation     │
│ API timeout      │ httpx.TimeoutException       │ 30s timeout, no retry       │
│ Shutdown hang    │ SIGKILL from k8s             │ asyncio.wait_for(30s)       │
└──────────────────┴─────────────────────────────┴─────────────────────────────┘
```

---

## 6.5 The Kill Switch

```
When to use it:
  1. Market circuit breaker triggered (S&P down >7%)
  2. Daily loss limit hit (>3% daily P&L)
  3. Suspicious model behavior (win rate < 40% for 1 hour)
  4. Infrastructure anomaly (3+ service restarts in 10 min)
  5. Manual override

How to use it:
  # Halt all trading immediately
  redis-cli -h localhost -p 16379 SET apex:kill_switch 1
  
  # Verify
  curl http://localhost:8004/status | jq .kill_switch
  # → {"kill_switch": true, "trading_halted": true}
  
  # Resume
  redis-cli -h localhost -p 16379 DEL apex:kill_switch

Fail-closed guarantee:
  Redis down      → halt
  Key missing     → trade (normal)
  Key = "1"       → halt
  Key = "0"       → trade
```

---

## 6.6 Production Readiness Checklist

```
Infrastructure
  [ ] Redis: AOF enabled, healthcheck passing, fail-closed kill switch verified
  [ ] Kafka: retention limits set, consumer lag < 50
  [ ] TimescaleDB: retention policy, freshness check < 120s
  [ ] All services: depends_on: service_healthy, restart: unless-stopped

Monitoring
  [ ] PipelineStale alert configured AND tested
  [ ] Grafana dashboard showing all 6 key metrics
  [ ] paper_trading_monitor.py running daily
  [ ] position_reconciler reconciling every 60s

Risk Controls
  [ ] Kill switch tested (SET → verify halt → DEL → verify resume)
  [ ] Daily loss limit: 3% cap firing correctly
  [ ] CVaR: historical simulation, not Gaussian
  [ ] Position size: 2% cap per trade

Model Quality (30-day paper window)
  [ ] Win rate >= 52%
  [ ] Sharpe >= 1.2 (live paper Sharpe, not backtest)
  [ ] Max daily drawdown never exceeded limit
  [ ] Zero kill switch force-fires

Secrets
  [ ] All secrets in SealedSecrets, never in Git
  [ ] ALPACA_BASE_URL confirmed = paper before starting
```

---

## Quiz — Part 6

**Q1.** APEX's `risk_engine` tries to read the kill switch from Redis. Redis is down. The `except RedisError` block fires. Should the system (a) default to trading, (b) default to halting, or (c) crash the process? Why?

**Q2.** You run `docker compose ps` and see `risk-engine` has restarted 47 times in the last hour. What are the two most likely root causes, and what commands would you run to investigate?

**Q3.** Kafka consumer lag for `lean-alpha` is sitting at 0 all day, but your daily P&L report shows only 3 trades (vs. normal 40+). Where in the pipeline would you look first, and why does lag=0 not guarantee signals are flowing?

---

# Part 7 — Twitter/TFT Sentiment Pipeline

## 7.1 What is Sentiment Alpha?

### Simple version
Imagine everyone at school is whispering "NVDA is going to the moon!" Before the stock moves, smart traders can hear the whispers and buy early. That's sentiment alpha — making money from what people are *saying* before market prices fully reflect it.

### Technical version

Sentiment alpha comes from the information gap between when crowd opinion shifts and when price fully reflects it. Three reasons this gap exists:

```
1. PROCESSING LAG
   Tweet posted at 14:03:42 → human reads/acts at 14:05:30
   Automated pipeline acts at 14:03:55
   Edge window = ~90 seconds

2. AGGREGATION ADVANTAGE
   1 person can't read 10,000 tweets about NVDA in 15 minutes
   FinBERT scores 10,000 in ~8 seconds on GPU
   Aggregate signal is invisible to manual traders

3. INFLUENCE WEIGHTING
   @ElonMusk tweeting "$TSLA" >> random account tweeting "$TSLA"
   Weighting by follower count and engagement separates signal from noise
```

| Study | Finding |
|---|---|
| Bollen et al. (2011) | Twitter mood predicted Dow Jones 3 days ahead (86.7% accuracy) |
| Sprenger et al. (2014) | StockTwits bullish ratio predicted next-day abnormal returns |
| APEX TFT_v3 | Val Sharpe 9.2 with OHLCV+sentiment vs 6.1 OHLCV-only |

> **Gotcha:** Sentiment edge decays fast. Most edge consumed within 15–60 minutes of a tweet spike. At 15-minute bars (APEX's resolution), ~40% of edge is still capturable. At 1-minute bars, almost all of it.

---

## 7.2 Full Pipeline Architecture

```
Twitter/X
    │
    │  twscrape (rate-limited, cookie-authenticated)
    ▼
ScrapeCollector (scrape.py)
    │  Token bucket: 150 requests/15min
    │  Consecutive-empty detection (TFT-02)
    ▼
TweetTransformer (transformer.py)
    │  cashtag extraction: $NVDA → post_symbols junction
    │  spam_score: follower/following ratio, post frequency
    │  content_hash: SHA-256 dedup key
    │  snapshot_at = NOW() (leakage-safe — NOT created_at)
    ▼
PostgreSQL 16 (partitioned tables)
    ├── raw_posts        (JSONB archive)
    ├── posts            (canonical: id, author, content, created_at)
    ├── authors          (id, username, verified)
    ├── author_metrics_snapshots  (followers at snapshot_at — TFT-05)
    ├── post_symbols     (post_id ↔ symbol junction)
    └── post_metrics_snapshots   (likes/rt/reply at collection time)
    │
    │  every 60 seconds
    ▼
FinBERT sentiment job (jobs/sentiment.py)
    │  Batch: 32 posts (TFT-03 OOM guard)
    │  Output: sentiment_score [-1,+1], sentiment_label, influence_weight
    ▼
post_features table
    │
    │  every 15 minutes
    ▼
feature_extract job (jobs/feature_extract.py)
    │  Groups posts into CLOSED 15-min bars (TFT-04)
    │  LATERAL join: metrics at interval_end - lag_buffer (TFT-06 leakage fix)
    │  20+ aggregated features per symbol per bar
    ▼
symbol_interval_features table
    │
    │  daily (before training)
    ▼
build_one_day.py
    │  JOIN with OHLCV
    │  LEAD(close ORDER BY bar_start) label (TFT-06)
    │  Calendar features (hour, day_of_week, is_earnings_week)
    ▼
data/NVDA_2024-12-03.parquet  →  TFT Model Training
```

---

## 7.3 How FinBERT Works

### Simple version
FinBERT is a robot that has read millions of financial articles and learned what words mean in a money context. "NVDA crushes earnings" → positive. "Liquidity concerns" → very negative. Normal dictionaries just see words; FinBERT understands financial context.

### Technical version

**BERT** reads text bidirectionally — understands context from both left and right:
```
"disappointing revenue but beat EPS"

Standard sentiment: → neutral (confusing)
FinBERT:           → POSITIVE (EPS matters more to markets)
```

FinBERT is BERT fine-tuned on 10,000 finance-professional-labeled sentences.

**APEX FinBERT output:**
```python
text = "$NVDA Q4 blowout, beats on all metrics. Data center monster quarter."

output = {
    "sentiment_label": "positive",
    "sentiment_score": 0.94,
    "logits": [0.02, 0.94, 0.04]  # [negative, positive, neutral]
}

influence_weight = log1p(followers)  # log scale — 1M followers ≠ 1000× signal vs 1K
weighted_score   = sentiment_score × influence_weight
```

> **Gotcha:** On CPU, 1,000 tweets takes ~45s. On GPU (T4): ~3s. During earnings, $NVDA gets 50,000 tweets/minute. CPU-only → must sample.

---

## 7.4 How TFT (Temporal Fusion Transformer) Works

### Simple version
Normal neural networks look at a snapshot in time. TFT looks at a movie — it sees how things changed over time AND can explain which parts mattered most. "The stock went up because 3 hours ago sentiment spiked AND price broke above the 200 EMA."

### Technical version

TFT was introduced by Google (2019) for multi-horizon time series forecasting — designed specifically for tabular time series.

```
Input (per bar):
  ┌──────────────────────────────────────────────┐
  │ Known past:    OHLCV, RSI, MACD, sentiment   │
  │ Known future:  calendar (hour, day_of_week)  │
  │ Static:        symbol embedding (dim=8)       │
  └──────────────────────────────────────────────┘
          │
          ▼
  Variable Selection Network (VSN)
  "Which of my 25+ features actually matter right now?"
  Learned weight 0→1 per feature per timestep
          │
          ▼
  LSTM encoder  (recent history — last 30 bars)
          │
          ▼
  Multi-head Attention (Transformer component)
  "Which past bars are most relevant?"
  Unlike LSTM: can attend to ANY bar in window, not just last
          │
          ▼
  Gated Residual Network (GRN)
          │
          ▼
  Quantile output:
  P10 = pessimistic  |  P50 = median  |  P90 = optimistic
```

**TFT Variable Selection — interpretable alpha:**
```
TFT Attention output for NVDA at 14:30:
  sentiment_spike_30min_ago: 0.43  ← high attention
  rsi_divergence_2h_ago:     0.31
  volume_ratio_current:      0.18
  stoch_k_crossover:         0.08
  ..."I'm buying because sentiment spiked 30 min ago"
```

**APEX TFT input/output:**
```
Lookback:  30 bars × 15 min = 7.5 hours
Features:  ~25 per bar (OHLCV derived + sentiment)
Static:    symbol embedding (dim=8)
Horizon:   1 bar ahead (15 min)
Output:    P10, P50, P90
Signal:    P50 > threshold AND (P90 - P10) < volatility_cap
```

---

## 7.5 The 20+ Sentiment Features

```
COUNT FEATURES
  post_count          total tweets in window
  unique_authors      distinct users (1000 posts from 1 bot ≠ signal)
  verified_count      blue-check accounts

SENTIMENT FEATURES
  avg_sentiment       mean FinBERT score [-1, +1]
  weighted_sentiment  influence-weighted mean
  sentiment_std       disagreement → high std = uncertainty
  bull_bear_ratio     posts>0.3 / posts<-0.3

MOMENTUM FEATURES
  sentiment_mom_1b    avg_sentiment[t] - avg_sentiment[t-1]
  sentiment_mom_4b    avg_sentiment[t] - avg_sentiment[t-4]
  sentiment_accel     mom_1b - mom_1b[t-1]

ENGAGEMENT FEATURES
  avg_likes           average likes per post
  avg_retweets        retweet velocity
  avg_replies         controversy indicator

VOLUME FEATURES
  post_volume_ratio   post_count / rolling_avg_post_count (spike = event)
  engagement_score    weighted likes+rt+replies

INFLUENCE FEATURES
  max_influence       log1p(followers) of most influential poster
  avg_influence       mean influence weight

DERIVED FEATURES
  positive_pct        fraction positive label
  negative_pct        fraction negative label
  neutral_pct         fraction neutral
  cashtag_density     $SYMBOL / total_words (relevance score)
```

---

## 7.6 The 7 Bugs — Financial Consequences

### TFT-01: GENERATED column cross-table subquery (PG16 forbidden)

**Bug:**
```sql
-- 001_initial.sql (BROKEN)
followers_at_post INTEGER GENERATED ALWAYS AS (
    SELECT followers FROM author_metrics_snapshots
    WHERE author_id = authors.id ORDER BY snapshot_at DESC LIMIT 1
) STORED
-- PG16 forbids correlated subqueries in GENERATED columns
```

**Financial consequence:**
```
@Elon had 80M followers in 2023, has 200M in 2026.
2023 tweet scored with 2026 follower count = 2.5× inflated influence.
Model trained on inflated signal → overtrades based on phantom historical alpha
→ Live Sharpe collapses vs backtest
```

**Fix:** `002_fix_author_metrics.sql` — trigger captures followers at INSERT time.

---

### TFT-02: Cookie expiry returns empty iterator silently

**Bug:**
```python
async for tweet in client.search(query):
    yield tweet
# Cookie expires → empty iterator, no exception, exits "successfully"
```

**Financial consequence:**
```
Day 8+: Cookie expires → 0 tweets collected → job says "success"
Day 9:  sentiment features = NaN
Day 10: TFT model receives NaN inputs → outputs NaN probability
Day 12: risk_engine sees confidence=0.0 → rejects ALL trades
→ ZERO TRADES for potentially many days. Silent failure.
```

**Fix:** `consecutive_empty` counter — raises `CollectorEmptyError` after 3 runs with 0 tweets.

---

### TFT-03: CUDA OOM on large batches

**Bug:**
```python
BATCH_SIZE = 512  # never adjusted
for batch in chunks(posts, 512):
    outputs = model(batch)   # RuntimeError: CUDA out of memory on earnings day
    # Process crashes
```

**Financial consequence:**
```
$NVDA earnings: 45,000 tweets flood in 5 minutes
→ 512-tweet batch crashes GPU (9GB needed, 8GB available)
→ Process crashes, 45,000 posts unscored
→ No sentiment features for 16:30 bar
→ APEX misses earnings post-reaction spike (historical: ±8% gap)
→ $160 missed per earnings event on $100k account
```

**Fix:**
```python
except RuntimeError as e:
    if "out of memory" in str(e):
        torch.cuda.empty_cache()
        BATCH_SIZE //= 2
        outputs = model(tokenized[:BATCH_SIZE])
```

---

### TFT-04: Open bar used instead of closed bar

**Bug:**
```python
def _floor_to_bar(ts, bar_minutes):
    return ts.replace(minute=(ts.minute // bar_minutes) * bar_minutes, second=0)
    # Returns CURRENT open bar, not last closed bar
```

**Financial consequence:**
```
feature_extract runs at 14:38 (mid-bar):
  Training: uses ALL posts 14:30→14:45 (bar is historical, all posts exist)
  Inference: uses only posts 14:30→14:38 (53% complete bar)

Train vs live feature distribution mismatch:
  train: avg_sentiment from 100% of bar's posts
  live:  avg_sentiment from ~53% of bar's posts

Live Sharpe ≈ backtest_sharpe × 0.53  (systematic underperformance)
```

**Fix:** Subtract one bar period to use last CLOSED bar:
```python
return floored - timedelta(minutes=bar_minutes)
```

---

### TFT-05: Latest follower count used for historical windows

**Bug:**
```sql
SELECT log1p(a.followers_current) AS influence_weight   -- TODAY's count
FROM posts p JOIN authors a ON a.id = p.author_id
```

**Financial consequence:**
Same root cause as TFT-01 but in the feature query path: model trains on 2.5× inflated influence for 2023 data → overfits to phantom historical alpha → live underperformance.

**Fix:**
```sql
JOIN LATERAL (
    SELECT followers FROM author_metrics_snapshots ams
    WHERE ams.author_id = p.author_id
      AND ams.snapshot_at <= p.created_at + INTERVAL '1 hour'
    ORDER BY snapshot_at DESC LIMIT 1
) ams ON true
```

---

### TFT-06: `LEAD()` without `ORDER BY` — undefined label

**Bug:**
```sql
LEAD(close) OVER (PARTITION BY symbol) / close - 1 AS fwd_return_1bar
--                              ↑ no ORDER BY — arbitrary row order
```

**Financial consequence:**
```
PostgreSQL row order without ORDER BY is arbitrary.
"Next" close could be ANY row in the partition.

Row 1 (14:30): close=450.00, "next" might be Row 3's close=453.10
Row 2 (14:45): close=447.20, "next" might be Row 1's close=450.00

Model trained on RANDOM labels → val_hit_rate stuck at exactly 0.50
(looks like a coin flip, because training labels were randomly shuffled)
```

**Fix:**
```sql
LEAD(close) OVER (PARTITION BY symbol ORDER BY bar_start) / close - 1
```

---

### TFT-07: Watermark written before DB commit

**Bug:**
```python
posts = await collector.fetch(since=watermark)
await db.bulk_insert(posts)          # step 1: insert
await redis.set("watermark", now())  # step 2: update watermark  ← WRONG ORDER
await db.commit()                    # step 3: commit
# Crash between step 2 and 3: watermark advanced, but posts rolled back
# Those posts PERMANENTLY SKIPPED on restart
```

**Financial consequence:**
```
If 287 posts from 14:15→14:30 are permanently lost:
  14:15 bar: 0 posts (instead of 287)
  avg_sentiment = NaN
  
If those 287 posts were a sentiment spike around an announcement:
  The spike never appears → model doesn't trade → APEX misses the move
```

**Fix:** Watermark update inside the same transaction:
```python
async with db.begin():
    await db.bulk_insert(posts)
    await db.execute(
        "INSERT INTO watermarks VALUES ($1) ON CONFLICT DO UPDATE SET value=$1",
        [now()]
    )
    # Both commit or both roll back — no partial state possible
```

---

## 7.7 Validating Sentiment Alpha Before Going Live

### Step 1 — Ablation test (does sentiment actually help?)
```python
model_A = train_tft(price_volume_features_only)  → val_sharpe = S_A
model_B = train_tft(price_volume_features + sentiment)  → val_sharpe = S_B

if S_B - S_A < 0.5:
    # Sentiment adds < 0.5 Sharpe → not worth the complexity
    reject_sentiment_alpha()
```

### Step 2 — 5-step leakage audit
```sql
-- 1. Post boundary
SELECT COUNT(*) FROM posts p
JOIN post_symbols ps ON ps.post_id = p.id
JOIN symbol_interval_features sif ON sif.symbol = ps.symbol
WHERE p.created_at >= sif.interval_end;
-- Must return 0

-- 2. Metrics cutoff
SELECT COUNT(*) FROM post_metrics_snapshots pms
JOIN ... WHERE pms.snapshot_at > interval_end - lag_buffer;
-- Must return 0

-- 3. Forward label integrity
SELECT CORR(fwd_return_1bar, LEAD(close) OVER (...)/close - 1) FROM ...;
-- Must be ~1.0

-- 4. Influence weight check (10 sampled rows)
-- |influence_weight - log1p(followers_at_post_time)| < 0.001

-- 5. Train/val split
assert train.bar_start.max() < val.bar_start.min()
```

### Step 3 — Out-of-time split (not random)
```
WRONG (random 80/20 split):
  train: random rows from Jan–Dec 2024
  val:   other random rows from same period
  ← model sees sentiment patterns from all months during training

CORRECT (temporal split):
  Jan 2024 → Sep 2024: TRAIN
  Oct 2024 → Dec 2024: VALIDATION (embargo = 1 trading day between)
  Jan 2025 → Mar 2025: TEST (never touch until final evaluation)
```

### Step 4 — Regime stability test
```
Bull  Jan–Mar 2024 → TFT Sharpe = X₁
Chop  Apr–Jun 2024 → TFT Sharpe = X₂
Bear  Jul–Aug 2024 → TFT Sharpe = X₃

Requirement: All > 1.0
If X₂ = 0.3 (choppy market) → sentiment alpha is regime-dependent
→ gate the sentiment signal in choppy regime
```

### Step 5 — Paper trading signal attribution
```
For winning trades: avg_sentiment_at_entry = +0.67
For losing trades:  avg_sentiment_at_entry = +0.61  ← similar!

For winning trades: technical_score_at_entry = +0.52
For losing trades:  technical_score_at_entry = +0.21  ← different!

Conclusion: Technical score was the better discriminator.
            Reduce sentiment weight in ensemble temporarily.
```

---

## 7.8 How Sentiment Connects to APEX Ensemble

```
twitter_tft/jobs/feature_extract.py
    │  writes symbol_interval_features every 15 min
    ▼
services/signal_provider/main.py
    │  reads sentiment features from TimescaleDB
    │  adds sentiment_score to ensemble feature vector
    ▼
ENS_v4 meta-learner
    │  receives: [xgb_pred, lstm_pred, tft_pred, sentiment_features...]
    ▼
lean-alpha → signal_engine → risk_engine → execution
```

APEX upsamples (forward-fills) 15-min sentiment values across 15 one-minute bars.

> **Gotcha — staleness during fast events:** Major news at 14:03 → next sentiment bar computed at 14:15. For 12 minutes, sentiment is stale. The TFT staleness gate in `signal_engine/ensemble.py` handles this: if last sentiment bar is > 600s old during market hours, sentiment weight = 0. Ensemble runs on price/volume features only until next bar.

---

## Quiz — Part 7

**Q1.** Your sentiment job collects 0 tweets for 3 consecutive runs. The TFT-02 fix fires `CollectorEmptyError`. Without this fix, what would downstream features look like at the next 15-min bar, and how would that propagate to a trade decision?

**Q2.** Your TFT model achieves val_Sharpe = 9.2 on the Oct–Dec 2024 test period using temporal split. A colleague says "just use random 80/20 split — it's faster." Walk them through which rows would be in training vs validation under random split and why that would inflate the reported Sharpe.

**Q3.** You run leakage audit check 2 and find the query returns 147 (not 0). What kind of leakage is this, what effect would it have on training vs live performance, and what does `post_metrics_snapshots` represent that makes it specifically prone to this type of leakage?

---

# Recap — All 7 Parts

```
Part 1: Quant Finance Foundations
  Alpha (CAPM intercept), Sharpe (reward/risk, ann_factor=√(252×390) for minutes),
  hit rate + payoff ratio, drawdown, signals/features/labels,
  5 market pattern sources (momentum, mean reversion, volume, volatility, sentiment)

Part 2: ML Models
  XGBoost (gradient boosting, 500 trees, residual learning, no temporal memory)
  LSTM (cell state, 3 gates, lookback=32×21, temporal acceleration patterns)
  TimesFM (zero-shot, patched decoder transformer, univariate, P10/P50/P90)
  ENS_v4 (stacking meta-learner, error decorrelation, cross-terms, Sharpe=15.44)

Parts 3+4: Infrastructure + Pipeline
  All 13 services with ports; Kafka topics + Bug-B auto-commit + CF-7 flush order
  Redis 4 roles (registry, kill switch, positions, bloom filter) + HI-8 AOF fix
  TimescaleDB hypertables + continuous aggregates + MD-2 retention
  MLflow ENS_v4 full record; Prometheus HI-6 market-hours gating
  T+0ms to T+300ms bar-to-trade journey; all 21 features; risk 5-check tree
  Exit monitor 30s loop; position reconciler drift detection

Part 5: Model Training Deep Dive
  3 leakage types (label, scaler/CF-4, temporal/CF-1+CF-2)
  Walk-forward 4-fold diagram; embargo math (21→180 bars, autocorrelation decay)
  CF-1 fold selection: folds[-1] not max-Sharpe
  FoldScaler IS-only fit + JSON sidecar; full MLflow record
  Model lifecycle (pending→staged→live→archived); ENS_v4 vs v3 root cause

Part 6: Production System Design
  Microservices vs monolith; healthchecks + depends_on + restart policies
  4 monitoring layers (Prometheus/Grafana, health_check.sh, paper monitor, MLflow)
  7 failure modes + defenses; fail-closed kill switch; CF-8 timeout; CF-9 shutdown
  Production readiness checklist; K8s for live capital

Part 7: Twitter/TFT Sentiment Pipeline
  Sentiment alpha theory (processing lag, aggregation, influence weighting)
  Full scrape→FinBERT→features→TFT pipeline; FinBERT financial context
  TFT architecture (VSN, LSTM encoder, multi-head attention, GRN, quantiles)
  20+ sentiment features; all 7 TFT bugs with exact financial consequences
  5-step validation framework; regime stability; signal attribution
  Integration with APEX ensemble via 15-min forward-fill
```

The 30-day paper trading window (Phase 08) is the remaining gate.
Pass criteria: win rate ≥ 52%, Sharpe ≥ 1.2, zero daily loss breaches.
Full checklist in [docs/PAPER_TRADING_RUNBOOK.md](PAPER_TRADING_RUNBOOK.md).

---

*Last updated: 2026-03-03*
