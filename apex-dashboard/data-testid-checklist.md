# data-testid Attribute Checklist

Complete list of `data-testid` attributes to add to the APEX app.
Organized by page priority. Add **REQUIRED** attributes first — tests
will use fragile text/role fallbacks until these are in place.

---

## Priority Legend

| Symbol | Meaning |
|--------|---------|
| 🔴 REQUIRED | Test will fail or use fragile fallback without this |
| 🟡 IMPORTANT | Improves test stability + maintainability significantly |
| 🟢 NICE-TO-HAVE | Enables richer assertions; low urgency |

---

## Global / Shared Components

### Sidebar (`src/components/Sidebar.tsx`)

| Element | data-testid | Priority |
|---------|------------|----------|
| Dashboard nav link | `nav-dashboard` | 🟡 IMPORTANT |
| Charts nav link | `nav-charts` | 🟡 IMPORTANT |
| Signals nav link | `nav-signals` | 🟡 IMPORTANT |
| Trading nav link | `nav-trading` | 🟡 IMPORTANT |
| **Orders nav link** | `nav-orders` | 🔴 REQUIRED |
| Wallet nav link | `nav-wallet` | 🟡 IMPORTANT |
| Risk nav link | `nav-risk` | 🟡 IMPORTANT |
| Backtest nav link | `nav-backtest` | 🟡 IMPORTANT |
| Models nav link | `nav-models` | 🟡 IMPORTANT |

**How to add:**
```tsx
// In Sidebar.tsx NAV array or the Link component:
<Link href={href} data-testid={`nav-${label.toLowerCase()}`}>
```

---

## /orders Page

| Element | data-testid | Priority |
|---------|------------|----------|
| **Emergency stop button** | `kill-switch-btn` | 🔴 REQUIRED |
| **Kill switch status text** | `kill-switch-status` | 🔴 REQUIRED |
| **Armed pulsing indicator** | `kill-switch-armed-indicator` | 🔴 REQUIRED |
| Symbol dropdown/select | `trade-symbol-select` | 🔴 REQUIRED |
| BUY button | `trade-side-buy` | 🔴 REQUIRED |
| SELL button | `trade-side-sell` | 🔴 REQUIRED |
| Quantity input | `trade-qty-input` | 🔴 REQUIRED |
| Market order button | `trade-order-type-market` | 🔴 REQUIRED |
| Limit order button | `trade-order-type-limit` | 🔴 REQUIRED |
| Limit price input | `trade-limit-price-input` | 🔴 REQUIRED |
| Paper mode button | `trade-account-paper` | 🔴 REQUIRED |
| Live mode button | `trade-account-live` | 🟡 IMPORTANT |
| Confirm checkbox (container div) | `trade-confirm-checkbox` | 🔴 REQUIRED |
| Submit order button | `trade-submit-btn` | 🔴 REQUIRED |
| Success message | `trade-result-msg` | 🔴 REQUIRED |
| Error message | `trade-error-msg` | 🔴 REQUIRED |
| Refresh orders button | `orders-refresh-btn` | 🟡 IMPORTANT |
| Open orders table | `orders-open-table` | 🟡 IMPORTANT |
| Order cancel button (per row) | `order-cancel-btn` | 🟡 IMPORTANT |
| Order history table | `orders-history-table` | 🟡 IMPORTANT |
| Paper mode toggle (header) | `orders-account-paper` | 🟢 NICE-TO-HAVE |
| Live mode toggle (header) | `orders-account-live` | 🟢 NICE-TO-HAVE |

**Minimal implementation — orders page:**
```tsx
// Kill switch section:
<button data-testid="kill-switch-btn" ...>⛔ EMERGENCY STOP</button>
<div data-testid="kill-switch-status">...</div>
<div data-testid="kill-switch-armed-indicator" .../>

// Order form:
<select data-testid="trade-symbol-select" ...>
<button data-testid="trade-side-buy" ...>↑ BUY</button>
<button data-testid="trade-side-sell" ...>↓ SELL</button>
<input data-testid="trade-qty-input" type="number" .../>
<button data-testid="trade-order-type-market" ...>market</button>
<button data-testid="trade-order-type-limit" ...>limit</button>
<input data-testid="trade-limit-price-input" .../>
<div data-testid="trade-confirm-checkbox" onClick={...}>
<button data-testid="trade-submit-btn" ...>SUBMIT ORDER</button>
<div data-testid="trade-result-msg" ...>{tradeMsg.text}</div>
```

---

## /trading Page

| Element | data-testid | Priority |
|---------|------------|----------|
| Auto trading toggle | `auto-trading-toggle` | 🔴 REQUIRED |
| Trading mode status | `trading-mode-status` | 🟡 IMPORTANT |
| Min confidence slider | `settings-min-confidence` | 🟢 NICE-TO-HAVE |
| Max position size input | `settings-max-position` | 🟢 NICE-TO-HAVE |
| Max daily trades input | `settings-max-trades` | 🟢 NICE-TO-HAVE |
| Save settings button | `settings-save-btn` | 🟡 IMPORTANT |
| Last trade result | `last-trade-result` | 🟡 IMPORTANT |

---

## /dashboard Page

