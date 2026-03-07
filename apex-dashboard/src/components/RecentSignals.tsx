"use client";
import { useEffect, useState } from "react";
import type { Signal } from "@/lib/types";

const NOW = Date.now();
const MOCK_SIGNALS: Signal[] = [
  {
    id: 1,
    symbol: "NVDA",
    timestamp: new Date(NOW - 3 * 60000).toISOString(),
    direction: "UP",
    ensemble_score: 0.82,
    confidence: 0.78,
    regime: "BULL",
    alpha_breakdown: { tft: 0.85, rsi: 0.72, ema: 0.80, macd: 0.75, stochastic: 0.68, sentiment: 0.91 },
  },
  {
    id: 2,
    symbol: "AAPL",
    timestamp: new Date(NOW - 8 * 60000).toISOString(),
    direction: "DOWN",
    ensemble_score: 0.71,
    confidence: 0.65,
    regime: "BEAR",
    alpha_breakdown: { tft: 0.68, rsi: 0.74, ema: 0.65, macd: 0.70, stochastic: 0.72, sentiment: 0.58 },
  },
  {
    id: 3,
    symbol: "MSFT",
    timestamp: new Date(NOW - 15 * 60000).toISOString(),
    direction: "UP",
    ensemble_score: 0.58,
    confidence: 0.52,
    regime: "SIDEWAYS",
    alpha_breakdown: { tft: 0.55, rsi: 0.48, ema: 0.62, macd: 0.51, stochastic: 0.44, sentiment: 0.63 },
  },
];

const DIR_CFG: Record<string, { color: string; arrow: string; bg: string }> = {
  UP:      { color: "#00FF88", arrow: "▲", bg: "rgba(0,255,136,0.06)"  },
  DOWN:    { color: "#FF2D55", arrow: "▼", bg: "rgba(255,45,85,0.06)"  },
  NEUTRAL: { color: "#4a6a8a", arrow: "●", bg: "rgba(74,106,138,0.06)" },
  HOLD:    { color: "#FFB800", arrow: "◆", bg: "rgba(255,184,0,0.06)"  },
};
const REG_CFG: Record<string, string> = {
  BULL: "#00FF88", BEAR: "#FF2D55", SIDEWAYS: "#FFB800",
};

function timeAgo(ts: string) {
  const d = (Date.now() - new Date(ts).getTime()) / 1000;
  if (d < 60)   return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  return `${Math.floor(d / 3600)}h ago`;
}

function ScoreBar({ score }: { score: number }) {
  const pct   = Math.min(Math.abs(score), 1) * 100;
  const color = score >= 0.7 ? "#00FF88" : score >= 0.5 ? "#FFB800" : "#FF2D55";
  const tip   = score >= 0.7
    ? `Score: ${pct.toFixed(0)}% — Strong signal`
    : score >= 0.5
    ? `Score: ${pct.toFixed(0)}% — Below confidence threshold`
    : `Score: ${pct.toFixed(0)}% — Weak signal`;
  return (
    <div className="flex items-center gap-1.5 w-[140px] flex-shrink-0" title={tip}>
      <div
        className="flex-1 h-1.5 rounded-full overflow-hidden"
        style={{ background: "rgba(255,255,255,0.07)" }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: color, boxShadow: `0 0 4px ${color}60` }}
        />
      </div>
      <span
        className="text-[10px] font-mono font-bold tabular-nums w-7 text-right flex-shrink-0"
        style={{ color }}
      >
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}

interface Props {
  refreshKey?: number;
  limit?: number;
}

