"use client";

import { useEffect, useRef, useState } from "react";
import { ZoomIn } from "lucide-react";

// ── TradingView interval map ──────────────────────────────────────────────────
const TF_MAP: Record<string, string> = {
  "1m":  "1",
  "5m":  "5",
  "15m": "15",
  "1H":  "60",
  "4H":  "240",
  "1D":  "D",
};

// ── Types ────────────────────────────────────────────────────────────────────

// (kept for interface stability — consumed by ChartModal and other callers)
interface OHLCVBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface Signal {
  id: string;
  symbol: string;
  direction: "up" | "down" | "hold";
  confidence: number;
  price: number;
  timestamp: string;
}

interface Position {
  symbol: string;
  side: "long" | "short";
  avg_entry_price: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  qty: number;
}

interface CandlestickChartProps {
  symbol?: string;
  height?: number;
  showSignals?: boolean;
  showPositions?: boolean;
  compact?: boolean;
  onExpand?: () => void;
}

// ── Main component ────────────────────────────────────────────────────────────
export default function CandlestickChart({
  symbol       = "NVDA",
  height       = 400,
  showSignals  = true,
  showPositions = true,
  compact      = false,
  onExpand,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const widgetRef    = useRef<any>(null);

  const [timeframe,   setTimeframe]   = useState("15m");
  const [mounted,     setMounted]     = useState(false);
  const [lastPrice,   setLastPrice]   = useState("");
  const [priceColor,  setPriceColor]  = useState("#00D4FF");

  useEffect(() => { setMounted(true); }, []);

  // ── TradingView widget init ──────────────────────────────────────────────
  useEffect(() => {
    if (!mounted || !containerRef.current) return;

    // Clear previous widget content
    containerRef.current.innerHTML = "";

    const containerId = `tv-chart-${symbol.replace(/[^a-zA-Z0-9]/g, "_")}-${timeframe.replace(/[^a-zA-Z0-9]/g, "_")}`;
    containerRef.current.id = containerId;

    const SCRIPT_ID = "tradingview-widget-script";

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const initWidget = () => {
      if (!containerRef.current) return;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const TV = (window as any).TradingView;
      if (!TV) return;

      if (widgetRef.current?.remove) {
        try { widgetRef.current.remove(); } catch { /* ignore */ }
        widgetRef.current = null;
      }

      widgetRef.current = new TV.widget({
        container_id: containerId,
        width:        "100%",
        height:       height - (compact ? 0 : 50),
        autosize:     true,

        symbol:   `NASDAQ:${symbol}`,
        interval: TF_MAP[timeframe] ?? "15",

        theme:      "dark",
        style:      "1",  // candlesticks
        locale:     "en",

        toolbar_bg: "#050810",
        overrides: {
          "mainSeriesProperties.candleStyle.upColor":         "#00FF88",
          "mainSeriesProperties.candleStyle.downColor":       "#FF2D55",
          "mainSeriesProperties.candleStyle.borderUpColor":   "#00cc6a",
          "mainSeriesProperties.candleStyle.borderDownColor": "#cc2244",
          "mainSeriesProperties.candleStyle.wickUpColor":     "#00cc6a",
          "mainSeriesProperties.candleStyle.wickDownColor":   "#cc2244",
          "paneProperties.background":                        "#050810",
          "paneProperties.backgroundType":                    "solid",
          "paneProperties.vertGridProperties.color":         "#0d1f2d",
          "paneProperties.horzGridProperties.color":         "#0d1f2d",
          "scalesProperties.textColor":                      "#8899aa",
          "scalesProperties.lineColor":                      "#0d1f2d",
        },

        studies: [
          {
            id:     "MASimple@tv-basicstudies",
            inputs: { length: 20 },
            overrides: { "Plot.color": "#00D4FF", "Plot.linewidth": 1 },
          },
          {
            id:     "MASimple@tv-basicstudies",
            inputs: { length: 50 },
            overrides: { "Plot.color": "#9945FF", "Plot.linewidth": 1 },
          },
          "Volume@tv-basicstudies",
        ],

        hide_top_toolbar:   compact,
        hide_legend:        false,
        hide_side_toolbar:  false,
        allow_symbol_change: false,
        save_image:         false,
        show_popup_button:  false,
        withdateranges:     !compact,

        disabled_features: [
          "use_localstorage_for_settings",
          "header_symbol_search",
          "header_compare",
          "header_undo_redo",
          "header_screenshot",
          "header_fullscreen_button",
          "go_to_date",
          "context_menus",
          "border_around_the_chart",
          "remove_library_container_border",
        ],
        enabled_features: [
          "hide_left_toolbar_by_default",
          "move_logo_to_main_pane",
        ],
      });
    };

    const existing = document.getElementById(SCRIPT_ID);
    if (!existing) {
      const script  = document.createElement("script");
      script.id     = SCRIPT_ID;
      script.src    = "https://s3.tradingview.com/tv.js";
      script.async  = true;
      script.onload = initWidget;
      document.head.appendChild(script);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } else if ((window as any).TradingView) {
      initWidget();
    } else {
      existing.addEventListener("load", initWidget, { once: true });
    }

    return () => {
      if (widgetRef.current?.remove) {
        try { widgetRef.current.remove(); } catch { /* ignore */ }
        widgetRef.current = null;
      }
    };
  }, [symbol, timeframe, height, compact, mounted]);

  // ── Live price (lightweight poll) ────────────────────────────────────────
  useEffect(() => {
    if (!mounted) return;
    let cancelled = false;

    const fetchPrice = async () => {
      try {
        const res = await fetch(`/api/quote?symbol=${symbol}`,
          { signal: AbortSignal.timeout(3000), cache: "no-store" });
        if (!res.ok || cancelled) return;
        const data = await res.json() as { price?: number; change_pct?: number };
        if (data.price) {
          setLastPrice(`$${data.price.toFixed(2)}`);
          setPriceColor((data.change_pct ?? 0) >= 0 ? "#00FF88" : "#FF2D55");
        }
      } catch { /* silent */ }
    };

    fetchPrice();
    const id = setInterval(fetchPrice, 15_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [symbol, mounted]);

  // ── SSR skeleton ─────────────────────────────────────────────────────────
  if (!mounted) {
    return (
      <div
        className="rounded-lg animate-pulse"
        style={{ height, background: "rgba(5,8,16,0.5)", border: "1px solid rgba(0,212,255,0.06)" }}
      />
    );
  }

  return (
    <div
      className="flex flex-col rounded-lg overflow-hidden"
      style={{ height, background: "#050810", border: "1px solid rgba(0,212,255,0.12)" }}
    >
      {/* ── Header toolbar (non-compact) ────────────────────────────────── */}
      {!compact && (
        <div
          className="flex items-center justify-between px-3 py-2 shrink-0"
          style={{ background: "rgba(7,12,24,0.95)", borderBottom: "1px solid rgba(0,212,255,0.1)" }}
        >
          {/* Symbol + live price */}
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm font-bold tracking-widest" style={{ color: "#e2f0ff" }}>
              {symbol}
            </span>
            {lastPrice && (
              <span className="font-mono text-sm font-bold" style={{ color: priceColor }}>
                {lastPrice}
              </span>
            )}
          </div>

          {/* Timeframe pills */}
          <div className="flex items-center gap-1">
            {Object.keys(TF_MAP).map(tf => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className="px-2 py-0.5 font-mono text-[10px] font-bold rounded uppercase tracking-wider transition-all"
                style={{
                  background: timeframe === tf ? "rgba(0,212,255,0.15)" : "transparent",
                  color:      timeframe === tf ? "#00D4FF" : "#445566",
                  border:     `1px solid ${timeframe === tf ? "rgba(0,212,255,0.4)" : "transparent"}`,
                }}
              >
                {tf}
              </button>
            ))}
          </div>

          {/* EMA legend */}
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 font-mono text-[9px] text-gray-600">
              <span className="w-4 h-px inline-block bg-cyan-400" />
              EMA20
            </span>
            <span className="flex items-center gap-1.5 font-mono text-[9px] text-gray-600">
              <span className="w-4 h-px inline-block bg-purple-400" />
              EMA50
            </span>
          </div>
        </div>
      )}

      {/* ── Compact header ──────────────────────────────────────────────── */}
      {compact && (
        <div
          className="flex items-center justify-between px-2 py-1.5 shrink-0"
          style={{ background: "rgba(7,12,24,0.9)", borderBottom: "1px solid rgba(0,212,255,0.08)" }}
        >
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-bold" style={{ color: "#e2f0ff" }}>{symbol}</span>
            {lastPrice && (
              <span className="font-mono text-xs" style={{ color: priceColor }}>{lastPrice}</span>
            )}
          </div>
          {onExpand && (
            <button
              onClick={onExpand}
              className="flex items-center gap-1 font-mono text-[9px] uppercase hover:opacity-80 transition-opacity"
              style={{ color: "#4a6a8a" }}
            >
              <ZoomIn style={{ width: 10, height: 10 }} />
              EXPAND
            </button>
          )}
        </div>
      )}

      {/*
        ── TradingView container ─────────────────────────────────────────
        CRITICAL: flex-1 + minHeight: 0 makes this fill all available space
        TradingView's autosize:true will match the container dimensions
      */}
      <div
        ref={containerRef}
        style={{ flex: 1, minHeight: 0, width: "100%" }}
      />
    </div>
  );
}