| Element | data-testid | Priority |
|---------|------------|----------|
| Page root container | `dashboard-root` | 🟢 NICE-TO-HAVE |
| Equity / Portfolio value card | `stat-card-equity` | 🟡 IMPORTANT |
| P&L stat card | `stat-card-pnl` | 🟡 IMPORTANT |
| Positions count card | `stat-card-positions` | 🟡 IMPORTANT |
| Market status indicator | `market-status` | 🟡 IMPORTANT |
| Auto trading banner | `auto-trading-banner` | 🟢 NICE-TO-HAVE |

---

## /signals Page

| Element | data-testid | Priority |
|---------|------------|----------|
| **Each signal row** | `signal-row` | 🔴 REQUIRED |
| Symbol cell (inside row) | `signal-symbol` | 🔴 REQUIRED |
| Confidence cell (inside row) | `signal-confidence` | 🔴 REQUIRED |
| Direction cell (inside row) | `signal-direction` | 🟡 IMPORTANT |
| Predicted value cell | `signal-predicted-value` | 🔴 REQUIRED |
| Horizon cell | `signal-horizon` | 🟡 IMPORTANT |
| Timestamp cell | `signal-timestamp` | 🟡 IMPORTANT |
| Symbol filter | `signal-filter-symbol` | 🟡 IMPORTANT |
| Horizon filter | `signal-filter-horizon` | 🟡 IMPORTANT |
| Refresh button | `signal-refresh-btn` | 🟡 IMPORTANT |
| Error/unavailable message | `forecast-error-msg` | 🟡 IMPORTANT |

**Minimal signal row implementation:**
```tsx
{signals.map(signal => (
  <tr key={signal.id} data-testid="signal-row" data-symbol={signal.symbol}>
    <td data-testid="signal-symbol">{signal.symbol}</td>
    <td data-testid="signal-confidence">{signal.confidence.toFixed(4)}</td>
    <td data-testid="signal-predicted-value">${signal.predicted_value.toFixed(2)}</td>
    <td data-testid="signal-direction">{signal.direction}</td>
    <td data-testid="signal-timestamp">{signal.timestamp}</td>
  </tr>
))}
```

---

## /models Page

| Element | data-testid | Priority |
|---------|------------|----------|
| **Live model badge** | `model-live-badge` | 🔴 REQUIRED |
| Model status label (per card) | `model-status-label` | 🟡 IMPORTANT |
| Train/retrain button | `model-train-btn` | 🔴 REQUIRED |
| Model ID display | `model-id` | 🟢 NICE-TO-HAVE |
| A/B test start button | `ab-test-start-btn` | 🟢 NICE-TO-HAVE |
| A/B test stop button | `ab-test-stop-btn` | 🟢 NICE-TO-HAVE |
| Alert count badge | `model-alerts-count` | 🟢 NICE-TO-HAVE |

---

## /wallet Page

| Element | data-testid | Priority |
|---------|------------|----------|
| Account equity value | `wallet-equity` | 🟡 IMPORTANT |
| Cash balance value | `wallet-cash` | 🟡 IMPORTANT |
| P&L value | `wallet-pnl` | 🟡 IMPORTANT |
| P&L chart | `wallet-pnl-chart` | 🟢 NICE-TO-HAVE |
| Positions table | `wallet-positions-table` | 🟡 IMPORTANT |
| Transactions table | `wallet-transactions-table` | 🟢 NICE-TO-HAVE |

---

## /charts Page

| Element | data-testid | Priority |
|---------|------------|----------|
| Symbol selector | `chart-symbol-select` | 🟡 IMPORTANT |
| Timeframe selector | `chart-timeframe-select` | 🟡 IMPORTANT |
| Chart canvas/container | `chart-container` | 🟡 IMPORTANT |
| Candlestick chart | `chart-candlestick` | 🟢 NICE-TO-HAVE |

---

## /risk Page

| Element | data-testid | Priority |
|---------|------------|----------|
| Risk score / gauge | `risk-score` | 🟡 IMPORTANT |
| VaR display | `risk-var` | 🟢 NICE-TO-HAVE |
| Risk alerts list | `risk-alerts` | 🟡 IMPORTANT |
| Exposure table | `risk-exposure-table` | 🟢 NICE-TO-HAVE |

---

## /backtest Page

| Element | data-testid | Priority |
|---------|------------|----------|
| Start date input | `backtest-start-date` | 🟡 IMPORTANT |
| End date input | `backtest-end-date` | 🟡 IMPORTANT |
| Run backtest button | `backtest-run-btn` | 🟡 IMPORTANT |
| Results container | `backtest-results` | 🟡 IMPORTANT |
| Sharpe ratio value | `backtest-sharpe` | 🟢 NICE-TO-HAVE |
| Total return value | `backtest-total-return` | 🟢 NICE-TO-HAVE |

---

## /login Page (when auth is added)

| Element | data-testid | Priority |
|---------|------------|----------|
| Email input | `login-email` | 🔴 REQUIRED |
| Password input | `login-password` | 🔴 REQUIRED |
| Submit button | `login-submit` | 🔴 REQUIRED |
| Error message | `login-error` | 🔴 REQUIRED |
| Logout button (anywhere) | `logout-btn` | 🔴 REQUIRED |

---

## Implementation Count Summary

| Priority | Count |
|----------|-------|
| 🔴 REQUIRED | ~25 |
| 🟡 IMPORTANT | ~30 |
| 🟢 NICE-TO-HAVE | ~20 |
| **Total** | **~75** |

**Start here:** Add all 🔴 REQUIRED attributes to `/orders/page.tsx`,
`/signals/page.tsx`, and `/models/page.tsx` first — those pages have
the most test coverage and the most complex interactions.
