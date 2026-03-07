"use client";

import { useEffect, useState } from "react";
import {
  AreaChart, Area, BarChart, Bar, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";
import Sidebar from "@/components/Sidebar";

// ── Types ─────────────────────────────────────────────────────────────────
interface RiskMetrics {
  var_1d_95:      number;
  var_1d_99:      number;
  max_drawdown:   number;
  current_dd:     number;
  sharpe:         number;
  sortino:        number;
  beta:           number;
  alpha:          number;
  portfolio_value: number;
  is_mock:        boolean;
}

interface PositionExposure {
  symbol:       string;
  market_value: number;
  pnl_pct:      number;
  sector:       string;
  weight:       number;
}

interface DrawdownPoint { date: string; value: number; }
interface SectorBar     { sector: string; pct: number; }

// ── Mock helpers ────────────────────────────────────────────────────────────
const MOCK_METRICS: RiskMetrics = {
  var_1d_95:       -2340.50,
  var_1d_99:       -3812.20,
  max_drawdown:    -18.4,
  current_dd:      -3.2,
  sharpe:          1.83,
  sortino:         2.41,
  beta:            0.94,
  alpha:           0.062,
  portfolio_value: 124_800,
  is_mock:         true,
};

const MOCK_POSITIONS: PositionExposure[] = [
  { symbol: "NVDA",  market_value: 49_280, pnl_pct:  8.2, sector: "Tech",      weight: 39.5 },
  { symbol: "MSFT",  market_value: 33_528, pnl_pct:  2.1, sector: "Tech",      weight: 26.9 },
  { symbol: "AAPL",  market_value: 17_900, pnl_pct: -1.3, sector: "Tech",      weight: 14.3 },
  { symbol: "SPY",   market_value:  9_840, pnl_pct:  0.7, sector: "ETF",       weight:  7.9 },
  { symbol: "AMZN",  market_value:  7_920, pnl_pct:  3.5, sector: "Consumer",  weight:  6.3 },
  { symbol: "TSLA",  market_value:  6_200, pnl_pct: -4.8, sector: "Auto/EV",   weight:  5.0 },
];

const MOCK_SECTORS: SectorBar[] = [
  { sector: "Tech",     pct: 80.7 },
  { sector: "ETF",      pct:  7.9 },
  { sector: "Consumer", pct:  6.3 },
  { sector: "Auto/EV",  pct:  5.0 },
];

function makeMockDrawdown(days = 30): DrawdownPoint[] {
  const pts: DrawdownPoint[] = [];
  let val = 0;
  for (let i = days; i >= 0; i--) {
    val += (Math.random() - 0.55) * 0.8;
    if (val > 0) val = 0;
    if (val < -22) val = -22;
    const d = new Date();
    d.setDate(d.getDate() - i);
    pts.push({ date: d.toISOString().slice(0, 10), value: parseFloat(val.toFixed(2)) });
  }
  return pts;
}

const MOCK_DRAWDOWN = makeMockDrawdown(30);

// ── Gauge component ─────────────────────────────────────────────────────────
function DrawdownGauge({ current, max }: { current: number; max: number }) {
  const pct = max === 0 ? 0 : Math.min(Math.abs(current / max) * 100, 100);
  const color = pct < 33 ? "#00FF88" : pct < 66 ? "#FFB800" : "#FF2D55";
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-28 h-14 overflow-hidden">
        <div className="absolute inset-0 w-28 h-28 border-[12px] border-[#0d1520] rounded-full" />
        <div
          className="absolute inset-0 w-28 h-28 border-[12px] rounded-full transition-all duration-700"
          style={{
            borderColor: `${color} transparent transparent transparent`,
            transform: `rotate(${pct * 1.8 - 90}deg)`,
          }}
        />
        <div
          className="absolute inset-0 w-28 h-28 border-[12px] rounded-full"
          style={{ borderColor: "#1a2535 #1a2535 transparent transparent", transform: "rotate(-90deg)" }}
        />
      </div>
      <span className="text-lg font-bold" style={{ color }}>{current.toFixed(1)}%</span>
      <span className="text-[10px] text-gray-500">of max {max.toFixed(1)}%</span>
    </div>
  );
}

// ── Alert badges ────────────────────────────────────────────────────────────
const RISK_ALERTS = [
  { level: "warn",  text: "Tech sector concentration at 80.7% — above 60% threshold" },
  { level: "warn",  text: "NVDA weight 39.5% — single-stock limit is 40%" },
  { level: "ok",    text: "Portfolio VaR within daily risk budget" },
  { level: "ok",    text: "Beta 0.94 — market exposure nominal" },
  { level: "info",  text: "Drawdown 3.2% — within 5% soft limit" },
];

