"use client";
import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

// ─── Inline Sidebar (no external import — avoids SSR issues) ─────────────────
const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", accent: "#00D4FF" },
  { href: "/charts",    label: "Charts",    accent: "#00D4FF" },
  { href: "/signals",   label: "Signals",   accent: "#00FF88" },
  { href: "/trading",   label: "Trading",   accent: "#00D4FF" },
  { href: "/orders",    label: "Orders",    accent: "#00D4FF" },
  { href: "/wallet",    label: "Wallet",    accent: "#FFB800" },
  { href: "/risk",      label: "Risk",      accent: "#FF2D55" },
  { href: "/backtest",  label: "Backtest",  accent: "#FFB800" },
  { href: "/models",    label: "Models",    accent: "#8B5CF6" },
];

function InlineSidebar() {
  const path = usePathname();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return (
    <aside style={{
      width: 200, minHeight: "100vh", background: "rgba(5,8,16,0.98)",
      borderRight: "1px solid #0d1f2d", display: "flex", flexDirection: "column",
      padding: "24px 0", gap: 0, flexShrink: 0,
    }}>
      <div style={{ padding: "0 20px 28px", display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{
          width: 32, height: 32, borderRadius: 8, background: "linear-gradient(135deg,#00D4FF,#0066CC)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 14, fontWeight: 700, color: "#fff", fontFamily: "JetBrains Mono, monospace"
        }}>A</div>
        <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 14, fontWeight: 700, color: "#e8f4fd", letterSpacing: "0.08em" }}>APEX</span>
      </div>
      {NAV_ITEMS.map(({ href, label, accent }) => {
        const active = mounted && path.startsWith(href);
        return (
          <Link key={href} href={href} style={{ textDecoration: "none" }}>
            <div style={{
              padding: "10px 20px", display: "flex", alignItems: "center", gap: 12,
              background: active ? `${accent}18` : "transparent",
              borderLeft: active ? `3px solid ${accent}` : "3px solid transparent",
              color: active ? accent : "#6a8aa8",
              fontFamily: "JetBrains Mono, monospace", fontSize: 12, fontWeight: active ? 600 : 400,
              cursor: "pointer", transition: "all 0.15s",
            }}>
              {label}
            </div>
          </Link>
        );
      })}
    </aside>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
const safeNum = (v: unknown, fallback = 0): number => {
  const n = Number(v);
  return isFinite(n) ? n : fallback;
};

const SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META", "GOOGL", "AMD", "SPY", "QQQ"];

type OrderStatus = "pending_new" | "accepted" | "partially_filled" | "filled" | "canceled" | "expired" | "replaced" | string;

interface Order {
  id: string;
  client_order_id?: string;
  symbol: string;
  side: "buy" | "sell";
  qty: string | number;
  filled_qty?: string | number;
  status: OrderStatus;
  filled_avg_price?: string | number | null;
  submitted_at?: string | null;
  filled_at?: string | null;
  order_type?: string;
  source?: string;
}

const STATUS_COLOR: Record<string, string> = {
  filled: "#00FF88",
  partially_filled: "#FFB800",
  pending_new: "#00D4FF",
  accepted: "#00D4FF",
  canceled: "#4a6a8a",
  expired: "#4a6a8a",
  replaced: "#8B5CF6",
};

const getStatusColor = (s: string) => STATUS_COLOR[s] ?? "#6a8aa8";

function fmtDate(ts?: string | null) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

function fmtPrice(v?: string | number | null) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return isFinite(n) ? `$${n.toFixed(2)}` : "—";
}

