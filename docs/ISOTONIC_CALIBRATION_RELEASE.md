# Isotonic Calibration — Release Note

**Date:** 2026-03-07  
**Phase:** 0 → Phase 0.5 (calibration cutover)  
**Status:** Active in production

---

## Summary

Replaced Platt (sigmoid) calibration with isotonic regression as the **active** probability
calibrator in the signal-engine pipeline. Platt probabilities are retained as an audit field.

## What Changed

### Signal-Engine (`services/signal_engine/main.py`)

| Field | Before (Platt) | After (Isotonic) |
|---|---|---|
| `probability` | Platt sigmoid | Isotonic (active) |
| `calibrated_prob` | Platt sigmoid | Isotonic (active) |
| `raw_edge_bps` | `(platt - 0.5) × 200` | `(isotonic - 0.5) × 200` |
| `platt_prob` | *(did not exist)* | Platt sigmoid (audit) |
| `isotonic_prob` | *(did not exist)* | Isotonic value (always present) |

Conviction filter and position sizing downstream both use `probability`, which now
carries the isotonic value.

### Redis Key

- **Key:** `apex:calibration:curve`
- **Format:** `pickle.dumps(IsotonicRegression)` — pickle protocol 4
- **Size:** ~664 bytes
- **Host port:** 16379 (container 6379)

### Scored Kafka Message Schema (`apex.signals.scored`)

New fields added alongside existing ones:

```json
{
  "probability":     0.5429,   // ← isotonic (active)
  "calibrated_prob": 0.5429,   // ← isotonic (active)
  "platt_prob":      0.6447,   // ← audit only
  "isotonic_prob":   0.5429,   // ← always present
  "raw_edge_bps":    8.57      // ← based on isotonic
}
```

### Downstream Services — No Code Changes Required

- **Risk-engine:** reads `probability` for conviction sizing → now receives isotonic.
- **Execution-engine:** reads `calibrated_prob` for DB persistence → now receives isotonic.

Both services were audited; no semantic drift.

## Feature Flag

```bash
# infra/.env
ENABLE_ISOTONIC_CALIBRATION=true   # active (default: false)
```

When `false` **or** when the Redis key is missing, the engine falls back to Platt
calibration automatically. No code change or restart delay beyond the env-var flip
and `docker compose up -d --force-recreate signal-engine`.

## Rollback

```bash
# 1. Flip the flag
sed -i 's/ENABLE_ISOTONIC_CALIBRATION=true/ENABLE_ISOTONIC_CALIBRATION=false/' infra/.env

# 2. Redeploy
cd infra && docker compose up -d --force-recreate signal-engine
```

Verify rollback in logs:

```bash
docker logs --tail 30 infra-signal-engine-1 2>&1 | grep active_prob
# Expected: active_prob=<platt_value>, source=platt
```

## Re-enable

```bash
sed -i 's/ENABLE_ISOTONIC_CALIBRATION=false/ENABLE_ISOTONIC_CALIBRATION=true/' infra/.env
cd infra && docker compose up -d --force-recreate signal-engine

docker logs --tail 30 infra-signal-engine-1 2>&1 | grep isotonic
# Expected: isotonic_calibrator_loaded_from_redis
```

## Fit Script

To refit the calibrator from `calibration_snapshots` data:

```bash
.venv/bin/python scripts/fit_isotonic_from_snapshots.py --redis-port 16379
```

The script reads histogram bins from TimescaleDB `calibration_snapshots`, expands
them into synthetic (probability, outcome) pairs, fits `IsotonicRegression`, and
pushes the result to Redis. Restart signal-engine after refitting.

## Verification Commands

```bash
# Check Redis key exists
docker exec infra-redis-1 redis-cli EXISTS apex:calibration:curve
# → (integer) 1

# Check signal-engine startup log
docker logs --tail 30 infra-signal-engine-1 2>&1 | grep isotonic

# Inject a test signal (weekday or with SKIP_MARKET_HOURS=1)
echo '{"symbol":"AAPL","factor_score":0.62,"xgb_score":0.58,"timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' | \
  docker exec -i infra-kafka-1 kafka-console-producer.sh \
    --broker-list localhost:9092 --topic apex.signals.raw

# Read scored output
docker exec infra-kafka-1 kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic apex.signals.scored \
  --from-beginning --max-messages 1 --timeout-ms 10000
```

## Tests

```bash
.venv/bin/python -m pytest tests/test_calibrator.py -v
# 13 tests — all pass
```
