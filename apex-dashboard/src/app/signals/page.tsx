"use client";
import { useEffect, useRef, useState } from "react";
import { RefreshCw, SlidersHorizontal } from "lucide-react";
import type { Signal } from "@/lib/types";

// ── Embedded mock signals (shown when API returns empty) ─────────────────────
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

const REGIMES    = ["ALL", "BULL", "BEAR", "SIDEWAYS"] as const;
const DIRECTIONS = ["ALL", "UP", "DOWN", "NEUTRAL", "HOLD"] as const;

const DIR_CFG: Record<string, { color: string; arrow: string }> = {
  UP:      { color: "#00FF88", arrow: "▲" },
  DOWN:    { color: "#FF2D55", arrow: "▼" },
  NEUTRAL: { color: "#4a6a8a", arrow: "●" },
  HOLD:    { color: "#FFB800", arrow: "◆" },
};
const REG_CFG: Record<string, { color: string }> = {
  BULL: { color: "#00FF88" }, BEAR: { color: "#FF2D55" }, SIDEWAYS: { color: "#FFB800" },
};

const ALPHA_KEYS = ["tft", "rsi", "ema", "macd", "stochastic", "sentiment"] as const;
const ALPHA_LABEL: Record<string, string> = {
  tft: "TFT", rsi: "RSI", ema: "EMA", macd: "MCD", stochastic: "STO", sentiment: "SNT",
};

function timeAgo(ts: string) {
  const d = (Date.now() - new Date(ts).getTime()) / 1000;
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  return `${Math.floor(d / 3600)}h ago`;
}

function AlphaSquares({ breakdown }: { breakdown: Signal["alpha_breakdown"] }) {
  return (
    <div className="flex items-center gap-0.5">
      {ALPHA_KEYS.map((k) => {
        const v = breakdown?.[k] ?? 0;
        const pct = Math.min(v, 1);
        const bg = pct > 0.7 ? "#00FF88" : pct > 0.5 ? "#FFB800" : "#FF2D55";
        const opacity = 0.3 + pct * 0.7;
        return (
          <div key={k} className="flex flex-col items-center gap-0.5">
            <div
              title={`${ALPHA_LABEL[k]}: ${v.toFixed(2)}`}
              style={{
                width: 10, height: 10, borderRadius: 2,
                background: bg, opacity,
                boxShadow: pct > 0.7 ? `0 0 3px ${bg}80` : "none",
              }}
            />
          </div>
        );
      })}
    </div>
  );
}

function ScoreMiniBar({ score }: { score: number }) {
  const pct  = Math.min(Math.abs(score), 1) * 100;
  const color = score > 0.7 ? "#00FF88" : score > 0.5 ? "#00D4FF" : "#FFB800";
  return (
    <div className="flex items-center gap-1.5 min-w-[80px]">
      <div className="flex-1 h-1.5 rounded-full" style={{ background: "rgba(255,255,255,0.08)" }}>
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, background: color, boxShadow: `0 0 4px ${color}60` }}
        />
      </div>
      <span className="text-[10px] font-mono font-bold tabular-nums" style={{ color }}>
        {(score * 100).toFixed(0)}%
      </span>
    </div>
  );
}

