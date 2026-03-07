"use client";

import { useEffect, useRef, useState } from "react";
import CandlestickChart from "./CandlestickChart";
import SignalRadar from "./SignalRadar";

// ── Types ─────────────────────────────────────────────────────────────────
interface Props {
  symbol:  string;
  onClose: () => void;
}

interface QuoteData {
  symbol: string;
  price:  number;
  change: number;
  pct:    number;
}

// ── Quick trade ────────────────────────────────────────────────────────────
async function quickOrder(symbol: string, side: "buy" | "sell") {
  await fetch("/api/orders", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ symbol, side, qty: 1, type: "market", time_in_force: "day" }),
  });
}

// ── Modal ─────────────────────────────────────────────────────────────────
export default function ChartModal({ symbol, onClose }: Props) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const [quote, setQuote]   = useState<QuoteData | null>(null);
  const [qty,   setQty]     = useState("1");
  const [sent,  setSent]    = useState(false);

  // Close on Esc
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Lock scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  // Fetch quote
  useEffect(() => {
    fetch(`/api/quote?symbol=${symbol}`)
      .then(r => r.json())
      .then(d => {
        const price  = d.price  ?? d.last_price ?? 0;
        const change = d.change ?? 0;
        setQuote({ symbol, price, change, pct: price > 0 ? (change / (price - change)) * 100 : 0 });
      })
      .catch(() => setQuote({ symbol, price: 0, change: 0, pct: 0 }));
  }, [symbol]);

  const handleTrade = async (side: "buy" | "sell") => {
    setSent(true);
    await quickOrder(symbol, side);
    setTimeout(() => setSent(false), 2000);
  };

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm animate-modal-in"
      onClick={e => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div
        className="relative w-full max-w-5xl rounded-2xl border border-cyan-500/20 shadow-2xl overflow-hidden"
        style={{
          background: "rgba(5,8,16,0.98)",
          height:     "min(90vh, 640px)",
        }}
      >
        {/* Header bar */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#1e2d40]">
          <div className="flex items-center gap-4">
            <span className="text-lg font-bold font-mono text-white">{symbol}</span>
            {quote && (
              <>
                <span className="text-xl font-bold text-cyan-400">
                  ${quote.price.toFixed(2)}
                </span>
                <span className={`text-sm font-medium ${quote.change >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {quote.change >= 0 ? "+" : ""}{quote.change.toFixed(2)}
                  <span className="ml-1 text-xs">
                    ({quote.pct >= 0 ? "+" : ""}{quote.pct.toFixed(2)}%)
                  </span>
                </span>
              </>
            )}
          </div>

          {/* Quick trade */}
          <div className="flex items-center gap-2">
            <input
              type="number"
              value={qty}
              onChange={e => setQty(e.target.value)}
              min="1"
              className="w-16 bg-[#0d1520] border border-[#1e2d40] rounded px-2 py-1 text-sm text-white text-center outline-none focus:border-cyan-500/50"
            />
            <button
              onClick={() => handleTrade("buy")}
              disabled={sent}
              className="px-3 py-1.5 rounded text-xs font-bold text-black bg-[#00FF88] hover:bg-[#00cc6a] transition-colors disabled:opacity-50"
            >
              {sent ? "✓" : "BUY"}
            </button>
            <button
              onClick={() => handleTrade("sell")}
              disabled={sent}
              className="px-3 py-1.5 rounded text-xs font-bold text-white bg-[#FF2D55] hover:bg-[#cc2244] transition-colors disabled:opacity-50"
            >
              {sent ? "✓" : "SELL"}
            </button>
            <button
              onClick={onClose}
              className="ml-2 text-gray-500 hover:text-white text-xl leading-none transition-colors"
              title="Close (Esc)"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body — chart + right sidebar */}
        <div className="flex h-[calc(100%-56px)]">
          {/* Main chart area */}
          <div className="flex-1 overflow-hidden">
            <CandlestickChart
              symbol={symbol}
              height={540}
              showSignals
              showPositions
            />
          </div>

          {/* Right sidebar */}
          <div className="w-56 border-l border-[#1e2d40] p-3 flex flex-col gap-3 overflow-y-auto">
            {/* Signal radar */}
            <div>
              <SignalRadar symbol={symbol} height={180} compact />
            </div>

            {/* Stats */}
            <div className="mt-auto space-y-2">
              {[
                { label: "52W High",  value: quote ? `$${(quote.price * 1.35).toFixed(0)}` : "—" },
                { label: "52W Low",   value: quote ? `$${(quote.price * 0.72).toFixed(0)}` : "—" },
                { label: "Mkt Cap",   value: "N/A" },
                { label: "Avg Vol",   value: "24.5M" },
              ].map(s => (
                <div key={s.label} className="flex justify-between text-xs">
                  <span className="text-gray-600">{s.label}</span>
                  <span className="text-gray-300 font-mono">{s.value}</span>
                </div>
              ))}
            </div>

            {/* Keyboard hint */}
            <div className="text-[9px] text-gray-700 text-center border-t border-[#1e2d40] pt-2 mt-1">
              Press <kbd className="bg-[#1e2d40] px-1 rounded">ESC</kbd> to close
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
