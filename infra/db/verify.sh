#!/bin/bash
# Verify TimescaleDB is set up correctly
# Run: bash infra/db/verify.sh

set -euo pipefail

echo "=== APEX Database Verification ==="

# 1. Check container is running
echo ""
echo "[1] Container status:"
docker inspect apex-timescaledb \
  --format='  Status: {{.State.Status}}  Health: {{.State.Health.Status}}' \
  2>/dev/null || echo "  Container not found — run: docker compose up -d timescaledb"

# 2. Check connection
echo ""
echo "[2] Connection test:"
docker exec apex-timescaledb \
  psql -U apex_user -d apex \
  -c "SELECT version();" \
  2>/dev/null | grep -o "PostgreSQL [0-9.]*" || \
  echo "  Connection failed"

# 3. Check TimescaleDB version
echo ""
echo "[3] TimescaleDB version:"
docker exec apex-timescaledb \
  psql -U apex_user -d apex \
  -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';" \
  2>/dev/null | grep -E "[0-9]+\.[0-9]+" | xargs || \
  echo "  TimescaleDB extension not found"

# 4. Check tables
echo ""
echo "[4] Tables:"
docker exec apex-timescaledb \
  psql -U apex_user -d apex \
  -c "\dt" 2>/dev/null || \
  echo "  Could not list tables (schema may not be initialized yet)"

# 5. Check hypertables
echo ""
echo "[5] Hypertables:"
docker exec apex-timescaledb \
  psql -U apex_user -d apex \
  -c "SELECT hypertable_name, num_chunks
      FROM timescaledb_information.hypertables
      ORDER BY hypertable_name;" \
  2>/dev/null || echo "  No hypertables found"

# 6. Check continuous aggregates
echo ""
echo "[6] Continuous aggregates:"
docker exec apex-timescaledb \
  psql -U apex_user -d apex \
  -c "SELECT view_name, materialization_hypertable_name
      FROM timescaledb_information.continuous_aggregates
      ORDER BY view_name;" \
  2>/dev/null || echo "  No continuous aggregates found"

# 7. Row counts
echo ""
echo "[7] Row counts (all should be 0 on a fresh DB):"
docker exec apex-timescaledb \
  psql -U apex_user -d apex -c "
    SELECT 'ohlcv_bars'          AS table_name, COUNT(*) AS rows FROM ohlcv_bars
    UNION ALL
    SELECT 'features',           COUNT(*) FROM features
    UNION ALL
    SELECT 'signals',            COUNT(*) FROM signals
    UNION ALL
    SELECT 'orders',             COUNT(*) FROM orders
    UNION ALL
    SELECT 'positions',          COUNT(*) FROM positions
    UNION ALL
    SELECT 'portfolio_snapshots',COUNT(*) FROM portfolio_snapshots
    UNION ALL
    SELECT 'model_performance',  COUNT(*) FROM model_performance
    ORDER BY table_name;
  " 2>/dev/null || echo "  Could not query tables"

# 8. Test API endpoint
echo ""
echo "[8] Dashboard bars API:"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:3001/api/bars?symbol=NVDA&timeframe=15Min&limit=5" \
  2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
  echo "  ✓ http://localhost:3001/api/bars → 200 OK"
else
  echo "  ✗ http://localhost:3001/api/bars → $HTTP_CODE (dashboard may not be running)"
fi

echo ""
echo "=== Verification complete ==="
echo ""
echo "Next step: Run data ingestion to populate ohlcv_bars"
echo "  docker compose up -d data_ingestion"