export default function RecentSignals({ refreshKey = 0, limit = 10 }: Props) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [isMock,  setIsMock]  = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/signals?limit=${limit}`)
      .then((r) => r.json())
      .then((data) => {
        const list: Signal[] = data.signals ?? [];
        if (list.length === 0) {
          setSignals(MOCK_SIGNALS.slice(0, limit));
          setIsMock(true);
        } else {
          setSignals(list.slice(0, limit));
          setIsMock(!!data.is_mock);
        }
      })
      .catch(() => {
        setSignals(MOCK_SIGNALS.slice(0, limit));
        setIsMock(true);
      })
      .finally(() => setLoading(false));
  }, [refreshKey, limit]);

  return (
    <div className="apex-card p-0 overflow-hidden">
      {/* ── Section header ── */}
      <div
        className="flex items-center justify-between px-4 py-2.5"
        style={{ borderBottom: "1px solid rgba(0,212,255,0.1)" }}
      >
        <span className="text-[11px] font-mono font-bold uppercase tracking-widest text-[#8aadcc]">
          Recent Signals
        </span>
        <div className="flex items-center gap-1.5">
          {isMock && <span className="demo-badge">DEMO</span>}
          <span className="text-[10px] font-mono text-[#4a6a8a]">
            · {loading ? "…" : signals.length} signals
          </span>
        </div>
      </div>

      {/* ── Loading skeleton ── */}
      {loading && (
        <div className="p-3 space-y-2">
          {[...Array(3)].map((_, i) => (
            <div
              key={i}
              className="h-9 rounded animate-pulse"
              style={{ background: "rgba(0,212,255,0.04)" }}
            />
          ))}
        </div>
      )}

      {/* ── Signal rows ── */}
      {!loading && (
        <div className="divide-y" style={{ borderColor: "rgba(0,212,255,0.06)" }}>
          {signals.map((s, i) => {
            const dc        = DIR_CFG[s.direction] ?? DIR_CFG.NEUTRAL;
            const regColor  = REG_CFG[s.regime]    ?? "#4a6a8a";
            const confColor = s.confidence >= 0.7 ? "#00FF88" : s.confidence >= 0.5 ? "#FFB800" : "#FF2D55";

            // Active alpha sources (value > 0.6)
            const sources = Object.entries(s.alpha_breakdown ?? {})
              .filter(([, v]) => (v ?? 0) > 0.6)
              .map(([k]) => k.toUpperCase().slice(0, 3));

            return (
              <div
                key={`${s.symbol}-${i}`}
                className="flex items-center gap-3 px-4 transition-colors animate-slide-up"
                style={{
                  minHeight:    36,
                  paddingTop:   6,
                  paddingBottom: 6,
                  borderLeft:   `3px solid ${dc.color}`,
                  background:   dc.bg,
                  animationDelay: `${i * 40}ms`,
                  cursor: "default",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background =
                    dc.bg.replace("0.06)", "0.12)");
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.background = dc.bg;
                }}
              >
                {/* Symbol — 52px */}
                <span
                  className="font-mono font-bold text-sm text-white flex-shrink-0"
                  style={{ width: 52 }}
                >
                  {s.symbol}
                </span>

                {/* Direction badge — 60px */}
                <span
                  className="inline-flex items-center justify-center gap-1 text-[10px] font-mono font-bold rounded flex-shrink-0"
                  style={{
                    width:      60,
                    padding:    "2px 0",
                    color:      dc.color,
                    background: `${dc.color}18`,
                    border:     `1px solid ${dc.color}40`,
                  }}
                >
                  {dc.arrow} {s.direction}
                </span>

                {/* Score bar — 140px */}
                <ScoreBar score={s.ensemble_score} />

                {/* Confidence — 40px */}
                <span
                  className="font-mono text-[11px] font-bold tabular-nums flex-shrink-0 text-right"
                  style={{ width: 40, color: confColor }}
                >
                  {(s.confidence * 100).toFixed(0)}%
                </span>

                {/* Source pills */}
                <div className="hidden sm:flex items-center gap-0.5 flex-shrink-0">
                  {sources.slice(0, 3).map((src) => (
                    <span
                      key={src}
                      className="text-[8px] font-mono px-1 rounded"
                      style={{
                        background: "rgba(0,212,255,0.1)",
                        color:      "#00D4FF",
                        border:     "1px solid rgba(0,212,255,0.2)",
                        lineHeight: "16px",
                      }}
                    >
                      {src}
                    </span>
                  ))}
                </div>

                {/* Regime badge — 70px */}
                <span
                  className="text-[9px] font-mono rounded hidden md:inline flex-shrink-0 text-center"
                  style={{
                    width:      70,
                    padding:    "2px 0",
                    color:      regColor,
                    background: `${regColor}12`,
                    border:     `1px solid ${regColor}30`,
                  }}
                >
                  {s.regime}
                </span>

                {/* Time — right-aligned gray */}
                <span
                  className="text-[9px] font-mono text-[#4a6a8a] flex-shrink-0 ml-auto"
                  style={{ minWidth: 46, textAlign: "right" }}
                >
                  {timeAgo(s.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