const OPEN_STATUSES = new Set(["pending_new", "accepted", "partially_filled", "new", "held"]);
const isOpen = (s: string) => OPEN_STATUSES.has(s);

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function OrdersPage() {
  const [mounted, setMounted] = useState(false);

  // Kill switch
  const [killActive, setKillActive] = useState(false);
  const [killLoading, setKillLoading] = useState(false);
  const [killMsg, setKillMsg] = useState<string | null>(null);

  // Orders
  const [orders, setOrders] = useState<Order[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [cancelingId, setCancelingId] = useState<string | null>(null);

  // Place order form
  const [symbol, setSymbol] = useState("NVDA");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [accountMode, setAccountMode] = useState<"paper" | "live">("paper");
  const [confirmed, setConfirmed] = useState(false);
  const [tradeLoading, setTradeLoading] = useState(false);
  const [tradeMsg, setTradeMsg] = useState<{ text: string; ok: boolean } | null>(null);

  // Account mode for orders display
  const [filterMode, setFilterMode] = useState<"paper" | "live">("paper");

  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => { setMounted(true); }, []);

  const fetchKillSwitch = useCallback(async () => {
    try {
      const res = await fetch("/api/kill-switch");
      const data = await res.json();
      setKillActive(!!data.active);
    } catch { /* ignore */ }
  }, []);

  const fetchOrders = useCallback(async () => {
    setOrdersLoading(true);
    try {
      const res = await fetch(`/api/orders?limit=50&account_mode=${filterMode}`);
      const data = await res.json();
      const list = Array.isArray(data.orders) ? data.orders : [];
      setOrders(list);
    } catch {
      setOrders([]);
    } finally {
      setOrdersLoading(false);
    }
  }, [filterMode]);

  useEffect(() => {
    if (!mounted) return;
    fetchKillSwitch();
    fetchOrders();
    intervalRef.current = setInterval(() => {
      fetchKillSwitch();
      fetchOrders();
    }, 8000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [mounted, fetchKillSwitch, fetchOrders]);

  const toggleKillSwitch = async (activate: boolean) => {
    setKillLoading(true);
    setKillMsg(null);
    try {
      const res = await fetch("/api/kill-switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: activate, reason: activate ? "Manual emergency stop via dashboard" : "Kill switch deactivated via dashboard" }),
      });
      const data = await res.json();
      setKillActive(!!data.active);
      setKillMsg(activate ? "⚠ KILL SWITCH ARMED — all trading halted" : "✓ Kill switch deactivated — trading resumed");
    } catch (e) {
      setKillMsg(`Error: ${String(e)}`);
    } finally {
      setKillLoading(false);
    }
  };

  const cancelOrder = async (id: string) => {
    setCancelingId(id);
    try {
      await fetch(`/api/orders?id=${encodeURIComponent(id)}&account_mode=${filterMode}`, { method: "DELETE" });
      await fetchOrders();
    } catch { /* ignore */ }
    setCancelingId(null);
  };

  const placeOrder = async () => {
    if (!confirmed) { setTradeMsg({ text: "Check the confirm box first", ok: false }); return; }
    const qtyN = safeNum(qty);
    if (qtyN <= 0) { setTradeMsg({ text: "Quantity must be > 0", ok: false }); return; }
    if (orderType === "limit" && !limitPrice) { setTradeMsg({ text: "Enter a limit price", ok: false }); return; }

    setTradeLoading(true);
    setTradeMsg(null);
    try {
      const body: Record<string, unknown> = {
        symbol, side, qty: qtyN, order_type: orderType,
        account_mode: accountMode, confirmed: true,
      };
      if (orderType === "limit") body.limit_price = safeNum(limitPrice);

      const res = await fetch("/api/trade", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok) {
        setTradeMsg({ text: `✓ ${side.toUpperCase()} ${qtyN} ${symbol} submitted (${data.id ?? "queued"})`, ok: true });
        setConfirmed(false);
        setTimeout(fetchOrders, 2000);
      } else {
        setTradeMsg({ text: `Error: ${data.error ?? data.detail ?? "Unknown error"}`, ok: false });
      }
    } catch (e) {
      setTradeMsg({ text: `Network error: ${String(e)}`, ok: false });
    } finally {
      setTradeLoading(false);
    }
  };

  const openOrders = orders.filter(o => isOpen(o.status));
  const closedOrders = orders.filter(o => !isOpen(o.status)).slice(0, 25);

  if (!mounted) {
    return (
      <div style={{ display: "flex", minHeight: "100vh", background: "#050810" }}>
        <div style={{ width: 200, background: "rgba(5,8,16,0.98)", borderRight: "1px solid #0d1f2d" }} />
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span style={{ color: "#00D4FF", fontFamily: "JetBrains Mono, monospace", fontSize: 12 }}>Loading…</span>
        </div>
      </div>
    );
  }

  const card = {
    background: "rgba(7,12,24,0.85)", border: "1px solid #1a2a3a",
    borderRadius: 12, padding: 24,
  } as React.CSSProperties;

  const labelStyle = { fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#4a6a8a", letterSpacing: "0.08em", textTransform: "uppercase" as const, marginBottom: 6 };
  const valueStyle = { fontFamily: "JetBrains Mono, monospace", fontSize: 13, color: "#e8f4fd" };
  const inputStyle = {
    background: "rgba(0,212,255,0.05)", border: "1px solid #1a2a3a", borderRadius: 6,
    color: "#e8f4fd", fontFamily: "JetBrains Mono, monospace", fontSize: 13,
    padding: "8px 12px", outline: "none", width: "100%", boxSizing: "border-box" as const,
  };
  const btnPrimary = (disabled: boolean) => ({
    background: disabled ? "#1a2a3a" : "linear-gradient(135deg,#00D4FF,#0066CC)",
    color: disabled ? "#4a6a8a" : "#fff", border: "none", borderRadius: 8, padding: "10px 20px",
    fontFamily: "JetBrains Mono, monospace", fontSize: 13, fontWeight: 700, cursor: disabled ? "not-allowed" : "pointer",
    transition: "all 0.15s", letterSpacing: "0.06em",
  } as React.CSSProperties);

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#050810", fontFamily: "JetBrains Mono, monospace" }}>
      <InlineSidebar />

      <main style={{ flex: 1, padding: "32px 28px", display: "flex", flexDirection: "column", gap: 24, overflowY: "auto" }}>

        {/* ── Header ── */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "#e8f4fd", letterSpacing: "0.06em" }}>ORDER MANAGEMENT</h1>
            <p style={{ margin: "4px 0 0", fontSize: 11, color: "#4a6a8a" }}>Manual trading · Emergency controls · Order history</p>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 11, color: "#4a6a8a" }}>MODE</span>
            {(["paper", "live"] as const).map(m => (
              <button key={m} onClick={() => setFilterMode(m)} style={{
                background: filterMode === m ? (m === "live" ? "#FF2D55" : "#00D4FF") + "22" : "transparent",
                border: `1px solid ${filterMode === m ? (m === "live" ? "#FF2D55" : "#00D4FF") : "#1a2a3a"}`,
                color: filterMode === m ? (m === "live" ? "#FF2D55" : "#00D4FF") : "#4a6a8a",
                borderRadius: 6, padding: "5px 14px", cursor: "pointer",
                fontFamily: "JetBrains Mono, monospace", fontSize: 12, fontWeight: 600,
                textTransform: "uppercase",
              }}>{m}</button>
            ))}
            <button onClick={fetchOrders} style={{
              background: "transparent", border: "1px solid #1a2a3a", borderRadius: 6,
              color: "#00D4FF", padding: "5px 12px", cursor: "pointer",
              fontFamily: "JetBrains Mono, monospace", fontSize: 11,
            }}>↺ REFRESH</button>
          </div>
        </div>

        {/* ── EMERGENCY STOP ── */}
        <div style={{
          ...card,
          border: killActive ? "1px solid #FF2D55" : "1px solid #2a1a1a",
          background: killActive ? "rgba(255,45,85,0.12)" : "rgba(255,45,85,0.04)",
          padding: "20px 24px",
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
              {/* Pulsing indicator */}
              <div style={{
                width: 14, height: 14, borderRadius: "50%",
                background: killActive ? "#FF2D55" : "#1a2a3a",
                boxShadow: killActive ? "0 0 12px #FF2D55" : "none",
                animation: killActive ? "pulse 1.5s infinite" : "none",
                flexShrink: 0,
              }} />
              <div>
                <div style={{ fontSize: 15, fontWeight: 700, color: killActive ? "#FF2D55" : "#e8f4fd", letterSpacing: "0.1em" }}>
                  EMERGENCY STOP {killActive ? "— ARMED" : "— STANDBY"}
                </div>
                <div style={{ fontSize: 11, color: "#6a8aa8", marginTop: 3 }}>
                  {killActive
                    ? "All automated & manual trading is BLOCKED. Deactivate to resume."
                    : "Press to immediately halt all trading activity (automated + manual)."}
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              {killActive ? (
                <button
                  onClick={() => toggleKillSwitch(false)}
                  disabled={killLoading}
                  style={{
                    background: killLoading ? "#1a2a3a" : "rgba(0,255,136,0.15)",
                    border: "1px solid #00FF88",
                    color: killLoading ? "#4a6a8a" : "#00FF88",
                    borderRadius: 8, padding: "10px 28px",
                    fontFamily: "JetBrains Mono, monospace", fontSize: 13, fontWeight: 700,
                    cursor: killLoading ? "not-allowed" : "pointer", letterSpacing: "0.06em",
                  }}
                >
                  {killLoading ? "…" : "✓ DEACTIVATE KILL SWITCH"}
                </button>
              ) : (
                <button
                  onClick={() => toggleKillSwitch(true)}
                  disabled={killLoading}
                  style={{
                    background: killLoading ? "#1a2a3a" : "#FF2D55",
                    border: "none",
                    color: killLoading ? "#4a6a8a" : "#fff",
                    borderRadius: 8, padding: "10px 28px",
                    fontFamily: "JetBrains Mono, monospace", fontSize: 13, fontWeight: 700,
                    cursor: killLoading ? "not-allowed" : "pointer", letterSpacing: "0.06em",
                    boxShadow: killLoading ? "none" : "0 0 18px rgba(255,45,85,0.5)",
                  }}
                >
                  {killLoading ? "…" : "⛔ EMERGENCY STOP"}
                </button>
              )}
            </div>
          </div>
          {killMsg && (
            <div style={{
              marginTop: 12, padding: "8px 14px", borderRadius: 6,
              background: killActive ? "rgba(255,45,85,0.15)" : "rgba(0,255,136,0.08)",
              border: `1px solid ${killActive ? "#FF2D55" : "#00FF88"}`,
              fontSize: 12, color: killActive ? "#FF2D55" : "#00FF88",
            }}>
              {killMsg}
            </div>
          )}
        </div>

        {/* ── Two-column: Place Order + Active Orders ── */}
        <div style={{ display: "grid", gridTemplateColumns: "380px 1fr", gap: 20 }}>

          {/* Place Order Form */}
          <div style={card}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#e8f4fd", letterSpacing: "0.08em", marginBottom: 20 }}>PLACE ORDER</div>

            {/* Account mode */}
            <div style={{ marginBottom: 16 }}>
              <div style={labelStyle}>Account</div>
              <div style={{ display: "flex", gap: 8 }}>
                {(["paper", "live"] as const).map(m => (
                  <button key={m} onClick={() => setAccountMode(m)} style={{
                    flex: 1, background: accountMode === m ? (m === "live" ? "#FF2D55" : "#00D4FF") + "22" : "transparent",
                    border: `1px solid ${accountMode === m ? (m === "live" ? "#FF2D55" : "#00D4FF") : "#1a2a3a"}`,
                    color: accountMode === m ? (m === "live" ? "#FF2D55" : "#00D4FF") : "#4a6a8a",
                    borderRadius: 6, padding: "7px 0", cursor: "pointer",
                    fontFamily: "JetBrains Mono, monospace", fontSize: 12, fontWeight: 600, textTransform: "uppercase",
                  }}>{m}{m === "live" ? " ⚠" : ""}</button>
                ))}
              </div>
              {accountMode === "live" && (
                <div style={{ marginTop: 8, fontSize: 11, color: "#FF2D55", padding: "6px 10px", background: "rgba(255,45,85,0.08)", borderRadius: 6, border: "1px solid #FF2D5540" }}>
                  ⚠ LIVE mode uses real money
                </div>
              )}
            </div>

            {/* Symbol */}
            <div style={{ marginBottom: 14 }}>
              <div style={labelStyle}>Symbol</div>
              <select value={symbol} onChange={e => setSymbol(e.target.value)} style={{ ...inputStyle }}>
                {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            {/* Side */}
            <div style={{ marginBottom: 14 }}>
              <div style={labelStyle}>Side</div>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => setSide("buy")} style={{
                  flex: 1, background: side === "buy" ? "rgba(0,255,136,0.15)" : "transparent",
                  border: `1px solid ${side === "buy" ? "#00FF88" : "#1a2a3a"}`,
                  color: side === "buy" ? "#00FF88" : "#4a6a8a",
                  borderRadius: 6, padding: "8px 0", cursor: "pointer",
                  fontFamily: "JetBrains Mono, monospace", fontSize: 13, fontWeight: 700,
                }}>↑ BUY</button>
                <button onClick={() => setSide("sell")} style={{
                  flex: 1, background: side === "sell" ? "rgba(255,45,85,0.15)" : "transparent",
                  border: `1px solid ${side === "sell" ? "#FF2D55" : "#1a2a3a"}`,
                  color: side === "sell" ? "#FF2D55" : "#4a6a8a",
                  borderRadius: 6, padding: "8px 0", cursor: "pointer",
                  fontFamily: "JetBrains Mono, monospace", fontSize: 13, fontWeight: 700,
                }}>↓ SELL</button>
              </div>
            </div>

            {/* Quantity */}
            <div style={{ marginBottom: 14 }}>
              <div style={labelStyle}>Quantity (shares)</div>
              <input
                type="number" min="1" step="1" value={qty}
                onChange={e => setQty(e.target.value)}
                style={inputStyle}
              />
            </div>

            {/* Order type */}
            <div style={{ marginBottom: 14 }}>
              <div style={labelStyle}>Order Type</div>
              <div style={{ display: "flex", gap: 8 }}>
                {(["market", "limit"] as const).map(t => (
                  <button key={t} onClick={() => setOrderType(t)} style={{
                    flex: 1, background: orderType === t ? "rgba(0,212,255,0.12)" : "transparent",
                    border: `1px solid ${orderType === t ? "#00D4FF" : "#1a2a3a"}`,
                    color: orderType === t ? "#00D4FF" : "#4a6a8a",
                    borderRadius: 6, padding: "7px 0", cursor: "pointer",
                    fontFamily: "JetBrains Mono, monospace", fontSize: 12, fontWeight: 600, textTransform: "uppercase",
                  }}>{t}</button>
                ))}
              </div>
            </div>

            {/* Limit price */}
            {orderType === "limit" && (
              <div style={{ marginBottom: 14 }}>
                <div style={labelStyle}>Limit Price ($)</div>
                <input
                  type="number" min="0.01" step="0.01" value={limitPrice}
                  onChange={e => setLimitPrice(e.target.value)}
                  placeholder="e.g. 150.00"
                  style={inputStyle}
                />
              </div>
            )}

            {/* Kill switch warning */}
            {killActive && (
              <div style={{ marginBottom: 14, padding: "8px 12px", borderRadius: 6, background: "rgba(255,45,85,0.12)", border: "1px solid #FF2D55", fontSize: 11, color: "#FF2D55" }}>
                ⛔ Kill switch is ARMED — deactivate it before placing orders
              </div>
            )}

            {/* Confirm checkbox */}
            <div style={{ marginBottom: 18, display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }} onClick={() => setConfirmed(c => !c)}>
              <div style={{
                width: 18, height: 18, borderRadius: 4,
                border: `2px solid ${confirmed ? "#00D4FF" : "#1a2a3a"}`,
                background: confirmed ? "rgba(0,212,255,0.2)" : "transparent",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0, transition: "all 0.15s",
              }}>
                {confirmed && <span style={{ color: "#00D4FF", fontSize: 11, fontWeight: 700 }}>✓</span>}
              </div>
              <span style={{ fontSize: 11, color: "#6a8aa8" }}>
                I confirm this {side.toUpperCase()} order for <strong style={{ color: "#e8f4fd" }}>{safeNum(qty)} {symbol}</strong>
                {orderType === "limit" && limitPrice ? ` at $${limitPrice}` : " at market price"}
              </span>
            </div>

            {/* Submit */}
            <button
              onClick={placeOrder}
              disabled={tradeLoading || !confirmed || killActive}
              style={{
                ...btnPrimary(tradeLoading || !confirmed || killActive),
                width: "100%",
                background: side === "buy" && confirmed && !tradeLoading && !killActive
                  ? "linear-gradient(135deg,#00FF88,#00AA55)"
                  : side === "sell" && confirmed && !tradeLoading && !killActive
                  ? "linear-gradient(135deg,#FF2D55,#CC0033)"
                  : "#1a2a3a",
              }}
            >
              {tradeLoading ? "Submitting…" : `SUBMIT ${side.toUpperCase()} ORDER`}
            </button>

            {tradeMsg && (
              <div style={{
                marginTop: 12, padding: "8px 12px", borderRadius: 6, fontSize: 12,
                background: tradeMsg.ok ? "rgba(0,255,136,0.08)" : "rgba(255,45,85,0.08)",
                border: `1px solid ${tradeMsg.ok ? "#00FF88" : "#FF2D55"}`,
                color: tradeMsg.ok ? "#00FF88" : "#FF2D55",
              }}>
                {tradeMsg.text}
              </div>
            )}
          </div>

          {/* Active / Open Orders */}
          <div style={card}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#e8f4fd", letterSpacing: "0.08em" }}>
                OPEN ORDERS
                <span style={{ marginLeft: 10, fontSize: 11, color: "#00D4FF", background: "rgba(0,212,255,0.12)", borderRadius: 20, padding: "2px 10px" }}>
                  {openOrders.length}
                </span>
              </div>
              {ordersLoading && <span style={{ fontSize: 11, color: "#4a6a8a" }}>Refreshing…</span>}
            </div>

            {openOrders.length === 0 ? (
              <div style={{ color: "#4a6a8a", fontSize: 12, textAlign: "center", padding: "32px 0" }}>
                No open orders
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #1a2a3a" }}>
                      {["Symbol", "Side", "Qty", "Filled", "Type", "Status", "Submitted", ""].map(h => (
                        <th key={h} style={{ ...labelStyle, textAlign: "left", padding: "4px 10px", fontWeight: 600 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {openOrders.map(o => (
                      <tr key={o.id} style={{ borderBottom: "1px solid #0d1f2d" }}>
                        <td style={{ ...valueStyle, padding: "9px 10px", fontWeight: 700 }}>{o.symbol}</td>
                        <td style={{ padding: "9px 10px", color: o.side === "buy" ? "#00FF88" : "#FF2D55", fontFamily: "JetBrains Mono, monospace", fontSize: 12, fontWeight: 700 }}>
                          {o.side.toUpperCase()}
                        </td>
                        <td style={{ ...valueStyle, padding: "9px 10px" }}>{o.qty}</td>
                        <td style={{ ...valueStyle, padding: "9px 10px", color: "#6a8aa8" }}>{o.filled_qty ?? 0}</td>
                        <td style={{ ...valueStyle, padding: "9px 10px", color: "#6a8aa8", textTransform: "uppercase" }}>{o.order_type ?? "—"}</td>
                        <td style={{ padding: "9px 10px" }}>
                          <span style={{
                            fontSize: 10, fontFamily: "JetBrains Mono, monospace", fontWeight: 600,
                            color: getStatusColor(o.status), background: getStatusColor(o.status) + "18",
                            borderRadius: 20, padding: "2px 8px", textTransform: "uppercase",
                          }}>{o.status.replace(/_/g, " ")}</span>
                        </td>
                        <td style={{ ...valueStyle, padding: "9px 10px", color: "#6a8aa8", fontSize: 11 }}>{fmtDate(o.submitted_at)}</td>
                        <td style={{ padding: "9px 10px" }}>
                          <button
                            onClick={() => cancelOrder(o.id)}
                            disabled={cancelingId === o.id}
                            style={{
                              background: "transparent", border: "1px solid #FF2D55",
                              color: cancelingId === o.id ? "#4a6a8a" : "#FF2D55",
                              borderRadius: 5, padding: "3px 10px", cursor: "pointer",
                              fontFamily: "JetBrains Mono, monospace", fontSize: 11,
                            }}
                          >
                            {cancelingId === o.id ? "…" : "✕ CANCEL"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* ── Order History ── */}
        <div style={card}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#e8f4fd", letterSpacing: "0.08em", marginBottom: 16 }}>
            RECENT ORDER HISTORY
            <span style={{ marginLeft: 10, fontSize: 11, color: "#6a8aa8", background: "rgba(106,138,168,0.1)", borderRadius: 20, padding: "2px 10px" }}>
              last 25
            </span>
          </div>
          {closedOrders.length === 0 ? (
            <div style={{ color: "#4a6a8a", fontSize: 12, textAlign: "center", padding: "24px 0" }}>No completed orders</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #1a2a3a" }}>
                    {["Symbol", "Side", "Qty", "Filled @ Avg", "Type", "Status", "Source", "Submitted", "Filled"].map(h => (
                      <th key={h} style={{ ...labelStyle, textAlign: "left", padding: "4px 10px", fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {closedOrders.map(o => (
                    <tr key={o.id} style={{ borderBottom: "1px solid #0b1520" }}>
                      <td style={{ ...valueStyle, padding: "8px 10px", fontWeight: 700 }}>{o.symbol}</td>
                      <td style={{ padding: "8px 10px", color: o.side === "buy" ? "#00FF88" : "#FF2D55", fontFamily: "JetBrains Mono, monospace", fontSize: 12, fontWeight: 700 }}>
                        {o.side.toUpperCase()}
                      </td>
                      <td style={{ ...valueStyle, padding: "8px 10px" }}>{o.qty}</td>
                      <td style={{ ...valueStyle, padding: "8px 10px" }}>
                        {o.filled_qty ?? o.qty} @ {fmtPrice(o.filled_avg_price)}
                      </td>
                      <td style={{ ...valueStyle, padding: "8px 10px", color: "#6a8aa8", textTransform: "uppercase" }}>{o.order_type ?? "—"}</td>
                      <td style={{ padding: "8px 10px" }}>
                        <span style={{
                          fontSize: 10, fontFamily: "JetBrains Mono, monospace", fontWeight: 600,
                          color: getStatusColor(o.status), background: getStatusColor(o.status) + "18",
                          borderRadius: 20, padding: "2px 8px", textTransform: "uppercase",
                        }}>{o.status.replace(/_/g, " ")}</span>
                      </td>
                      <td style={{ ...valueStyle, padding: "8px 10px", color: "#6a8aa8" }}>{o.source ?? "manual"}</td>
                      <td style={{ ...valueStyle, padding: "8px 10px", color: "#6a8aa8", fontSize: 11 }}>{fmtDate(o.submitted_at)}</td>
                      <td style={{ ...valueStyle, padding: "8px 10px", color: "#6a8aa8", fontSize: 11 }}>{fmtDate(o.filled_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

      </main>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(1.2); }
        }
        input[type=number]::-webkit-inner-spin-button,
        input[type=number]::-webkit-outer-spin-button { opacity: 0.4; }
        * { box-sizing: border-box; }
      `}</style>
    </div>
  );
}
