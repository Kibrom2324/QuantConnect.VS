"use client";

/**
 * APEX Trading Page — fully self-contained.
 * All buttons, toggles, modals and state live here.
 * Fetches from /api/(account | trading-mode | orders | trade | quote | kill-switch).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  RefreshCw, Zap, TrendingUp, TrendingDown, X, Search,
  Check, AlertTriangle, Settings,
} from "lucide-react";

// ────────────────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────────────────
interface AccountInfo {
  buying_power:    number;
  portfolio_value: number;
  cash:            number;
  day_pnl:         number;
  day_pnl_pct:     number;
  trading_blocked: boolean;
  account_mode:    "paper" | "live";
  is_mock:         boolean;
}

interface TradingConfig {
  auto_trading_enabled:   boolean;
  account_mode:           "paper" | "live";
  min_confidence:         number;
  market_hours_only:      boolean;
  max_position_size_usd:  number;
  max_daily_trades:       number;
  trades_today:           number;
  is_market_open:         boolean;
  live_trading_available: boolean;
  last_updated:           string;
}

interface Order {
  id:               string;
  symbol:           string;
  side:             "buy" | "sell";
  qty:              number;
  status:           string;
  filled_avg_price: number | null;
  filled_qty:       number;
  submitted_at:     string;
  filled_at:        string | null;
  order_type:       string;
  limit_price?:     number | null;
  source?:          string;
}

interface Quote {
  symbol:     string;
  price:      number;
  change:     number;
  change_pct: number;
  name:       string;
  is_mock?:   boolean;
}

// ────────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────────
const fmt = (n: number) =>
  n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function timeAgo(ts: string) {
  const d = (Date.now() - new Date(ts).getTime()) / 1000;
  if (d < 60)    return `${Math.floor(d)}s ago`;
  if (d < 3600)  return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return new Date(ts).toLocaleDateString("en-GB", { month: "short", day: "numeric" });
}

const POPULAR_SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD"];

// ────────────────────────────────────────────────────────────────────────────
// Small reusable components
// ────────────────────────────────────────────────────────────────────────────
function Toggle({
  checked, onChange, disabled, testId,
}: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; testId?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      data-testid={testId}
      onClick={() => !disabled && onChange(!checked)}
      className="relative inline-flex items-center rounded-full transition-all focus:outline-none"
      style={{
        width: 40, height: 22, flexShrink: 0,
        background: checked ? "rgba(0,255,136,0.3)" : "rgba(74,106,138,0.25)",
        border:     `1px solid ${checked ? "rgba(0,255,136,0.6)" : "rgba(74,106,138,0.4)"}`,
        boxShadow:  checked ? "0 0 10px rgba(0,255,136,0.3)" : "none",
        cursor:     disabled ? "not-allowed" : "pointer",
        opacity:    disabled ? 0.5 : 1,
      }}
    >
      <span style={{
        position: "absolute", width: 16, height: 16, borderRadius: "50%",
        background: checked ? "#00FF88" : "#4a6a8a",
        left:       checked ? 21 : 3,
        transition: "left 0.18s ease, background 0.18s ease",
        boxShadow:  checked ? "0 0 6px rgba(0,255,136,0.7)" : "none",
      }} />
    </button>
  );
}

type FilterType = "all" | "auto" | "manual" | "filled" | "cancelled";

function FilterTab({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 rounded font-mono text-[10px] uppercase tracking-wider transition-all"
      style={{
        background: active ? "rgba(0,212,255,0.12)" : "transparent",
        border:     `1px solid ${active ? "rgba(0,212,255,0.4)" : "rgba(0,212,255,0.08)"}`,
        color:      active ? "#00D4FF" : "#4a6a8a",
      }}
    >
      {label}
    </button>
  );
}

function RiskCheck({ label, state }: { label: string; state: "ok" | "fail" | "warn" | "unknown" }) {
  const icon  = state === "ok" ? "✓" : state === "fail" ? "✗" : state === "warn" ? "⚠" : "○";
  const color = state === "ok" ? "#00FF88" : state === "fail" ? "#FF2D55" : state === "warn" ? "#FFB800" : "#4a6a8a";
  return (
    <div className="flex items-center gap-2 text-[10px] font-mono">
      <span style={{ color, fontWeight: 700 }}>{icon}</span>
      <span style={{ color: state === "fail" ? "#FF2D55" : "#8aadcc" }}>{label}</span>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Order Row
// ────────────────────────────────────────────────────────────────────────────
function OrderRow({ order, onCancel }: { order: Order; onCancel?: (id: string) => void }) {
  const isBuy       = order.side === "buy";
  const isFilled    = order.status === "filled";
  const isCancelled = order.status === "cancelled" || order.status === "canceled";
  const isRejected  = order.status === "rejected";
  const isPending   = ["accepted","pending_new","new","partially_filled"].includes(order.status);
  const source      = order.source ?? "manual";
  const total       = (order.filled_avg_price ?? 0) * (order.filled_qty ?? order.qty);

  const borderColor = isCancelled ? "#2a4a6a" : isRejected ? "#FF2D55" :
    isBuy && isFilled ? "#00FF88" : !isBuy && isFilled ? "#00D4FF" : "#2a4a6a";
  const statusColor = isFilled ? "#00FF88" : isCancelled ? "#4a6a8a" :
    isRejected ? "#FF2D55" : isPending ? "#FFB800" : "#4a6a8a";

  return (
    <tr style={{ borderLeft: `3px solid ${borderColor}`, borderBottom: "1px solid rgba(0,212,255,0.05)" }}>
      <td className="font-mono text-[10px] text-[#4a6a8a]">{timeAgo(order.submitted_at)}</td>
      <td><span className="font-mono font-bold text-sm text-white">{order.symbol}</span></td>
      <td>
        <span className="inline-flex items-center gap-1 text-[10px] font-mono font-bold px-2 py-0.5 rounded" style={{
          color:      isBuy ? "#00FF88" : "#FF2D55",
          background: isBuy ? "rgba(0,255,136,0.1)" : "rgba(255,45,85,0.1)",
          border:     `1px solid ${isBuy ? "rgba(0,255,136,0.3)" : "rgba(255,45,85,0.3)"}`,
        }}>
          {isBuy ? <TrendingUp style={{ width: 10 }} /> : <TrendingDown style={{ width: 10 }} />}
          {order.side.toUpperCase()}
        </span>
      </td>
      <td className="font-mono text-[11px] text-[#e2f0ff]">{order.qty}</td>
      <td className="font-mono text-[11px] text-[#8aadcc]">
        {order.filled_avg_price != null ? `$${fmt(order.filled_avg_price)}` : "—"}
      </td>
      <td className="font-mono text-[11px] font-bold tabular-nums" style={{ color: isBuy ? "#00FF88" : "#e2f0ff" }}>
        {total > 0 ? `$${fmt(total)}` : "—"}
      </td>
      <td>
        <span className="text-[9px] font-mono font-bold uppercase px-1.5 py-0.5 rounded" style={{
          color: statusColor, background: `${statusColor}15`, border: `1px solid ${statusColor}35`,
        }}>
          {order.status.replace(/_/g, " ")}
        </span>
      </td>
      <td>
        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded" style={{
          color:      source === "auto" ? "#00D4FF" : "#8B5CF6",
          background: source === "auto" ? "rgba(0,212,255,0.1)" : "rgba(139,92,246,0.1)",
          border:     `1px solid ${source === "auto" ? "rgba(0,212,255,0.25)" : "rgba(139,92,246,0.25)"}`,
        }}>
          {source.toUpperCase()}
        </span>
      </td>
      <td>
        {isPending && onCancel && (
          <button
            onClick={() => onCancel(order.id)}
            className="flex items-center gap-1 text-[9px] font-mono px-2 py-1 rounded transition-all hover:opacity-80"
            style={{ color: "#FF2D55", background: "rgba(255,45,85,0.08)", border: "1px solid rgba(255,45,85,0.25)" }}
          >
            <X style={{ width: 9, height: 9 }} /> Cancel
          </button>
        )}
      </td>
    </tr>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Confirmation modal
// ────────────────────────────────────────────────────────────────────────────
function ConfirmModal({
  title, body, confirmLabel = "CONFIRM", confirmColor = "#00D4FF",
  requirePhrase, onCancel, onConfirm, loading,
}: {
  title:          string;
  body:           React.ReactNode;
  confirmLabel?:  string;
  confirmColor?:  string;
  requirePhrase?: string;
  onCancel:       () => void;
  onConfirm:      () => void;
  loading?:       boolean;
}) {
  const [phrase, setPhrase] = useState("");
  const canConfirm = !requirePhrase || phrase === requirePhrase;

  return (
    <div className="fixed inset-0 z-[500] flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.78)", backdropFilter: "blur(4px)" }}>
      <div className="rounded-lg p-6 max-w-sm w-full mx-4 space-y-4" style={{
        background: "#070c18", border: "1px solid rgba(0,212,255,0.3)",
        boxShadow: "0 0 40px rgba(0,0,0,0.8), 0 0 20px rgba(0,212,255,0.08)",
      }}>
        <h3 className="font-heading text-sm font-bold uppercase tracking-widest" style={{ color: confirmColor }}>
          {title}
        </h3>
        <div className="text-[11px] font-mono text-[#8aadcc] space-y-1">{body}</div>
        {requirePhrase && (
          <div className="space-y-1">
            <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">
              Type <span style={{ color: confirmColor }}>&#34;{requirePhrase}&#34;</span> to confirm
            </div>
            <input
              type="text"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              placeholder={requirePhrase}
              className="w-full px-3 py-2 rounded font-mono text-xs bg-transparent focus:outline-none"
              style={{
                background: "rgba(0,0,0,0.4)",
                border: `1px solid ${canConfirm ? confirmColor + "60" : "rgba(74,106,138,0.4)"}`,
                color: "#e2f0ff",
              }}
            />
          </div>
        )}
        <div className="flex gap-2 pt-1">
          <button onClick={onCancel}
            className="flex-1 py-2 rounded font-mono text-[10px] uppercase font-bold transition-all hover:opacity-80"
            style={{ background: "rgba(74,106,138,0.2)", border: "1px solid rgba(74,106,138,0.3)", color: "#4a6a8a" }}>
            CANCEL
          </button>
          <button
            onClick={onConfirm}
            disabled={!canConfirm || loading}
            className="flex-1 py-2 rounded font-mono text-[10px] uppercase font-bold transition-all"
            style={{
              background: canConfirm && !loading ? `${confirmColor}22` : "rgba(74,106,138,0.1)",
              border:     `1px solid ${canConfirm && !loading ? confirmColor + "60" : "rgba(74,106,138,0.2)"}`,
              color:      canConfirm && !loading ? confirmColor : "#4a6a8a",
              cursor:     canConfirm && !loading ? "pointer" : "not-allowed",
            }}
          >
            {loading ? "SENDING…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Page component
// ────────────────────────────────────────────────────────────────────────────
export default function TradingPage() {
  // ── global state ──────────────────────────────────────────────────
  const [refreshKey,  setRefreshKey]  = useState(0);
  const [account,     setAccount]     = useState<AccountInfo | null>(null);
  const [cfg,         setCfg]         = useState<TradingConfig | null>(null);
  const [orders,      setOrders]      = useState<Order[]>([]);
  const [pageLoading, setPageLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [killActive,  setKillActive]  = useState(false);

  // ── auto trading panel state ────────────────────────────────────────
  const [autoSaving,      setAutoSaving]      = useState(false);
  const [autoModalType,   setAutoModalType]   = useState<"live-switch" | "auto-on-live" | null>(null);
  const [pendingAutoMode, setPendingAutoMode] = useState<"paper" | "live" | null>(null);
  const [settingsChanged, setSettingsChanged] = useState(false);
  const [localMinConf,    setLocalMinConf]    = useState(70);
  const [localMktHours,   setLocalMktHours]   = useState(true);
  const [localMaxPos,     setLocalMaxPos]     = useState(5000);
  const [localMaxTrades,  setLocalMaxTrades]  = useState(10);
  const settingsInitRef = useRef(false);

  // ── manual trade panel state ────────────────────────────────────────
  const [symbol,         setSymbol]        = useState("");
  const [quote,          setQuote]         = useState<Quote | null>(null);
  const [quoteLoading,   setQuoteLoading]  = useState(false);
  const [symbolInput,    setSymbolInput]   = useState("");
  const [showSymbolDrop, setShowSymbolDrop]= useState(false);
  const [side,           setSide]          = useState<"buy" | "sell">("buy");
  const [qty,            setQty]           = useState<string>("");
  const [orderType,      setOrderType]     = useState<"market" | "limit">("market");
  const [limitPrice,     setLimitPrice]    = useState<string>("");
  const [showPreview,    setShowPreview]   = useState(false);
  const [submitLoading,  setSubmitLoading] = useState(false);
  const [lastResult,     setLastResult]    = useState<{ id: string; status: string; price: number | null } | null>(null);
  const [submitError,    setSubmitError]   = useState("");
  const symbolDropRef = useRef<HTMLDivElement>(null);
  const quoteDebRef   = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── order history filter ────────────────────────────────────────────
  const [filter, setFilter] = useState<FilterType>("all");

  // ── derived ─────────────────────────────────────────────────────────
  const accountMode = cfg?.account_mode ?? "paper";
  const isLive      = accountMode === "live";
  const autoOn      = cfg?.auto_trading_enabled ?? false;
  const dayPnl      = account?.day_pnl     ?? 0;
  const dayPnlPct   = account?.day_pnl_pct ?? 0;
  const isMock      = account?.is_mock     ?? true;
  const tradesDone  = cfg?.trades_today    ?? 0;
  const maxTrades   = cfg?.max_daily_trades ?? 10;

  // ── data fetching ────────────────────────────────────────────────────
  const loadAll = useCallback(async () => {
    setPageLoading(true);
    try {
      const [accRes, cfgRes, ordRes, killRes] = await Promise.allSettled([
        fetch(`/api/account?account_mode=${accountMode}`).then((r) => r.json()),
        fetch("/api/trading-mode").then((r) => r.json()),
        fetch(`/api/orders?limit=50&account_mode=${accountMode}`).then((r) => r.json()),
        fetch("/api/kill-switch").then((r) => r.json()),
      ]);
      if (accRes.status  === "fulfilled") setAccount(accRes.value  as AccountInfo);
      if (cfgRes.status  === "fulfilled") {
        const c = cfgRes.value as TradingConfig;
        setCfg(c);
        if (!settingsInitRef.current) {
          setLocalMinConf(c.min_confidence);
          setLocalMktHours(c.market_hours_only);
          setLocalMaxPos(c.max_position_size_usd);
          setLocalMaxTrades(c.max_daily_trades);
          settingsInitRef.current = true;
        }
      }
      if (ordRes.status  === "fulfilled") setOrders((ordRes.value as { orders?: Order[] }).orders ?? []);
      if (killRes.status === "fulfilled") setKillActive(!!(killRes.value as { active?: boolean }).active);
    } finally {
      setPageLoading(false);
      setLastUpdated(new Date());
    }
  }, [accountMode]);

  useEffect(() => { loadAll(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // auto-refresh every 15 s
  useEffect(() => {
    const id = setInterval(() => setRefreshKey((k) => k + 1), 15_000);
    return () => clearInterval(id);
  }, []);

  // close symbol dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (symbolDropRef.current && !symbolDropRef.current.contains(e.target as Node))
        setShowSymbolDrop(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // ── quote lookup ─────────────────────────────────────────────────────
  const lookupQuote = useCallback((sym: string) => {
    if (!sym.trim()) { setQuote(null); return; }
    setQuoteLoading(true);
    fetch(`/api/quote?symbol=${encodeURIComponent(sym.trim().toUpperCase())}`)
      .then((r) => r.json())
      .then((d: Quote) => { if (d.price != null) setQuote(d); else setQuote(null); })
      .catch(() => setQuote(null))
      .finally(() => setQuoteLoading(false));
  }, []);

  const handleSymbolInput = (v: string) => {
    setSymbolInput(v);
    setShowSymbolDrop(true);
    if (quoteDebRef.current) clearTimeout(quoteDebRef.current);
    quoteDebRef.current = setTimeout(() => {
      const upper = v.trim().toUpperCase();
      if (upper) { setSymbol(upper); lookupQuote(upper); }
      else { setSymbol(""); setQuote(null); }
    }, 300);
  };

  const selectSymbol = (sym: string) => {
    setSymbolInput(sym);
    setSymbol(sym);
    setShowSymbolDrop(false);
    lookupQuote(sym);
  };

  // ── risk checks ──────────────────────────────────────────────────────
  const qtyNum   = parseFloat(qty) || 0;
  const priceNow = quote?.price ?? 0;
  const estValue = qtyNum * (orderType === "limit" && limitPrice ? parseFloat(limitPrice) || priceNow : priceNow);
  const maxPos   = cfg?.max_position_size_usd ?? 5000;
  const buyPower = account?.buying_power ?? 0;

  const riskChecks = {
    kill:     !killActive,
    position: maxPos > 0 ? estValue <= maxPos : true,
    market:   cfg?.is_market_open ?? false,
    buying:   buyPower > 0 ? estValue <= buyPower : true,
  };

  const canPreview = symbol.length > 0 && qtyNum > 0
    && riskChecks.kill && riskChecks.position && riskChecks.buying;

  // ── submit order ─────────────────────────────────────────────────────
  const submitOrder = async () => {
    setSubmitLoading(true);
    setSubmitError("");
    try {
      const body: Record<string, unknown> = {
        symbol, side, qty: qtyNum, order_type: orderType, account_mode: accountMode, confirmed: true,
      };
      if (orderType === "limit" && limitPrice) body.limit_price = parseFloat(limitPrice);
      const res  = await fetch("/api/trade", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { setSubmitError(data.error ?? "Order failed"); setShowPreview(false); return; }
      setLastResult({ id: data.id, status: data.status, price: data.filled_avg_price ?? null });
      setShowPreview(false);
      setSymbol(""); setSymbolInput(""); setQuote(null);
      setQty(""); setLimitPrice(""); setSide("buy");
      setRefreshKey((k) => k + 1);
    } catch {
      setSubmitError("Network error — could not reach API");
      setShowPreview(false);
    } finally {
      setSubmitLoading(false);
    }
  };

  // ── cancel order ──────────────────────────────────────────────────────
  const cancelOrder = async (id: string) => {
    try {
      await fetch(`/api/orders?id=${id}&account_mode=${accountMode}`, { method: "DELETE" });
      setRefreshKey((k) => k + 1);
    } catch { /* silent */ }
  };

  // ── auto trading helpers ──────────────────────────────────────────────
  const updateTradingMode = async (update: Partial<TradingConfig>) => {
    setAutoSaving(true);
    try {
      const res = await fetch("/api/trading-mode", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update),
      });
      if (res.ok) setCfg(await res.json() as TradingConfig);
    } catch { /* silent */ } finally { setAutoSaving(false); }
  };

  const handleAutoToggle = (newVal: boolean) => {
    if (newVal && isLive) { setAutoModalType("auto-on-live"); return; }
    if (cfg) setCfg({ ...cfg, auto_trading_enabled: newVal });
    updateTradingMode({ auto_trading_enabled: newVal });
  };

  const handleAccountModeSwitch = (mode: "paper" | "live") => {
    if (mode === "live" && !cfg?.live_trading_available) return;
    if (mode === accountMode) return;
    setPendingAutoMode(mode);
    setAutoModalType("live-switch");
  };

  const confirmAccountModeSwitch = () => {
    if (!pendingAutoMode) return;
    if (cfg) setCfg({ ...cfg, account_mode: pendingAutoMode, auto_trading_enabled: false });
    updateTradingMode({ account_mode: pendingAutoMode, auto_trading_enabled: false });
    setAutoModalType(null); setPendingAutoMode(null);
  };

  const saveSettings = () => {
    updateTradingMode({
      min_confidence: localMinConf, market_hours_only: localMktHours,
      max_position_size_usd: localMaxPos, max_daily_trades: localMaxTrades,
    });
    setSettingsChanged(false);
  };

  // ── filtered orders ────────────────────────────────────────────────
  const filteredOrders = useMemo(() => {
    switch (filter) {
      case "auto":      return orders.filter((o) => o.source === "auto");
      case "manual":    return orders.filter((o) => !o.source || o.source === "manual");
      case "filled":    return orders.filter((o) => o.status === "filled");
      case "cancelled": return orders.filter((o) => o.status === "cancelled" || o.status === "canceled");
      default:          return orders;
    }
  }, [orders, filter]);

  // ── render ─────────────────────────────────────────────────────────
  return (
    <div className="space-y-5">

      {/* ══════════════════════════════════════════ HEADER */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="font-heading text-2xl font-bold uppercase tracking-widest flex items-center gap-3"
            style={{ color: "#00D4FF", textShadow: "0 0 20px rgba(0,212,255,0.5)" }}>
            <Zap style={{ width: 22, height: 22 }} />
            Trading
            {autoOn && (
              <span className="text-[10px] font-mono font-bold px-2 py-1 rounded animate-pulse" style={{
                color:      isLive ? "#FFB800" : "#00FF88",
                background: isLive ? "rgba(255,184,0,0.12)" : "rgba(0,255,136,0.1)",
                border:     `1px solid ${isLive ? "rgba(255,184,0,0.4)" : "rgba(0,255,136,0.3)"}`,
              }}>
                ⚡ AUTO {isLive ? "LIVE" : "PAPER"}
              </span>
            )}
            {killActive && (
              <span className="text-[10px] font-mono font-bold px-2 py-1 rounded" style={{
                color: "#FF2D55", background: "rgba(255,45,85,0.12)", border: "1px solid rgba(255,45,85,0.4)",
              }}>
                🛑 KILL SWITCH ACTIVE
              </span>
            )}
          </h1>
          <p className="text-[10px] font-mono text-[#4a6a8a] mt-0.5">
            {isLive ? "⚠ Live account — real money at risk" : "Paper trading — simulated fills"}&nbsp;·&nbsp;
            <span style={{ color: isMock ? "#FFB800" : "#00FF88" }}>
              {isMock ? "○ DEMO DATA" : "● LIVE ALPACA"}
            </span>&nbsp;·&nbsp;
            Last updated {lastUpdated.toLocaleTimeString()}
          </p>
        </div>
        <button
          onClick={() => setRefreshKey((k) => k + 1)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded font-mono text-[10px] uppercase tracking-wider transition-all hover:opacity-80"
          style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.25)", color: "#00D4FF" }}
        >
          <RefreshCw style={{ width: 11, height: 11 }} />
          Refresh
        </button>
      </div>

      {/* ══════════════════════════════════════════ ACCOUNT CARDS */}
      <div className="flex gap-3 flex-wrap">
        {/* Card 1 */}
        <div className="apex-card flex-1 min-w-[160px]" style={{ borderLeft: `2px solid ${isLive ? "#FF2D5560" : "#00D4FF60"}` }}>
          <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-2">Account Mode</div>
          <div className="font-heading text-3xl font-black uppercase tracking-widest" style={{
            color: isLive ? "#FF2D55" : "#00D4FF",
            textShadow: `0 0 20px ${isLive ? "rgba(255,45,85,0.4)" : "rgba(0,212,255,0.4)"}`,
          }}>
            {isLive ? "LIVE" : "PAPER"}
          </div>
          <div className="font-mono text-[10px] mt-1 text-[#4a6a8a]">
            Buying Power:{" "}<span className="text-[#e2f0ff] font-bold">${account ? fmt(account.buying_power) : "—"}</span>
          </div>
          <div className="font-mono text-[10px] text-[#4a6a8a]">
            Portfolio:{" "}<span className="text-[#e2f0ff] font-bold">${account ? fmt(account.portfolio_value) : "—"}</span>
          </div>
          {pageLoading && <div className="text-[8px] font-mono text-[#2a4a6a] mt-0.5 animate-pulse">loading…</div>}
        </div>

        {/* Card 2 */}
        <div className="apex-card flex-1 min-w-[160px]" style={{ borderLeft: `2px solid ${autoOn ? "#00FF8860" : "#4a6a8a60"}` }}>
          <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-2">Auto Trading</div>
          <div className="flex items-center gap-2">
            <span className="font-heading text-xl font-bold uppercase tracking-wider" style={{ color: autoOn ? "#00FF88" : "#4a6a8a" }}>
              {autoOn ? "ACTIVE" : "PAUSED"}
            </span>
            {autoOn && <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#00FF88", boxShadow: "0 0 6px rgba(0,255,136,0.8)" }} />}
          </div>
          <div className="font-mono text-[10px] mt-1 text-[#4a6a8a]">
            Trades today:{" "}
            <span style={{ color: tradesDone >= maxTrades ? "#FF2D55" : "#e2f0ff", fontWeight: 700 }}>
              {tradesDone}/{maxTrades}
            </span>
          </div>
          <div className="font-mono text-[10px] text-[#4a6a8a]">
            Market:{" "}<span style={{ color: cfg?.is_market_open ? "#00FF88" : "#4a6a8a" }}>{cfg?.is_market_open ? "OPEN" : "CLOSED"}</span>
          </div>
        </div>

        {/* Card 3 — real day P&L from Alpaca equity diff */}
        <div className="apex-card flex-1 min-w-[160px]" style={{ borderLeft: `2px solid ${dayPnl >= 0 ? "#00FF8860" : "#FF2D5560"}` }}>
          <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-2">Today&#39;s P&amp;L</div>
          <div className="font-heading text-2xl font-bold tabular-nums" style={{
            color: dayPnl >= 0 ? "#00FF88" : "#FF2D55",
            textShadow: `0 0 12px ${dayPnl >= 0 ? "rgba(0,255,136,0.4)" : "rgba(255,45,85,0.4)"}`,
          }}>
            {dayPnl >= 0 ? "+" : ""}${fmt(Math.abs(dayPnl))}
          </div>
          <div className="font-mono text-[10px] mt-1 text-[#4a6a8a]">
            <span style={{ color: dayPnlPct >= 0 ? "#00FF88" : "#FF2D55" }}>
              {dayPnlPct >= 0 ? "+" : ""}{dayPnlPct.toFixed(2)}%
            </span>{" "}today
          </div>
          <div className="font-mono text-[9px] text-[#2a4a6a] mt-0.5">
            {isMock ? "Simulated" : "Alpaca equity diff"}
          </div>
        </div>
      </div>

      {/* ══════════════════════════════════════ AUTO + MANUAL PANELS */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

        {/* ─── AUTO TRADING PANEL ─────────────────────────────────── */}
        <div className="lg:col-span-3 apex-card space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap style={{ width: 14, height: 14, color: "#00D4FF" }} />
              <span className="font-heading text-sm font-bold uppercase tracking-widest text-[#e2f0ff]">
                Auto Trading
              </span>
              {autoSaving && <span className="text-[8px] font-mono text-[#4a6a8a] animate-pulse">saving…</span>}
            </div>
            {killActive && (
              <span className="text-[9px] font-mono px-2 py-1 rounded flex items-center gap-1" style={{
                color: "#FF2D55", background: "rgba(255,45,85,0.12)", border: "1px solid rgba(255,45,85,0.3)",
              }}>
                <AlertTriangle style={{ width: 9, height: 9 }} /> KILL SWITCH ON
              </span>
            )}
          </div>

          {/* Paper / Live selector */}
          <div className="flex items-center gap-1 p-1 rounded" style={{
            background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.1)", width: "fit-content",
          }}>
            {(["paper", "live"] as const).map((m) => {
              const active       = accountMode === m;
              const liveDisabled = m === "live" && !cfg?.live_trading_available;
              return (
                <button key={m} onClick={() => handleAccountModeSwitch(m)} disabled={liveDisabled}
                  className="px-3 py-1 rounded font-mono text-[10px] uppercase font-bold tracking-wider transition-all"
                  style={{
                    background: active ? (m === "live" ? "rgba(255,45,85,0.2)" : "rgba(0,212,255,0.15)") : "transparent",
                    border:     `1px solid ${active ? (m === "live" ? "rgba(255,45,85,0.5)" : "rgba(0,212,255,0.5)") : "transparent"}`,
                    color:      active ? (m === "live" ? "#FF2D55" : "#00D4FF") : "#4a6a8a",
                    cursor:     liveDisabled ? "not-allowed" : "pointer",
                    opacity:    liveDisabled ? 0.4 : 1,
                  }}
                  title={liveDisabled ? "ALPACA_LIVE_KEY not configured" : undefined}
                >
                  {m.toUpperCase()}{liveDisabled ? " 🔒" : ""}
                </button>
              );
            })}
          </div>

          {/* Big toggle */}
          <div className="flex items-center justify-between p-4 rounded" style={{
            background: autoOn ? "rgba(0,255,136,0.05)" : "rgba(0,0,0,0.2)",
            border:     `1px solid ${autoOn ? "rgba(0,255,136,0.2)" : "rgba(74,106,138,0.2)"}`,
          }}>
            <div>
              <div className="font-mono text-sm font-bold" style={{ color: autoOn ? "#00FF88" : "#4a6a8a" }}>
                {autoOn ? "⚡ ENGINE RUNNING" : "ENGINE STOPPED"}
              </div>
              <div className="font-mono text-[9px] mt-0.5 text-[#4a6a8a]">
                {killActive ? "Kill switch prevents starting"
                  : autoOn ? "Evaluating signals every 30s"
                  : "Click toggle to start auto trading"}
              </div>
            </div>
            <Toggle checked={autoOn} onChange={handleAutoToggle} disabled={killActive || autoSaving} testId="auto-trading-toggle" />
          </div>

          {/* Settings */}
          <div className="space-y-3">
            <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] flex items-center gap-1">
              <Settings style={{ width: 9, height: 9 }} /> Settings
            </div>

            {/* Confidence slider */}
            <div className="space-y-1">
              <div className="flex justify-between">
                <span className="font-mono text-[10px] text-[#8aadcc]">Min Confidence</span>
                <span className="font-mono text-[10px] font-bold" style={{ color: "#00D4FF" }}>{localMinConf}%</span>
              </div>
              <input type="range" min={50} max={95} step={1} value={localMinConf}
                onChange={(e) => { setLocalMinConf(parseInt(e.target.value)); setSettingsChanged(true); }}
                className="w-full h-1 rounded cursor-pointer appearance-none"
                style={{ accentColor: "#00D4FF" }}
              />
              <div className="flex justify-between font-mono text-[8px] text-[#2a4a6a]">
                <span>50%</span><span>95%</span>
              </div>
            </div>

            {/* Market hours */}
            <div className="flex items-center justify-between">
              <span className="font-mono text-[10px] text-[#8aadcc]">Market Hours Only</span>
              <Toggle checked={localMktHours} onChange={(v) => { setLocalMktHours(v); setSettingsChanged(true); }} />
            </div>

            {/* Max pos + max trades */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <div className="font-mono text-[10px] text-[#8aadcc]">Max Position ($)</div>
                <input type="number" min={100} max={50000} step={100} value={localMaxPos}
                  onChange={(e) => { setLocalMaxPos(parseInt(e.target.value) || 0); setSettingsChanged(true); }}
                  className="w-full px-2 py-1.5 rounded font-mono text-xs bg-transparent focus:outline-none"
                  style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.2)", color: "#e2f0ff" }}
                />
              </div>
              <div className="space-y-1">
                <div className="font-mono text-[10px] text-[#8aadcc]">Max Daily Trades</div>
                <input type="number" min={1} max={100} step={1} value={localMaxTrades}
                  onChange={(e) => { setLocalMaxTrades(parseInt(e.target.value) || 1); setSettingsChanged(true); }}
                  className="w-full px-2 py-1.5 rounded font-mono text-xs bg-transparent focus:outline-none"
                  style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.2)", color: "#e2f0ff" }}
                />
              </div>
            </div>

            {/* Save button — shown when settings changed */}
            {settingsChanged && (
              <button onClick={saveSettings} disabled={autoSaving}
                className="w-full py-2 rounded font-mono text-[10px] uppercase font-bold tracking-wider transition-all hover:opacity-90"
                style={{ background: "rgba(0,255,136,0.12)", border: "1px solid rgba(0,255,136,0.35)", color: "#00FF88" }}>
                {autoSaving ? "SAVING…" : "SAVE SETTINGS"}
              </button>
            )}
          </div>

          {/* Live status */}
          <div className="space-y-1 pt-2" style={{ borderTop: "1px solid rgba(0,212,255,0.08)" }}>
            <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1">Live Status</div>
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span style={{ color: autoOn ? "#00FF88" : "#4a6a8a" }}>●</span>
              <span className="text-[#8aadcc]">{autoOn ? "Checking signals every 30s" : "Engine stopped"}</span>
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span style={{ color: tradesDone < maxTrades ? "#00FF88" : "#FF2D55" }}>●</span>
              <span className="text-[#8aadcc]">Today: {tradesDone}/{maxTrades} trades used</span>
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span style={{ color: cfg?.is_market_open ? "#00FF88" : "#4a6a8a" }}>●</span>
              <span className="text-[#8aadcc]">Market: {cfg?.is_market_open ? "OPEN" : "CLOSED"}</span>
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span className="text-[#4a6a8a]">●</span>
              <span className="text-[#4a6a8a]">Min confidence: {localMinConf}%</span>
            </div>
          </div>

          {/* Auto trading modals */}
          {autoModalType === "live-switch" && (
            <ConfirmModal
              title="⚠ Switch to Live Trading"
              confirmLabel="SWITCH TO LIVE"
              confirmColor="#FF2D55"
              requirePhrase="I UNDERSTAND"
              onCancel={() => { setAutoModalType(null); setPendingAutoMode(null); }}
              onConfirm={confirmAccountModeSwitch}
              body={
                <div className="space-y-1">
                  <p>You are switching to <strong style={{ color: "#FF2D55" }}>LIVE account</strong>.</p>
                  <p>Auto trading will be disabled. Real money is at risk.</p>
                </div>
              }
            />
          )}
          {autoModalType === "auto-on-live" && (
            <ConfirmModal
              title="⚡ Enable Auto Trading (LIVE)"
              confirmLabel="ENABLE AUTO TRADING"
              confirmColor="#FFB800"
              requirePhrase="CONFIRM"
              loading={autoSaving}
              onCancel={() => setAutoModalType(null)}
              onConfirm={() => {
                setAutoModalType(null);
                if (cfg) setCfg({ ...cfg, auto_trading_enabled: true });
                updateTradingMode({ auto_trading_enabled: true });
              }}
              body={
                <div className="space-y-1">
                  <p>You are enabling auto trading on a <strong style={{ color: "#FF2D55" }}>LIVE account</strong>.</p>
                  <p>The engine will place real orders with real money based on AI signals.</p>
                  <p>Ensure the kill switch is working before proceeding.</p>
                </div>
              }
            />
          )}
        </div>

        {/* ─── MANUAL TRADE PANEL ─────────────────────────────────── */}
        <div className="lg:col-span-2 apex-card space-y-4">
          <div className="flex items-center gap-2">
            <TrendingUp style={{ width: 14, height: 14, color: "#00FF88" }} />
            <span className="font-heading text-sm font-bold uppercase tracking-widest text-[#e2f0ff]">
              Manual Order
            </span>
            {killActive && <span className="text-[8px] font-mono text-[#FF2D55] ml-auto">🛑 blocked</span>}
          </div>

          {/* Success result */}
          {lastResult && (
            <div className="p-3 rounded space-y-1" style={{
              background: "rgba(0,255,136,0.08)", border: "1px solid rgba(0,255,136,0.25)",
            }}>
              <div className="flex items-center gap-2 font-mono text-[10px]">
                <Check style={{ width: 12, height: 12, color: "#00FF88" }} />
                <span style={{ color: "#00FF88" }}>ORDER {lastResult.status.toUpperCase()}</span>
              </div>
              <div className="font-mono text-[10px] text-[#8aadcc]">ID: {lastResult.id.slice(0, 18)}…</div>
              {lastResult.price != null && (
                <div className="font-mono text-[10px] text-[#8aadcc]">Fill price: ${fmt(lastResult.price)}</div>
              )}
              <button onClick={() => setLastResult(null)} className="text-[9px] font-mono text-[#4a6a8a] hover:text-[#8aadcc] underline mt-1">
                New order →
              </button>
            </div>
          )}

          {submitError && (
            <div className="p-2 rounded text-[10px] font-mono flex items-center justify-between" style={{
              background: "rgba(255,45,85,0.08)", border: "1px solid rgba(255,45,85,0.25)", color: "#FF2D55",
            }}>
              <span>{submitError}</span>
              <button onClick={() => setSubmitError("")} style={{ color: "#4a6a8a" }}>✕</button>
            </div>
          )}

          {!lastResult && (
            <>
              {/* Symbol search */}
              <div className="space-y-1" ref={symbolDropRef}>
                <div className="font-mono text-[10px] text-[#4a6a8a]">Symbol</div>
                <div className="relative">
                  <div className="flex items-center gap-2 px-3 py-2 rounded" style={{
                    background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.2)",
                  }}>
                    <Search style={{ width: 12, height: 12, color: "#4a6a8a" }} />
                    <input
                      type="text"
                      value={symbolInput}
                      onChange={(e) => handleSymbolInput(e.target.value)}
                      onFocus={() => setShowSymbolDrop(true)}
                      placeholder="NVDA, AAPL, MSFT…"
                      className="flex-1 bg-transparent font-mono text-xs focus:outline-none uppercase"
                      style={{ color: "#e2f0ff" }}
                    />
                    {quoteLoading && <span className="text-[8px] font-mono text-[#4a6a8a] animate-pulse">…</span>}
                  </div>

                  {/* Dropdown */}
                  {showSymbolDrop && (
                    <div className="absolute top-full left-0 right-0 mt-1 rounded z-50 overflow-hidden" style={{
                      background: "#070c18", border: "1px solid rgba(0,212,255,0.2)",
                      boxShadow: "0 8px 24px rgba(0,0,0,0.6)",
                    }}>
                      <div className="p-2">
                        <div className="text-[8px] font-mono text-[#4a6a8a] uppercase tracking-widest px-1 mb-1">Popular</div>
                        <div className="flex flex-wrap gap-1">
                          {POPULAR_SYMBOLS.map((s) => (
                            <button key={s} onClick={() => selectSymbol(s)}
                              className="px-2 py-0.5 rounded font-mono text-[9px] transition-all hover:opacity-90"
                              style={{
                                background: symbol === s ? "rgba(0,212,255,0.15)" : "rgba(0,212,255,0.06)",
                                border:     `1px solid ${symbol === s ? "rgba(0,212,255,0.4)" : "rgba(0,212,255,0.1)"}`,
                                color:      symbol === s ? "#00D4FF" : "#8aadcc",
                              }}>
                              {s}
                            </button>
                          ))}
                        </div>
                      </div>
                      {quote && (
                        <button onClick={() => setShowSymbolDrop(false)}
                          className="w-full text-left flex items-center justify-between px-3 py-2 hover:opacity-90 transition-all"
                          style={{ background: "rgba(0,212,255,0.07)", borderTop: "1px solid rgba(0,212,255,0.1)" }}>
                          <span className="font-mono text-xs font-bold text-[#e2f0ff]">{quote.symbol}</span>
                          <div className="text-right">
                            <div className="font-mono text-xs font-bold" style={{ color: "#00D4FF" }}>${fmt(quote.price)}</div>
                            <div className="font-mono text-[9px]" style={{ color: quote.change_pct >= 0 ? "#00FF88" : "#FF2D55" }}>
                              {quote.change_pct >= 0 ? "+" : ""}{quote.change_pct.toFixed(2)}%
                            </div>
                          </div>
                        </button>
                      )}
                    </div>
                  )}
                </div>

                {/* Quote strip */}
                {quote && symbol && (
                  <div className="flex items-center gap-2 font-mono text-[10px]">
                    <span className="font-bold" style={{ color: "#00D4FF" }}>${fmt(quote.price)}</span>
                    <span style={{ color: quote.change_pct >= 0 ? "#00FF88" : "#FF2D55" }}>
                      {quote.change_pct >= 0 ? "+" : ""}{quote.change_pct.toFixed(2)}%
                    </span>
                    <span className="text-[#4a6a8a]">{quote.name}</span>
                  </div>
                )}
              </div>

              {/* BUY / SELL toggle */}
              <div className="space-y-1">
                <div className="font-mono text-[10px] text-[#4a6a8a]">Side</div>
                <div className="grid grid-cols-2 gap-1">
                  {(["buy", "sell"] as const).map((s) => (
                    <button key={s} onClick={() => setSide(s)}
                      className="py-2 rounded font-mono text-xs font-bold uppercase tracking-wider flex items-center justify-center gap-1.5 transition-all"
                      style={{
                        background: side === s
                          ? (s === "buy" ? "rgba(0,255,136,0.18)" : "rgba(255,45,85,0.18)")
                          : "rgba(0,0,0,0.2)",
                        border: `1px solid ${side === s
                          ? (s === "buy" ? "rgba(0,255,136,0.5)" : "rgba(255,45,85,0.5)")
                          : "rgba(74,106,138,0.2)"}`,
                        color: side === s ? (s === "buy" ? "#00FF88" : "#FF2D55") : "#4a6a8a",
                      }}>
                      {s === "buy" ? <TrendingUp style={{ width: 12, height: 12 }} /> : <TrendingDown style={{ width: 12, height: 12 }} />}
                      {s.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>

              {/* Qty */}
              <div className="space-y-1">
                <div className="font-mono text-[10px] text-[#4a6a8a]">Quantity (shares)</div>
                <input type="number" min={1} step={1} value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  placeholder="0"
                  className="w-full px-3 py-2 rounded font-mono text-sm bg-transparent focus:outline-none"
                  style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.2)", color: "#e2f0ff" }}
                />
                {qtyNum > 0 && priceNow > 0 && (
                  <div className="font-mono text-[10px] text-[#4a6a8a]">
                    Est. value: <span style={{ color: "#00D4FF", fontWeight: 700 }}>${fmt(estValue)}</span>
                  </div>
                )}
              </div>

              {/* MARKET / LIMIT toggle */}
              <div className="space-y-1">
                <div className="font-mono text-[10px] text-[#4a6a8a]">Order Type</div>
                <div className="flex gap-1">
                  {(["market", "limit"] as const).map((ot) => (
                    <button key={ot} onClick={() => setOrderType(ot)}
                      className="flex-1 py-1.5 rounded font-mono text-[10px] uppercase font-bold tracking-wider transition-all"
                      style={{
                        background: orderType === ot ? "rgba(0,212,255,0.14)" : "rgba(0,0,0,0.2)",
                        border:     `1px solid ${orderType === ot ? "rgba(0,212,255,0.45)" : "rgba(74,106,138,0.2)"}`,
                        color:      orderType === ot ? "#00D4FF" : "#4a6a8a",
                      }}>
                      {ot.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>

              {/* Limit price */}
              {orderType === "limit" && (
                <div className="space-y-1">
                  <div className="font-mono text-[10px] text-[#4a6a8a]">Limit Price ($)</div>
                  <input type="number" min={0.01} step={0.01} value={limitPrice}
                    onChange={(e) => setLimitPrice(e.target.value)}
                    placeholder={priceNow > 0 ? fmt(priceNow) : "0.00"}
                    className="w-full px-3 py-2 rounded font-mono text-sm bg-transparent focus:outline-none"
                    style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(255,184,0,0.3)", color: "#FFB800" }}
                  />
                </div>
              )}

              {/* Risk checks */}
              <div className="space-y-1 p-3 rounded" style={{
                background: "rgba(0,0,0,0.2)", border: "1px solid rgba(0,212,255,0.08)",
              }}>
                <div className="text-[8px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-2">Risk Checks</div>
                <RiskCheck label="Kill switch inactive" state={riskChecks.kill ? "ok" : "fail"} />
                <RiskCheck
                  label={`Within position limit ($${fmt(maxPos)})`}
                  state={qtyNum === 0 ? "unknown" : riskChecks.position ? "ok" : "fail"}
                />
                <RiskCheck label="Market hours" state={riskChecks.market ? "ok" : "warn"} />
                <RiskCheck
                  label={`Buying power ($${account ? fmt(buyPower) : "—"})`}
                  state={qtyNum === 0 ? "unknown" : riskChecks.buying ? "ok" : "fail"}
                />
              </div>

              {/* Preview button */}
              <button
                onClick={() => canPreview && !killActive && setShowPreview(true)}
                disabled={!canPreview || killActive}
                className="w-full py-2.5 rounded font-mono text-xs uppercase font-bold tracking-wider transition-all"
                style={{
                  background: canPreview && !killActive
                    ? (side === "buy" ? "rgba(0,255,136,0.15)" : "rgba(255,45,85,0.15)")
                    : "rgba(74,106,138,0.1)",
                  border: `1px solid ${canPreview && !killActive
                    ? (side === "buy" ? "rgba(0,255,136,0.45)" : "rgba(255,45,85,0.45)")
                    : "rgba(74,106,138,0.2)"}`,
                  color: canPreview && !killActive
                    ? (side === "buy" ? "#00FF88" : "#FF2D55")
                    : "#4a6a8a",
                  cursor: canPreview && !killActive ? "pointer" : "not-allowed",
                }}
              >
                {killActive            ? "🛑 Kill Switch Active"         :
                  !symbol              ? "ENTER SYMBOL FIRST"            :
                  qtyNum === 0         ? "ENTER QUANTITY FIRST"          :
                  !riskChecks.position ? "EXCEEDS POSITION LIMIT"        :
                  !riskChecks.buying   ? "INSUFFICIENT BUYING POWER"     :
                  `PREVIEW ${side.toUpperCase()} ORDER`}
              </button>
            </>
          )}
        </div>
      </div>

      {/* ══════════════════════════════════════════ ORDER HISTORY */}
      <div className="apex-card p-0 overflow-hidden">
        <div className="flex items-center justify-between flex-wrap gap-3 px-4 py-3"
          style={{ borderBottom: "1px solid rgba(0,212,255,0.1)" }}>
          <span className="font-heading text-sm font-bold uppercase tracking-widest text-[#e2f0ff]">
            Order History
          </span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {(["all","auto","manual","filled","cancelled"] as FilterType[]).map((f) => (
              <FilterTab key={f} label={f} active={filter === f} onClick={() => setFilter(f)} />
            ))}
          </div>
        </div>

        {pageLoading ? (
          <div className="p-6 space-y-2">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-10 rounded animate-pulse" style={{ background: "rgba(0,212,255,0.04)" }} />
            ))}
          </div>
        ) : filteredOrders.length === 0 ? (
          <div className="py-12 text-center font-mono text-[10px] uppercase tracking-widest text-[#4a6a8a]">
            No orders found
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="apex-table w-full">
              <thead>
                <tr>
                  <th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th>
                  <th>Price</th><th>Total</th><th>Status</th><th>Source</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredOrders.map((o) => (
                  <OrderRow key={o.id} order={o} onCancel={cancelOrder} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ══════════════════════════════════ ORDER PREVIEW MODAL */}
      {showPreview && (
        <ConfirmModal
          title={`Confirm ${side.toUpperCase()} Order${isLive ? " — LIVE MONEY" : ""}`}
          confirmLabel={`SUBMIT ${side.toUpperCase()} ORDER`}
          confirmColor={isLive ? "#FFB800" : side === "buy" ? "#00FF88" : "#FF2D55"}
          requirePhrase={isLive ? "CONFIRM" : undefined}
          loading={submitLoading}
          onCancel={() => setShowPreview(false)}
          onConfirm={submitOrder}
          body={
            <div className="space-y-1">
              <p><strong style={{ color: "#e2f0ff" }}>Symbol:</strong> {symbol}</p>
              <p><strong style={{ color: "#e2f0ff" }}>Side:</strong>{" "}
                <span style={{ color: side === "buy" ? "#00FF88" : "#FF2D55" }}>{side.toUpperCase()}</span>
              </p>
              <p><strong style={{ color: "#e2f0ff" }}>Qty:</strong> {qtyNum} shares</p>
              <p><strong style={{ color: "#e2f0ff" }}>Type:</strong> {orderType.toUpperCase()}
                {orderType === "limit" ? ` @ $${limitPrice}` : ""}
              </p>
              <p><strong style={{ color: "#e2f0ff" }}>Est. value:</strong>{" "}
                <span style={{ color: "#00D4FF" }}>${fmt(estValue)}</span>
              </p>
              <p><strong style={{ color: "#e2f0ff" }}>Account:</strong>{" "}
                <span style={{ color: isLive ? "#FF2D55" : "#00D4FF" }}>{accountMode.toUpperCase()}</span>
              </p>
              {isLive && <p style={{ color: "#FFB800" }}>⚠ This will use real money.</p>}
            </div>
          }
        />
      )}
    </div>
  );
}
