"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAlpacaStream, LivePrice } from "@/hooks/useAlpacaStream";

const WATCHED_SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "SPY", "QQQ", "GOOGL", "META", "AMD"];

interface LivePriceBarProps {
  onSymbolClick?: (symbol: string) => void;
}

interface TickerItem extends LivePrice {
  flash: "green" | "red" | null;
}

export default function LivePriceBar({ onSymbolClick }: LivePriceBarProps) {
  const [items, setItems]   = useState<Record<string, TickerItem>>({});
  const prevPrices          = useRef<Record<string, number>>({});
  const flashTimers         = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const { prices } = useAlpacaStream({
    symbols:  WATCHED_SYMBOLS,
    enabled:  true,
  });

  // Sync stream prices → ticker items
  useEffect(() => {
    setItems(prev => {
      const next = { ...prev };
      for (const sym of WATCHED_SYMBOLS) {
        const p = prices[sym];
        if (!p) continue;

        const prevPrice = prevPrices.current[sym];
        let flash: "green" | "red" | null = null;
        if (prevPrice != null && p.price !== prevPrice) {
          flash = p.price > prevPrice ? "green" : "red";
          // Clear flash after 800ms
          if (flashTimers.current[sym]) clearTimeout(flashTimers.current[sym]);
          flashTimers.current[sym] = setTimeout(() => {
            setItems(old => ({
              ...old,
              [sym]: { ...old[sym], flash: null },
            }));
          }, 800);
        }
        prevPrices.current[sym] = p.price;

        next[sym] = {
          ...p,
          flash: flash ?? next[sym]?.flash ?? null,
        };
      }
      return next;
    });
  }, [prices]);

  const handleClick = useCallback((sym: string) => {
    onSymbolClick?.(sym);
  }, [onSymbolClick]);

  // Build ordered list twice for seamless scroll loop
  const tickerContent = WATCHED_SYMBOLS.map(sym => {
    const item = items[sym];
    if (!item) return (
      <span key={sym} className="ticker-item">
        <span className="font-mono text-[11px] font-bold text-[#4a6a8a]">{sym}</span>
        <span className="font-mono text-[11px] text-[#2a4a6a]">—</span>
      </span>
    );

    const up       = item.change_pct >= 0;
    const priceStr = item.price >= 1000
      ? `$${item.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : `$${item.price.toFixed(2)}`;
    const pctStr  = `${up ? "▲" : "▼"}${up ? "+" : ""}${item.change_pct.toFixed(2)}%`;
    const flashClass = item.flash === "green" ? "price-flash-green" : item.flash === "red" ? "price-flash-red" : "";

    return (
      <span
        key={sym}
        className={`ticker-item ${flashClass}`}
        onClick={() => handleClick(sym)}
        title={`${sym} — click to open chart`}
      >
        <span className="font-mono text-[11px] font-bold" style={{ color: "#8aadcc" }}>
          {sym}
        </span>
        <span className={`font-mono text-[11px] font-bold tabular-nums`} style={{ color: up ? "#00FF88" : "#FF2D55" }}>
          {priceStr}
        </span>
        <span className="font-mono text-[10px]" style={{ color: up ? "#00cc6a" : "#cc2244" }}>
          {pctStr}
        </span>
        <span className="text-[#1a2a40] mx-1 font-mono text-[10px]">|</span>
      </span>
    );
  });

  return (
    <div
      className="overflow-hidden shrink-0"
      style={{
        height:       28,
        background:   "rgba(0,0,0,0.45)",
        borderBottom: "1px solid rgba(0,212,255,0.08)",
        position:     "relative",
      }}
    >
      {/* Fade edges */}
      <div className="absolute left-0 top-0 bottom-0 w-8 z-10 pointer-events-none"
        style={{ background: "linear-gradient(to right, rgba(0,0,0,0.6), transparent)" }} />
      <div className="absolute right-0 top-0 bottom-0 w-8 z-10 pointer-events-none"
        style={{ background: "linear-gradient(to left, rgba(0,0,0,0.6), transparent)" }} />

      {/* Scrolling content — duplicated for seamless loop */}
      <div className="animate-ticker flex items-center h-full" style={{ width: "max-content" }}>
        {tickerContent}
        {tickerContent}
      </div>
    </div>
  );
}