function AlertBadge({ level, text }: { level: string; text: string }) {
  const map: Record<string, string> = {
    warn: "border-yellow-500/50 bg-yellow-500/5 text-yellow-300",
    ok:   "border-green-500/50  bg-green-500/5  text-green-300",
    info: "border-blue-500/50   bg-blue-500/5   text-blue-300",
  };
  const icon: Record<string, string> = { warn: "⚠", ok: "✓", info: "ℹ" };
  return (
    <div className={`flex items-start gap-2 border rounded px-3 py-2 text-xs ${map[level] ?? map.info}`}>
      <span className="mt-0.5 shrink-0">{icon[level] ?? "·"}</span>
      <span>{text}</span>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function RiskPage() {
  const [metrics,   setMetrics]   = useState<RiskMetrics | null>(null);
  const [positions, setPositions] = useState<PositionExposure[]>([]);
  const [sectors,   setSectors]   = useState<SectorBar[]>([]);
  const [drawdown,  setDrawdown]  = useState<DrawdownPoint[]>([]);
  const [loading,   setLoading]   = useState(true);

  useEffect(() => {
    async function load() {
      try {
        // Try to derive metrics from existing positions API
        const posRes = await fetch("/api/positions").then(r => r.json());
        const rawPos  = posRes.positions ?? posRes ?? [];

        if (rawPos.length > 0) {
          const totalVal = rawPos.reduce((s: number, p: {market_value?: string | number}) =>
            s + parseFloat(String(p.market_value ?? 0)), 0);
          const derived: PositionExposure[] = rawPos.map((p: {
            symbol: string;
            market_value?: string | number;
            unrealized_plpc?: string | number;
          }) => ({
            symbol:       p.symbol,
            market_value: parseFloat(String(p.market_value ?? 0)),
            pnl_pct:      parseFloat(String(p.unrealized_plpc ?? 0)) * 100,
            sector:       "Tech",
            weight:       totalVal > 0
              ? (parseFloat(String(p.market_value ?? 0)) / totalVal) * 100
              : 0,
          }));

          // Aggregate sectors
          const sectorMap: Record<string, number> = {};
          derived.forEach(d => {
            sectorMap[d.sector] = (sectorMap[d.sector] ?? 0) + d.weight;
          });

          setPositions(derived);
          setSectors(
            Object.entries(sectorMap)
              .map(([sector, pct]) => ({ sector, pct: parseFloat(pct.toFixed(1)) }))
              .sort((a, b) => b.pct - a.pct)
          );
          setMetrics({ ...MOCK_METRICS, portfolio_value: totalVal, is_mock: false });
        } else {
          setPositions(MOCK_POSITIONS);
          setSectors(MOCK_SECTORS);
          setMetrics(MOCK_METRICS);
        }
      } catch {
        setPositions(MOCK_POSITIONS);
        setSectors(MOCK_SECTORS);
        setMetrics(MOCK_METRICS);
      }
      setDrawdown(MOCK_DRAWDOWN);
      setLoading(false);
    }
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const kpis = metrics ? [
    { label: "VaR 1D (95%)",        value: `$${Math.abs(metrics.var_1d_95).toFixed(0)}`, sub: "max expected loss",   neg: true  },
    { label: "VaR 1D (99%)",        value: `$${Math.abs(metrics.var_1d_99).toFixed(0)}`, sub: "tail risk estimate",  neg: true  },
    { label: "Sharpe Ratio",        value: metrics.sharpe.toFixed(2),                    sub: "risk-adjusted return",neg: false },
    { label: "Portfolio Beta",      value: metrics.beta.toFixed(2),                      sub: "vs. S&P 500",         neg: false },
  ] : [];

  const heatColor = (pnl: number) => {
    if (pnl >  5) return "#00FF88";
    if (pnl >  2) return "#00cc6a";
    if (pnl >  0) return "#007a40";
    if (pnl > -2) return "#7a2030";
    if (pnl > -5) return "#cc2244";
    return "#FF2D55";
  };

  const radarData = metrics ? [
    { axis: "VaR",     value: Math.min(100, 100 - Math.abs(metrics.var_1d_95) / 50) },
    { axis: "Draw",    value: Math.min(100, 100 + metrics.current_dd * 3) },
    { axis: "Sharpe",  value: Math.min(100, metrics.sharpe * 40) },
    { axis: "Sortino", value: Math.min(100, metrics.sortino * 35) },
    { axis: "Beta",    value: Math.min(100, 100 - Math.abs(metrics.beta - 1) * 100) },
    { axis: "Conc.",   value: Math.min(100, 100 - (positions[0]?.weight ?? 40)) },
  ] : [];

  return (
    <div className="flex h-screen bg-[#050810] text-white overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-wider text-white">
              RISK MONITOR
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">
              Portfolio risk metrics — updates every 30s
              {metrics?.is_mock && (
                <span className="ml-2 text-yellow-400/70">[DEMO DATA]</span>
              )}
            </p>
          </div>
          <div className="text-right">
            <div className="text-xs text-gray-500">Portfolio Value</div>
            <div className="text-xl font-bold text-cyan-400">
              ${metrics ? metrics.portfolio_value.toLocaleString() : "—"}
            </div>
          </div>
        </div>

        {/* KPI row */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {loading
            ? Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="fold-card p-4 animate-pulse">
                  <div className="h-3 w-16 bg-gray-700 rounded mb-2" />
                  <div className="h-7 w-20 bg-gray-600 rounded" />
                </div>
              ))
            : kpis.map(k => (
                <div key={k.label} className="fold-card p-4">
                  <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">{k.label}</div>
                  <div className={`text-2xl font-bold ${k.neg ? "text-red-400" : "text-cyan-400"}`}>
                    {k.value}
                  </div>
                  <div className="text-[10px] text-gray-600 mt-1">{k.sub}</div>
                </div>
              ))
          }
        </div>

        {/* Middle row — heatmap / drawdown gauge / radar */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

          {/* Position heat map */}
          <div className="lg:col-span-1 fold-card p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-3">Position Heat Map</div>
            <div className="grid grid-cols-3 gap-1.5">
              {positions.map(pos => (
                <div
                  key={pos.symbol}
                  className="heat-cell flex flex-col items-center justify-center rounded text-xs font-bold p-2"
                  style={{
                    background: `${heatColor(pos.pnl_pct)}18`,
                    border:     `1px solid ${heatColor(pos.pnl_pct)}40`,
                    minHeight:  `${Math.max(48, pos.weight * 2)}px`,
                  }}
                >
                  <span style={{ color: heatColor(pos.pnl_pct) }}>{pos.symbol}</span>
                  <span className="text-[9px] text-gray-400">{pos.weight.toFixed(1)}%</span>
                  <span className={`text-[9px] ${pos.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {pos.pnl_pct >= 0 ? "+" : ""}{pos.pnl_pct.toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Drawdown gauge + radar */}
          <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Gauge */}
            <div className="fold-card p-4 flex flex-col items-center justify-center gap-4">
              <div className="text-[10px] text-gray-500 uppercase tracking-widest self-start">Drawdown Status</div>
              <DrawdownGauge
                current={metrics?.current_dd ?? 0}
                max={metrics?.max_drawdown ?? -18.4}
              />
              <div className="w-full grid grid-cols-2 gap-2 text-center mt-2">
                <div>
                  <div className="text-xs text-gray-500">Sharpe</div>
                  <div className="text-lg font-bold text-cyan-400">{metrics?.sharpe.toFixed(2) ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Sortino</div>
                  <div className="text-lg font-bold text-purple-400">{metrics?.sortino.toFixed(2) ?? "—"}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Alpha</div>
                  <div className="text-lg font-bold text-green-400">
                    {metrics ? `${(metrics.alpha * 100).toFixed(1)}%` : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500">Beta</div>
                  <div className="text-lg font-bold text-gray-300">{metrics?.beta.toFixed(2) ?? "—"}</div>
                </div>
              </div>
            </div>

            {/* Risk Radar */}
            <div className="fold-card p-4">
              <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">Risk Profile Radar</div>
              <ResponsiveContainer width="100%" height={200}>
                <RadarChart data={radarData}>
                  <PolarGrid stroke="#1e2d40" />
                  <PolarAngleAxis dataKey="axis" tick={{ fill: "#6b7280", fontSize: 10 }} />
                  <Radar
                    dataKey="value"
                    fill="#00D4FF"
                    fillOpacity={0.15}
                    stroke="#00D4FF"
                    strokeWidth={1.5}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Bottom row — drawdown chart / sector exposure / alerts */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

          {/* Drawdown area chart */}
          <div className="lg:col-span-1 fold-card p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-3">30-Day Drawdown</div>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={drawdown}>
                <XAxis dataKey="date" hide />
                <YAxis domain={["auto", 0]} hide />
                <Tooltip
                  contentStyle={{ background: "#0d1520", border: "1px solid #1e2d40", fontSize: 11 }}
                  formatter={(v: number) => [`${v.toFixed(1)}%`, "DD"]}
                />
                <Area
                  dataKey="value"
                  stroke="#FF2D55"
                  fill="#FF2D55"
                  fillOpacity={0.12}
                  strokeWidth={1.5}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Sector exposure */}
          <div className="lg:col-span-1 fold-card p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-3">Sector Exposure</div>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={sectors} layout="vertical">
                <XAxis type="number" domain={[0, 100]} tick={{ fill: "#6b7280", fontSize: 10 }} />
                <YAxis type="category" dataKey="sector" width={60} tick={{ fill: "#9ca3af", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: "#0d1520", border: "1px solid #1e2d40", fontSize: 11 }}
                  formatter={(v: number) => [`${v.toFixed(1)}%`]}
                />
                <Bar dataKey="pct" radius={[0, 3, 3, 0]}>
                  {sectors.map((_, i) => (
                    <Cell
                      key={i}
                      fill={i === 0 && _.pct > 70 ? "#FFB800" : "#00D4FF"}
                      fillOpacity={0.8}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Risk alerts */}
          <div className="lg:col-span-1 fold-card p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-3">Risk Alerts</div>
            <div className="space-y-2">
              {RISK_ALERTS.map((a, i) => (
                <AlertBadge key={i} level={a.level} text={a.text} />
              ))}
            </div>
          </div>
        </div>

      </main>
    </div>
  );
}
