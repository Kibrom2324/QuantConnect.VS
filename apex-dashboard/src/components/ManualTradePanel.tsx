"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { TrendingDown, TrendingUp, Search, Check, AlertTriangle, X } from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────
interface Quote {
  symbol:     string;
  price:      number;
  change:     number;
  change_pct: number;
  name:       string;
  is_mock?:   boolean;
}

interface AccountInfo {
  buying_power:   number;
  account_mode:   "paper" | "live";
  trading_blocked: boolean;
  is_mock?:       boolean;
}

interface KillState {
  active: boolean;
}

interface TradingConfig {
  max_position_size_usd: number;
  is_market_open:        boolean;
  account_mode:          "paper" | "live";
}

interface OrderResult {
  id:               string;
  status:           string;
  symbol:           string;
  side:             string;
  qty:              number;
  filled_avg_price: number | null;
  filled_at:        string | null;
  is_mock:          boolean;
  error?:           string;
}

// ── Helpers ───────────────────────────────────────────────────────────────
const fmt = (n: number) =>
  n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Popular symbols for quick picker
const POPULAR = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD"];

// ── Symbol search dropdown ─────────────────────────────────────────────────
function SymbolSearch({
  value,
  quote,
  onSelect,
  disabled,
}: {
  value:    string;
  quote:    Quote | null;
  onSelect: (sym: string) => void;
  disabled: boolean;
}) {
  const [input,  setInput]  = useState(value);
  const [open,   setOpen]   = useState(false);
  const [results, setResults] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const debRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => { setInput(value); }, [value]);

  const lookup = useCallback((sym: string) => {
    if (!sym.trim()) { setResults([]); return; }
    setLoading(true);
    fetch(`/api/quote?symbol=${encodeURIComponent(sym.trim().toUpperCase())}`)
      .then((r) => r.json())
      .then((d: Quote) => {
        if (d.price != null) setResults([d]);
        else setResults([]);
      })
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, []);

  const handleChange = (v: string) => {
    setInput(v);
    setOpen(true);
    if (debRef.current) clearTimeout(debRef.current);
    debRef.current = setTimeout(() => lookup(v), 350);
  };

  const pick = (sym: string) => {
    onSelect(sym);
    setInput(sym);
    setOpen(false);
  };

  const priceColor = quote
    ? quote.change >= 0 ? "#00FF88" : "#FF2D55"
    : "#4a6a8a";

  return (
    <div ref={ref} className="relative">
      <div className="relative flex items-center">
        <Search
          style={{ width: 12, height: 12, color: "#4a6a8a", position: "absolute", left: 10, pointerEvents: "none" }}
        />
        <input
          type="text"
          value={input}
          onChange={(e) => handleChange(e.target.value.toUpperCase())}
          onFocus={() => setOpen(true)}
          disabled={disabled}
          placeholder="TICKER"
          maxLength={6}
          className="w-full pl-7 pr-3 py-2 rounded font-mono text-sm font-bold uppercase outline-none transition-all"
          style={{
            background: "rgba(0,212,255,0.06)",
            border:     `1px solid ${quote ? "rgba(0,212,255,0.35)" : "rgba(0,212,255,0.2)"}`,
            color:      "#e2f0ff",
            cursor:     disabled ? "not-allowed" : "text",
          }}
        />
      </div>
      {/* Quote display under input */}
      {quote && (
        <div className="mt-1 px-1 flex items-center gap-3 font-mono text-[10px]">
          <span className="text-[#8aadcc] truncate">{quote.name}</span>
          <span className="font-bold" style={{ color: "#e2f0ff" }}>${fmt(quote.price)}</span>
          <span style={{ color: priceColor }}>
            {quote.change >= 0 ? "+" : ""}{fmt(quote.change)} ({quote.change >= 0 ? "+" : ""}{quote.change_pct.toFixed(2)}%)
          </span>
        </div>
      )}
      {/* Dropdown */}
      {open && (
        <div
          className="absolute left-0 top-full mt-1 w-full rounded overflow-hidden z-50"
          style={{
            background: "#0a1020",
            border:     "1px solid rgba(0,212,255,0.25)",
            boxShadow:  "0 8px 24px rgba(0,0,0,0.6)",
          }}
        >
          {/* Popular shortcuts */}
          {!input.trim() && (
            <>
              <div className="px-3 py-1.5 text-[8px] font-mono uppercase tracking-widest text-[#4a6a8a]">
                Popular
              </div>
              <div className="flex flex-wrap gap-1 px-3 pb-2">
                {POPULAR.map((s) => (
                  <button
                    key={s}
                    onClick={() => pick(s)}
                    className="px-2 py-1 rounded text-[10px] font-mono font-bold transition-all"
                    style={{
                      background: "rgba(0,212,255,0.08)",
                      border:     "1px solid rgba(0,212,255,0.2)",
                      color:      "#00D4FF",
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </>
          )}
          {/* Search results */}
          {loading && (
            <div className="px-3 py-2 text-[10px] font-mono text-[#4a6a8a] animate-pulse">
              Fetching…
            </div>
          )}
          {!loading && results.map((q) => (
            <button
              key={q.symbol}
              onClick={() => pick(q.symbol)}
              className="w-full text-left px-3 py-2.5 flex items-center gap-3 transition-all hover:bg-[rgba(0,212,255,0.05)]"
            >
              <span className="font-mono font-bold text-[12px] text-[#00D4FF] w-14 flex-shrink-0">{q.symbol}</span>
              <span className="font-mono text-[10px] text-[#8aadcc] flex-1 truncate">{q.name}</span>
              <span className="font-mono text-[11px] font-bold text-[#e2f0ff]">${fmt(q.price)}</span>
              <span
                className="font-mono text-[9px] flex-shrink-0"
                style={{ color: q.change >= 0 ? "#00FF88" : "#FF2D55" }}
              >
                {q.change >= 0 ? "+" : ""}{q.change_pct.toFixed(2)}%
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Preview / confirmation modal ───────────────────────────────────────────
function PreviewModal({
  symbol, side, qty, orderType, limitPrice, price, accountMode, maxPositionUsd,
  onCancel, onConfirm, loading,
}: {
  symbol:       string;
  side:         "buy" | "sell";
  qty:          number;
  orderType:    "market" | "limit";
  limitPrice:   number;
  price:        number;
  accountMode:  "paper" | "live";
  maxPositionUsd: number;
  onCancel:     () => void;
  onConfirm:    () => void;
  loading:      boolean;
}) {
  const [phrase, setPhrase] = useState("");
  const estPrice = orderType === "limit" ? limitPrice : price;
  const estTotal = estPrice * qty;
  const isLive   = accountMode === "live";
  const liveMet  = !isLive || phrase === "CONFIRM";
  const sideColor = side === "buy" ? "#00FF88" : "#FF2D55";

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(4px)" }}
    >
      <div
        className="rounded-lg max-w-sm w-full mx-4"
        style={{
          background: "#070c18",
          border:     `1px solid ${isLive ? "rgba(255,45,85,0.4)" : "rgba(0,212,255,0.3)"}`,
          boxShadow:  "0 0 40px rgba(0,0,0,0.8)",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-3"
          style={{ borderBottom: `1px solid ${isLive ? "rgba(255,45,85,0.2)" : "rgba(0,212,255,0.12)"}` }}
        >
          <span className="font-heading text-sm font-bold uppercase tracking-widest text-[#e2f0ff]">
            ⚡ Order Preview
          </span>
          <button onClick={onCancel} style={{ color: "#4a6a8a" }}>
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3">
          {/* Direction */}
          <div className="text-center">
            <span
              className="font-heading text-2xl font-bold uppercase tracking-wider"
              style={{ color: sideColor, textShadow: `0 0 16px ${sideColor}60` }}
            >
              {side.toUpperCase()} {qty} {symbol}
            </span>
          </div>

          {/* Details grid */}
          <div className="space-y-1.5 font-mono text-[11px]">
            {[
              { label: "Order Type",  value: orderType.toUpperCase() },
              { label: "Est. Price",  value: estPrice > 0 ? `~$${fmt(estPrice)}` : "Market" },
              { label: "Est. Total",  value: estTotal > 0 ? `~$${fmt(estTotal)}` : "—" },
            ].map(({ label, value }) => (
              <div key={label} className="flex items-center justify-between">
                <span className="text-[#4a6a8a]">{label}</span>
                <span className="text-[#e2f0ff] font-bold">{value}</span>
              </div>
            ))}
            {estTotal > maxPositionUsd && (
              <div
                className="flex items-center gap-1.5 text-[10px] px-2 py-1.5 rounded"
                style={{
                  background: "rgba(255,184,0,0.08)",
                  border:     "1px solid rgba(255,184,0,0.3)",
                  color:      "#FFB800",
                }}
              >
                <AlertTriangle style={{ width: 11, height: 11 }} />
                Exceeds max position size (${fmt(maxPositionUsd)})
              </div>
            )}
          </div>

          {/* Account badge */}
          <div className="flex items-center justify-center">
            <span
              className="px-4 py-1.5 rounded font-mono text-[11px] font-bold uppercase tracking-widest"
              style={{
                background: isLive ? "rgba(255,45,85,0.12)" : "rgba(0,212,255,0.1)",
                border:     `1px solid ${isLive ? "rgba(255,45,85,0.4)" : "rgba(0,212,255,0.3)"}`,
                color:      isLive ? "#FF2D55" : "#00D4FF",
              }}
            >
              {isLive ? "⚠ LIVE TRADING — REAL MONEY" : "✓ PAPER TRADING — SIMULATED"}
            </span>
          </div>

          {/* Live confirmation phrase */}
          {isLive && (
            <div className="space-y-1">
              <p className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">
                Type <span style={{ color: "#FFB800" }}>"CONFIRM"</span> to proceed with live order:
              </p>
              <input
                autoFocus
                value={phrase}
                onChange={(e) => setPhrase(e.target.value.toUpperCase())}
                placeholder="CONFIRM"
                className="w-full px-3 py-2 rounded font-mono text-[11px] outline-none"
                style={{
                  background: "rgba(0,0,0,0.4)",
                  border:     `1px solid ${phrase === "CONFIRM" ? "rgba(0,255,136,0.5)" : "rgba(74,106,138,0.4)"}`,
                  color:      phrase === "CONFIRM" ? "#00FF88" : "#e2f0ff",
                }}
              />
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="flex gap-3 px-5 py-3"
          style={{ borderTop: "1px solid rgba(0,212,255,0.08)" }}
        >
          <button
            onClick={onCancel}
            className="flex-1 py-2.5 rounded font-mono text-[11px] uppercase tracking-wider transition-all"
            style={{
              background: "rgba(74,106,138,0.12)",
              border:     "1px solid rgba(74,106,138,0.3)",
              color:      "#4a6a8a",
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!liveMet || loading}
            className="flex-1 py-2.5 rounded font-mono text-[11px] uppercase font-bold tracking-wider transition-all"
            style={{
              background: liveMet && !loading
                ? isLive ? "rgba(255,45,85,0.2)"  : "rgba(0,212,255,0.15)"
                : "rgba(74,106,138,0.08)",
              border: `1px solid ${liveMet && !loading
                ? isLive ? "rgba(255,45,85,0.5)"  : "rgba(0,212,255,0.4)"
                : "rgba(74,106,138,0.2)"}`,
              color:  liveMet && !loading
                ? isLive ? "#FF2D55" : "#00D4FF"
                : "#4a6a8a",
              cursor: liveMet && !loading ? "pointer" : "not-allowed",
              boxShadow: liveMet && !loading && !isLive ? "0 0 12px rgba(0,212,255,0.2)" : "none",
            }}
          >
            {loading ? "◈ Submitting…" : "Confirm & Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Risk check item ────────────────────────────────────────────────────────
function RiskCheck({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <div className="flex items-center gap-2 text-[10px] font-mono">
      <span style={{ color: ok === null ? "#4a6a8a" : ok ? "#00FF88" : "#FF2D55", flexShrink: 0 }}>
        {ok === null ? "○" : ok ? "✓" : "✗"}
      </span>
      <span style={{ color: ok === null ? "#4a6a8a" : ok ? "#8aadcc" : "#FF6B80" }}>{label}</span>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────
interface Props {
  accountMode?: "paper" | "live";
  maxPositionUsd?: number;
  refreshKey?: number;
}

export default function ManualTradePanel({
  accountMode: propAccountMode,
  maxPositionUsd: propMax,
  refreshKey = 0,
}: Props) {
  // Form state
  const [symbol,     setSymbol]     = useState("");
  const [quote,      setQuote]      = useState<Quote | null>(null);
  const [side,       setSide]       = useState<"buy" | "sell">("buy");
  const [qty,        setQty]        = useState<string>("");
  const [orderType,  setOrderType]  = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState<string>("");
  const [showPreview, setShowPreview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result,     setResult]     = useState<OrderResult | null>(null);
  const [error,      setError]      = useState<string | null>(null);

  // Context
  const [kill,    setKill]    = useState<KillState>({ active: false });
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [tradingCfg, setTradingCfg] = useState<TradingConfig | null>(null);

  const accountMode = propAccountMode ?? tradingCfg?.account_mode ?? "paper";
  const maxPos      = propMax ?? tradingCfg?.max_position_size_usd ?? 5000;
  const mktOpen     = tradingCfg?.is_market_open ?? false;

  // Load context
  useEffect(() => {
    fetch("/api/kill-switch")
      .then((r) => r.json())
      .then((d: KillState) => setKill(d))
      .catch(() => {});
    fetch(`/api/account?account_mode=${accountMode}`)
      .then((r) => r.json())
      .then((d: AccountInfo) => setAccount(d))
      .catch(() => {});
    fetch("/api/trading-mode")
      .then((r) => r.json())
      .then((d: TradingConfig) => setTradingCfg(d))
      .catch(() => {});
  }, [refreshKey, accountMode]);

  // Fetch quote when symbol changes
  useEffect(() => {
    if (!symbol || symbol.length < 1) { setQuote(null); return; }
    const t = setTimeout(() => {
      fetch(`/api/quote?symbol=${encodeURIComponent(symbol)}`)
        .then((r) => r.json())
        .then((d: Quote) => { if (d.price != null) setQuote(d); })
        .catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [symbol]);

  const qtyNum       = parseFloat(qty)       || 0;
  const limitNum     = parseFloat(limitPrice) || 0;
  const estPrice     = orderType === "limit" ? limitNum : (quote?.price ?? 0);
  const estTotal     = estPrice * qtyNum;

  // Risk checks
  const checks = {
    position:  estTotal > 0 && estTotal <= maxPos,
    market:    mktOpen,
    kill:      !kill.active,
    power:     account != null && estTotal <= (account.buying_power ?? 0),
  };
  const allClear = checks.kill && checks.position && (!estTotal || checks.power) && symbol.length > 0 && qtyNum > 0;

  const handleSymbolSelect = (sym: string) => {
    setSymbol(sym);
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/trade", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol:      symbol.toUpperCase(),
          side,
          qty:         qtyNum,
          order_type:  orderType,
          limit_price: orderType === "limit" ? limitNum : undefined,
          account_mode: accountMode,
          confirmed:   true,
        }),
      });
      const data: OrderResult = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Order failed");
        setShowPreview(false);
      } else {
        setResult(data);
        setShowPreview(false);
      }
    } catch (e) {
      setError(String(e));
      setShowPreview(false);
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setSymbol(""); setQuote(null); setSide("buy"); setQty("");
    setOrderType("market"); setLimitPrice(""); setResult(null); setError(null);
  };

  const isLive    = accountMode === "live";
  const sideColor = side === "buy" ? "#00FF88" : "#FF2D55";
  const isBlocked = kill.active || account?.trading_blocked;

  return (
    <>
      {/* Preview modal */}
      {showPreview && quote && (
        <PreviewModal
          symbol={symbol}
          side={side}
          qty={qtyNum}
          orderType={orderType}
          limitPrice={limitNum}
          price={quote.price}
          accountMode={accountMode}
          maxPositionUsd={maxPos}
          onCancel={() => setShowPreview(false)}
          onConfirm={handleSubmit}
          loading={submitting}
        />
      )}

      <div
        className="apex-card flex flex-col gap-3"
        style={{
          border:    `1px solid ${isLive ? "rgba(255,45,85,0.2)" : "rgba(0,212,255,0.12)"}`,
          boxShadow: isLive ? "0 0 16px rgba(255,45,85,0.06)" : "none",
        }}
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-0.5">
              Order Entry
            </div>
            <span className="font-heading text-sm font-bold text-[#e2f0ff] uppercase tracking-widest">
              Manual Order
            </span>
          </div>
          <span
            className="text-[10px] font-mono font-bold uppercase px-2 py-1 rounded"
            style={{
              color:      isLive ? "#FF2D55" : "#00D4FF",
              background: isLive ? "rgba(255,45,85,0.12)" : "rgba(0,212,255,0.08)",
              border:     `1px solid ${isLive ? "rgba(255,45,85,0.35)" : "rgba(0,212,255,0.25)"}`,
            }}
          >
            {isLive ? "⚠ LIVE" : "PAPER"}
          </span>
        </div>

        {/* ── Kill switch warning ── */}
        {kill.active && (
          <div
            className="flex items-center gap-2 px-3 py-2 rounded text-[10px] font-mono"
            style={{
              background: "rgba(255,45,85,0.1)",
              border:     "1px solid rgba(255,45,85,0.35)",
              color:      "#FF6B80",
            }}
          >
            <AlertTriangle style={{ width: 12, height: 12 }} />
            Kill switch active — trading disabled
          </div>
        )}

        {/* ── Success result ── */}
        {result && (
          <div
            className="rounded-lg px-4 py-4 space-y-2"
            style={{
              background: "rgba(0,255,136,0.05)",
              border:     "1px solid rgba(0,255,136,0.3)",
            }}
          >
            <div className="font-mono text-[12px] font-bold" style={{ color: "#00FF88" }}>
              ✓ Order submitted
            </div>
            <div className="space-y-1 font-mono text-[10px]">
              <div>
                <span className="text-[#4a6a8a]">Order ID: </span>
                <span className="text-[#8aadcc]">{result.id}</span>
              </div>
              <div>
                <span className="text-[#4a6a8a]">Status: </span>
                <span style={{ color: "#00FF88", fontWeight: 700 }}>
                  {result.status.toUpperCase()}
                  {result.filled_avg_price != null ? ` @ $${fmt(result.filled_avg_price)}` : ""}
                </span>
              </div>
              {result.is_mock && (
                <div className="text-[#4a6a8a]">(simulated order — mock mode)</div>
              )}
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={resetForm}
                className="flex-1 py-1.5 rounded font-mono text-[10px] uppercase tracking-wider transition-all"
                style={{
                  background: "rgba(0,212,255,0.08)",
                  border:     "1px solid rgba(0,212,255,0.2)",
                  color:      "#00D4FF",
                }}
              >
                Trade Again
              </button>
            </div>
          </div>
        )}

        {/* ── Error result ── */}
        {error && !result && (
          <div
            className="rounded-lg px-4 py-3 space-y-1.5"
            style={{
              background: "rgba(255,45,85,0.06)",
              border:     "1px solid rgba(255,45,85,0.3)",
            }}
          >
            <div className="font-mono text-[11px] font-bold" style={{ color: "#FF2D55" }}>
              ✗ Order failed
            </div>
            <div className="font-mono text-[10px]" style={{ color: "#FF6B80" }}>{error}</div>
            <button
              onClick={() => setError(null)}
              className="mt-1 text-[9px] font-mono text-[#4a6a8a] underline"
            >
              Try Again
            </button>
          </div>
        )}

        {/* ── Order form (hidden on success) ── */}
        {!result && (
          <div className="space-y-3">
            {/* Symbol */}
            <div>
              <label className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1 block">
                Symbol
              </label>
              <SymbolSearch
                value={symbol}
                quote={quote}
                onSelect={handleSymbolSelect}
                disabled={!!isBlocked}
              />
            </div>

            {/* Side */}
            <div>
              <label className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1 block">
                Side
              </label>
              <div className="flex gap-2">
                <button
                  onClick={() => setSide("buy")}
                  disabled={!!isBlocked}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded font-mono text-[11px] uppercase font-bold tracking-wider transition-all"
                  style={{
                    background: side === "buy" ? "rgba(0,255,136,0.18)" : "rgba(0,0,0,0.2)",
                    border:     `2px solid ${side === "buy" ? "rgba(0,255,136,0.6)" : "rgba(0,255,136,0.15)"}`,
                    color:      side === "buy" ? "#00FF88" : "#4a6a8a",
                    boxShadow:  side === "buy" ? "0 0 12px rgba(0,255,136,0.15)" : "none",
                    cursor:     isBlocked ? "not-allowed" : "pointer",
                  }}
                >
                  <TrendingUp style={{ width: 12, height: 12 }} />
                  BUY
                </button>
                <button
                  onClick={() => setSide("sell")}
                  disabled={!!isBlocked}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded font-mono text-[11px] uppercase font-bold tracking-wider transition-all"
                  style={{
                    background: side === "sell" ? "rgba(255,45,85,0.18)" : "rgba(0,0,0,0.2)",
                    border:     `2px solid ${side === "sell" ? "rgba(255,45,85,0.6)" : "rgba(255,45,85,0.15)"}`,
                    color:      side === "sell" ? "#FF2D55" : "#4a6a8a",
                    boxShadow:  side === "sell" ? "0 0 12px rgba(255,45,85,0.15)" : "none",
                    cursor:     isBlocked ? "not-allowed" : "pointer",
                  }}
                >
                  <TrendingDown style={{ width: 12, height: 12 }} />
                  SELL
                </button>
              </div>
            </div>

            {/* Quantity */}
            <div>
              <label className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1 block">
                Qty (shares)
              </label>
              <input
                type="number"
                min={1}
                step={1}
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                disabled={!!isBlocked}
                placeholder="0"
                className="w-full px-3 py-2 rounded font-mono text-sm font-bold outline-none"
                style={{
                  background: "rgba(0,212,255,0.06)",
                  border:     `1px solid ${qtyNum > 0 ? "rgba(0,212,255,0.35)" : "rgba(0,212,255,0.15)"}`,
                  color:      "#e2f0ff",
                  cursor:     isBlocked ? "not-allowed" : "text",
                }}
              />
              {estTotal > 0 && (
                <p className="mt-0.5 text-[9px] font-mono text-[#4a6a8a]">
                  Est. value:{" "}
                  <span
                    style={{
                      color: estTotal > maxPos ? "#FFB800" : sideColor,
                      fontWeight: 700,
                    }}
                  >
                    ${fmt(estTotal)}
                  </span>
                </p>
              )}
            </div>

            {/* Order type */}
            <div>
              <label className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1 block">
                Order Type
              </label>
              <div className="flex gap-2">
                {(["market", "limit"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setOrderType(t)}
                    disabled={!!isBlocked}
                    className="flex-1 py-1.5 rounded font-mono text-[10px] uppercase tracking-wider transition-all"
                    style={{
                      background: orderType === t ? "rgba(0,212,255,0.12)" : "rgba(0,0,0,0.2)",
                      border:     `1px solid ${orderType === t ? "rgba(0,212,255,0.45)" : "rgba(0,212,255,0.12)"}`,
                      color:      orderType === t ? "#00D4FF" : "#4a6a8a",
                      cursor:     isBlocked ? "not-allowed" : "pointer",
                    }}
                  >
                    {t.toUpperCase()}
                  </button>
                ))}
              </div>
              {orderType === "limit" && (
                <input
                  type="number"
                  min={0.01}
                  step={0.01}
                  value={limitPrice}
                  onChange={(e) => setLimitPrice(e.target.value)}
                  disabled={!!isBlocked}
                  placeholder="Limit price $"
                  className="mt-2 w-full px-3 py-2 rounded font-mono text-sm outline-none"
                  style={{
                    background: "rgba(0,212,255,0.06)",
                    border:     "1px solid rgba(0,212,255,0.2)",
                    color:      "#e2f0ff",
                  }}
                />
              )}
            </div>

            {/* Risk checks */}
            <div
              className="rounded px-3 py-2.5 space-y-1.5"
              style={{
                background: "rgba(0,0,0,0.2)",
                border:     "1px solid rgba(0,212,255,0.06)",
              }}
            >
              <div className="text-[8px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1">
                Risk Checks
              </div>
              <RiskCheck
                ok={estTotal > 0 ? checks.position : null}
                label={`Within position limit ($${fmt(maxPos)})`}
              />
              <RiskCheck
                ok={mktOpen}
                label={mktOpen ? "Market is open" : "Market is closed (manual override OK)"}
              />
              <RiskCheck
                ok={checks.kill}
                label="Kill switch inactive"
              />
              <RiskCheck
                ok={account != null && estTotal > 0 ? checks.power : null}
                label={`Buying power: ${account ? "$" + fmt(account.buying_power) : "loading…"}`}
              />
            </div>

            {/* Preview button */}
            <button
              onClick={() => setShowPreview(true)}
              disabled={!allClear || !!isBlocked}
              className="w-full py-3 rounded font-mono text-[12px] uppercase font-bold tracking-widest transition-all"
              style={{
                background: allClear && !isBlocked
                  ? `linear-gradient(135deg, ${sideColor}28, ${sideColor}14)`
                  : "rgba(74,106,138,0.08)",
                border: `1px solid ${allClear && !isBlocked ? `${sideColor}60` : "rgba(74,106,138,0.2)"}`,
                color:  allClear && !isBlocked ? sideColor : "#4a6a8a",
                cursor: allClear && !isBlocked ? "pointer" : "not-allowed",
                boxShadow: allClear && !isBlocked ? `0 0 16px ${sideColor}18` : "none",
              }}
              title={kill.active ? "Kill switch active — resume trading first" : undefined}
            >
              {kill.active ? "Kill Switch Active" : allClear ? (
                <span className="flex items-center justify-center gap-2">
                  <Check style={{ width: 12, height: 12 }} />
                  Preview Order
                </span>
              ) : "Enter order details"}
            </button>

            {/* Market closed warning */}
            {!mktOpen && !kill.active && (
              <p className="text-[9px] font-mono text-center" style={{ color: "#FFB800" }}>
                ⚠ Market is closed. Manual orders can still be submitted.
              </p>
            )}
          </div>
        )}
      </div>
    </>
  );
}
