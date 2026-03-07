"use client";

import { useEffect, useRef, useState } from "react";

interface PnLData {
  equity: number;
  last_equity: number;
  pnl_today: number;
  pnl_today_pct: number;
  buying_power: number;
  portfolio_value: number;
  week_pnl?: number;
  win_rate?: number;
  sharpe?: number;
  max_dd?: number;
  total_trades_today?: number;
  is_mock?: boolean;
  error?: string;
}

function fmt(n: number): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${n.toFixed(2)}`;
}

function fmtPct(n: number): string {
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function useCountUp(target: number, duration = 800) {
  const [val, setVal] = useState(0);
  const prev = useRef(0);
  useEffect(() => {
    const from = prev.current;
    const diff = target - from;
    if (diff === 0) return;
    const start = performance.now();
    const frame = (now: number) => {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(from + diff * eased);
      if (p < 1) requestAnimationFrame(frame);
      else prev.current = target;
    };
    requestAnimationFrame(frame);
  }, [target, duration]);
  return val;
}

/* Mini SVG ring for win rate */
function WinRateRing({ pct }: { pct: number }) {
  const r = 22;
  const circ = 2 * Math.PI * r;
  const fill = (pct / 100) * circ;
  const color = pct >= 55 ? "#00FF88" : pct >= 48 ? "#FFB800" : "#FF2D55";
  return (
    <svg width="56" height="56" viewBox="0 0 56 56">
      <circle cx="28" cy="28" r={r} fill="none" stroke="rgba(26,42,64,0.8)" strokeWidth="4" />
      <circle
        cx="28" cy="28" r={r} fill="none"
        stroke={color} strokeWidth="4"
        strokeDasharray={`${fill} ${circ}`}
        strokeLinecap="round"
        className="ring-progress"
        style={{ filter: `drop-shadow(0 0 4px ${color})` }}
      />
      <text x="28" y="32" textAnchor="middle" fill={color}
        fontSize="11" fontFamily="JetBrains Mono, monospace" fontWeight="700">
        {pct.toFixed(0)}%
      </text>
    </svg>
  );
}

export default function PnLTicker({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<PnLData | null>(null);

  useEffect(() => {
    fetch("/api/pnl")
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData({
        equity: 0, last_equity: 0, pnl_today: 0, pnl_today_pct: 0,
        buying_power: 0, portfolio_value: 0, error: "offline",
      }));
  }, [refreshKey]);

  const pv      = useCountUp(data?.portfolio_value ?? 0);
  const pnl     = useCountUp(data?.pnl_today ?? 0);
  const bp      = useCountUp(data?.buying_power ?? 0);
  const pnlPct  = data?.pnl_today_pct ?? 0;
  const winRate = data?.win_rate ?? 0;
  const sharpe  = data?.sharpe ?? 0;
  const maxDD   = data?.max_dd ?? 0;
  const trades  = data?.total_trades_today ?? 0;
  const pnlUp   = pnl >= 0;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">

      {/* Portfolio Value */}
      <div className="apex-card apex-card-cyan col-span-2 sm:col-span-1">
        <div className="flex items-center justify-between mb-1">
          <div className="text-[9px] text-[#4a6a8a] uppercase tracking-widest">Portfolio Value</div>
          {data?.is_mock === false
            ? <span className="text-[8px] font-mono font-bold px-1.5 py-0.5 rounded" style={{ color: "#00FF88", background: "rgba(0,255,136,0.1)", border: "1px solid rgba(0,255,136,0.3)" }}>● LIVE</span>
            : <span className="text-[8px] font-mono font-bold px-1.5 py-0.5 rounded" style={{ color: "#FFB800", background: "rgba(255,184,0,0.1)", border: "1px solid rgba(255,184,0,0.3)" }}>○ DEMO</span>
          }
        </div>
        <div className="text-xl font-bold font-mono text-glow-cyan" style={{ color: "#00D4FF" }}>
          {fmt(pv)}
        </div>
        <div className="text-[10px] text-[#4a6a8a] mt-1">{trades} trades today</div>
      </div>

      {/* Day P&L */}
      <div className={`apex-card ${pnlUp ? "apex-card-green" : "apex-card-red"}`}>
        <div className="text-[9px] text-[#4a6a8a] uppercase tracking-widest mb-1">Day P&L</div>
        <div
          className={`text-lg font-bold font-mono ${pnlUp ? "text-glow-green" : "text-glow-red"}`}
          style={{ color: pnlUp ? "#00FF88" : "#FF2D55" }}
        >
          {pnlUp ? "+" : ""}{fmt(pnl)}
        </div>
        <div className="text-[10px] mt-1" style={{ color: pnlUp ? "#00FF88" : "#FF2D55" }}>
          {fmtPct(pnlPct)}
        </div>
      </div>

      {/* Win Rate ring */}
      <div className="apex-card flex flex-col items-center justify-center gap-1">
        <div className="text-[9px] text-[#4a6a8a] uppercase tracking-widest">Win Rate</div>
        <WinRateRing pct={winRate} />
        <div className="text-[9px] text-[#4a6a8a]">last 30 trades</div>
      </div>

      {/* Sharpe */}
      <div className="apex-card">
        <div className="text-[9px] text-[#4a6a8a] uppercase tracking-widest mb-2">Sharpe Ratio</div>
        <div
          className="text-lg font-bold font-mono"
          style={{ color: sharpe >= 1.5 ? "#00FF88" : sharpe >= 1.0 ? "#FFB800" : "#FF2D55" }}
        >
          {sharpe.toFixed(2)}
        </div>
        <div className="mt-2">
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{
                width: `${Math.min((sharpe / 3) * 100, 100)}%`,
                background: sharpe >= 1.5 ? "#00FF88" : sharpe >= 1.0 ? "#FFB800" : "#FF2D55",
              }}
            />
          </div>
          <div className="text-[8px] text-[#4a6a8a] mt-0.5">{sharpe >= 1.5 ? "Excellent" : sharpe >= 1.0 ? "Good" : "Poor"}</div>
        </div>
      </div>

      {/* Max Drawdown */}
      <div className="apex-card">
        <div className="text-[9px] text-[#4a6a8a] uppercase tracking-widest mb-2">Max Drawdown</div>
        <div className="text-lg font-bold font-mono text-glow-red" style={{ color: "#FF2D55" }}>
          {maxDD.toFixed(1)}%
        </div>
        <div className="mt-2">
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{
                width: `${Math.min(Math.abs(maxDD) * 5, 100)}%`,
                background: Math.abs(maxDD) <= 2 ? "#00FF88" : Math.abs(maxDD) <= 5 ? "#FFB800" : "#FF2D55",
              }}
            />
          </div>
          <div className="text-[8px] text-[#4a6a8a] mt-0.5">
            BP: {fmt(bp)}
          </div>
        </div>
      </div>

    </div>
  );
}