function FilterPill<T extends string>({
  options, value, onChange, cfg,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
  cfg?: Record<string, { color: string; arrow?: string }>;
}) {
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {options.map((opt) => {
        const active = value === opt;
        const color  = cfg?.[opt]?.color ?? "#00D4FF";
        return (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            className="px-2.5 py-0.5 rounded font-mono text-[9px] uppercase tracking-wider transition-all"
            style={
              active
                ? { background: `${color}20`, border: `1px solid ${color}60`, color, boxShadow: `0 0 8px ${color}30` }
                : { background: "transparent", border: "1px solid rgba(255,255,255,0.08)", color: "#4a6a8a" }
            }
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

export default function SignalsPage() {
  const [signals, setSignals]     = useState<Signal[]>(MOCK_SIGNALS);
  const [isMock, setIsMock]       = useState(true);
  const [regime, setRegime]       = useState<"ALL" | Signal["regime"]>("ALL");
  const [direction, setDirection] = useState<"ALL" | Signal["direction"]>("ALL");
  const [minConf, setMinConf]     = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [live, setLive]           = useState(true);
  const intervalRef               = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    fetch(`/api/signals?limit=50`)
      .then((r) => r.json())
      .then((data) => {
        const list: Signal[] = data.signals ?? [];
        const mock = !!data.is_mock;
        if (list.length > 0) {
          setSignals(list);
          setIsMock(mock);
        } else {
          // No live signals — show mock data
          setSignals(MOCK_SIGNALS);
          setIsMock(true);
        }
      })
      .catch(() => {
        setSignals(MOCK_SIGNALS);
        setIsMock(true);
      });
  }, [refreshKey]);

  useEffect(() => {
    if (!live) { if (intervalRef.current) clearInterval(intervalRef.current); return; }
    intervalRef.current = setInterval(() => setRefreshKey((k) => k + 1), 5000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [live]);

  const filtered = signals.filter((s) => {
    if (regime    !== "ALL" && s.regime    !== regime)    return false;
    if (direction !== "ALL" && s.direction !== direction) return false;
    if (s.confidence < minConf / 100)                    return false;
    return true;
  });

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1
              className="font-heading text-2xl font-bold uppercase tracking-widest"
              style={{ color: "#00D4FF", textShadow: "0 0 20px rgba(0,212,255,0.5)" }}
            >
              Signal Stream
            </h1>
            {isMock && <span className="demo-badge">DEMO</span>}
          </div>
          <p className="text-[10px] font-mono text-[#4a6a8a] mt-0.5">
            {filtered.length} of {signals.length} signals displayed
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setLive((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded font-mono text-[10px] uppercase tracking-wider transition-all"
            style={
              live
                ? { background: "rgba(0,255,136,0.1)", border: "1px solid rgba(0,255,136,0.4)", color: "#00FF88" }
                : { background: "rgba(74,106,138,0.1)", border: "1px solid rgba(74,106,138,0.3)", color: "#4a6a8a" }
            }
          >
            <span
              className="w-1.5 h-1.5 rounded-full"
              style={{ background: live ? "#00FF88" : "#4a6a8a", animation: live ? "blink-dot 1s step-end infinite" : undefined }}
            />
            {live ? "LIVE 5s" : "PAUSED"}
          </button>
          <button
            onClick={() => setRefreshKey((k) => k + 1)}
            className="p-1.5 rounded transition-all"
            style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.25)", color: "#00D4FF" }}
          >
            <RefreshCw style={{ width: 12, height: 12 }} />
          </button>
        </div>
      </div>

      {/* ── Filters ── */}
      <div className="apex-card flex flex-wrap items-center gap-4 py-3">
        <SlidersHorizontal style={{ width: 14, height: 14, color: "#4a6a8a", flexShrink: 0 }} />
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">Regime</span>
          <FilterPill options={REGIMES} value={regime} onChange={setRegime as (v: string) => void} cfg={REG_CFG} />
        </div>
        <div className="w-px h-4 self-center" style={{ background: "rgba(255,255,255,0.08)" }} />
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">Direction</span>
          <FilterPill options={DIRECTIONS} value={direction} onChange={setDirection as (v: string) => void} cfg={DIR_CFG} />
        </div>
        <div className="w-px h-4 self-center" style={{ background: "rgba(255,255,255,0.08)" }} />
        <div className="flex items-center gap-2">
          <span className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">Min Conf</span>
          <input
            type="range" min="0" max="90" value={minConf}
            onChange={(e) => setMinConf(+e.target.value)}
            className="w-20"
          />
          <span className="text-[10px] font-mono w-8 tabular-nums" style={{ color: "#00D4FF" }}>{minConf}%</span>
        </div>
      </div>

      {/* ── Alpha key legend ── */}
      <div className="flex items-center gap-3 px-1">
        <span className="text-[9px] font-mono text-[#4a6a8a] uppercase tracking-wider">Alpha:</span>
        {ALPHA_KEYS.map((k) => (
          <span key={k} className="text-[9px] font-mono text-[#4a6a8a]">{ALPHA_LABEL[k]}</span>
        ))}
        <span className="ml-auto flex items-center gap-2 text-[8px] font-mono text-[#2a4a6a]">
          <span className="w-2 h-2 rounded-sm bg-[#00FF88] opacity-80" /> &gt;0.7
          <span className="w-2 h-2 rounded-sm bg-[#FFB800] opacity-80" /> 0.5–0.7
          <span className="w-2 h-2 rounded-sm bg-[#FF2D55] opacity-80" /> &lt;0.5
        </span>
      </div>

      {/* ── Signals Table ── */}
      <div className="apex-card p-0 overflow-x-auto">
        <table className="apex-table w-full">
          <thead>
            <tr>
              <th>SYMBOL</th>
              <th>DIRECTION</th>
              <th style={{ minWidth: 100 }}>SCORE</th>
              <th>CONF</th>
              <th colSpan={6}>ALPHA FACTORS</th>
              <th>REGIME</th>
              <th>WHEN</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={12} className="text-center py-10 font-mono text-[10px] tracking-widest" style={{ color: "#4a6a8a" }}>
                  — NO SIGNALS MATCH FILTERS —
                </td>
              </tr>
            ) : (
              filtered.map((s, i) => {
                const dc       = DIR_CFG[s.direction]  ?? { color: "#4a6a8a", arrow: "●" };
                const rc       = REG_CFG[s.regime]     ?? { color: "#4a6a8a" };
                const confColor = s.confidence >= 0.70 ? "#00FF88" : s.confidence >= 0.50 ? "#FFB800" : "#FF2D55";
                const borderColor = s.direction === "UP" ? "#00D4FF" : s.direction === "DOWN" ? "#FF2D55" : "#FFB800";
                return (
                  <tr
                    key={`${s.symbol}-${i}`}
                    className="animate-slide-up"
                    style={{ borderLeft: `3px solid ${borderColor}`, animationDelay: `${i * 40}ms` }}
                  >
                    {/* Symbol */}
                    <td>
                      <span className="font-mono font-bold text-sm text-white">{s.symbol}</span>
                    </td>

                    {/* Direction */}
                    <td>
                      <span
                        className="inline-flex items-center gap-1 text-[10px] font-mono font-bold px-2 py-0.5 rounded"
                        style={{ color: dc.color, background: `${dc.color}18`, border: `1px solid ${dc.color}40` }}
                      >
                        {dc.arrow} {s.direction}
                      </span>
                    </td>

                    {/* Score bar */}
                    <td><ScoreMiniBar score={s.ensemble_score} /></td>

                    {/* Confidence */}
                    <td>
                      <span className="font-mono text-[11px] font-bold" style={{ color: confColor }}>
                        {(s.confidence * 100).toFixed(0)}%
                      </span>
                    </td>

                    {/* Alpha factor squares — 6 cells collapsed into one td */}
                    <td colSpan={6}>
                      <div className="flex items-center gap-2">
                        <AlphaSquares breakdown={s.alpha_breakdown} />
                        {/* Numeric values for TFT and dominant */}
                        <div className="hidden lg:flex items-center gap-2 ml-1 text-[9px] font-mono text-[#4a6a8a]">
                          {ALPHA_KEYS.map((k) => {
                            const v = s.alpha_breakdown?.[k] ?? 0;
                            const color = v > 0.7 ? "#00FF88" : v > 0.5 ? "#FFB800" : "#FF2D55";
                            return (
                              <span key={k} style={{ color }}>{v.toFixed(2)}</span>
                            );
                          })}
                        </div>
                      </div>
                    </td>

                    {/* Regime */}
                    <td>
                      <span
                        className="text-[9px] font-mono px-1.5 py-0.5 rounded"
                        style={{ color: rc.color, background: `${rc.color}15`, border: `1px solid ${rc.color}35` }}
                      >
                        {s.regime}
                      </span>
                    </td>

                    {/* Time */}
                    <td>
                      <span className="text-[10px] font-mono whitespace-nowrap" style={{ color: "#4a6a8a" }}>
                        {timeAgo(s.timestamp)}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
