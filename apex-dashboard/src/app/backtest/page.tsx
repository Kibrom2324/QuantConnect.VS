"use client";
import { useEffect, useRef, useState } from "react";
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, Cell,
} from "recharts";
import { TrendingUp, TrendingDown, ChevronDown, BarChart2 } from "lucide-react";

// ── Per-file mock data ────────────────────────────────────────────────────
const MOCK_CATALOG = [
  {
    filename:    "APEX_Backtest_2024Q4.json",
    label:       "APEX_Backtest_2024Q4.json",
    period:      "Dec 2024",
    sharpe_label:"1.42",
    total_return_pct: 18.4,
    sharpe_ratio:     1.42,
    max_drawdown_pct: -3.2,
    win_rate:         54.6,
    total_trades:     847,
    profit_factor:    1.38,
    equity: [
      { date: "Jan", equity: 100000 },
      { date: "Feb", equity: 102300 },
      { date: "Mar", equity: 104800 },
      { date: "Apr", equity: 103200 },
      { date: "May", equity: 107500 },
      { date: "Jun", equity: 106100 },
      { date: "Jul", equity: 109800 },
      { date: "Aug", equity: 108300 },
      { date: "Sep", equity: 112400 },
      { date: "Oct", equity: 114700 },
      { date: "Nov", equity: 113200 },
      { date: "Dec", equity: 118400 },
    ],
    monthly: [
      { month: "Jan", ret: 2.30 }, { month: "Feb", ret: 2.20 },
      { month: "Mar", ret: -1.52 }, { month: "Apr", ret: 4.17 },
      { month: "May", ret: -1.30 }, { month: "Jun", ret: 3.49 },
      { month: "Jul", ret: -1.37 }, { month: "Aug", ret: 3.80 },
      { month: "Sep", ret: 2.05 }, { month: "Oct", ret: -1.31 },
      { month: "Nov", ret: 4.59 }, { month: "Dec", ret: 1.42 },
    ],
    trades: [
      { id: 1, symbol: "NVDA", direction: "LONG",  entry: "2024-10-15", exit: "2024-10-22", pnl: 330 },
      { id: 2, symbol: "AAPL", direction: "SHORT", entry: "2024-11-03", exit: "2024-11-10", pnl: 105 },
      { id: 3, symbol: "MSFT", direction: "LONG",  entry: "2024-11-20", exit: "2024-11-28", pnl: 130 },
      { id: 4, symbol: "TSLA", direction: "SHORT", entry: "2024-12-02", exit: "2024-12-09", pnl: -130 },
      { id: 5, symbol: "NVDA", direction: "LONG",  entry: "2024-12-15", exit: "2024-12-22", pnl: 270 },
    ],
  },
  {
    filename:    "APEX_Backtest_2024Q3.json",
    label:       "APEX_Backtest_2024Q3.json",
    period:      "Sep 2024",
    sharpe_label:"1.21",
    total_return_pct: 11.2,
    sharpe_ratio:     1.21,
    max_drawdown_pct: -4.8,
    win_rate:         52.1,
    total_trades:     624,
    profit_factor:    1.22,
    equity: [
      { date: "Jan", equity: 100000 },
      { date: "Feb", equity: 101500 },
      { date: "Mar", equity: 103200 },
      { date: "Apr", equity: 102100 },
      { date: "May", equity: 104800 },
      { date: "Jun", equity: 103400 },
      { date: "Jul", equity: 106200 },
      { date: "Aug", equity: 105100 },
      { date: "Sep", equity: 111200 },
    ],
    monthly: [
      { month: "Jan", ret: 1.50 }, { month: "Feb", ret: 1.65 },
      { month: "Mar", ret: -1.05 }, { month: "Apr", ret: 2.60 },
      { month: "May", ret: -1.33 }, { month: "Jun", ret: 2.71 },
      { month: "Jul", ret: -1.05 }, { month: "Aug", ret: 2.96 },
      { month: "Sep", ret: 5.80 },
    ],
    trades: [
      { id: 1, symbol: "NVDA", direction: "LONG",  entry: "2024-07-10", exit: "2024-07-18", pnl: 220 },
      { id: 2, symbol: "MSFT", direction: "SHORT", entry: "2024-08-05", exit: "2024-08-12", pnl: -80 },
      { id: 3, symbol: "GOOGL", direction: "LONG", entry: "2024-09-01", exit: "2024-09-10", pnl: 190 },
    ],
  },
  {
    filename:    "APEX_Backtest_2024Q2.json",
    label:       "APEX_Backtest_2024Q2.json",
    period:      "Jun 2024",
    sharpe_label:"0.98",
    total_return_pct: 6.8,
    sharpe_ratio:     0.98,
    max_drawdown_pct: -6.1,
    win_rate:         49.3,
    total_trades:     512,
    profit_factor:    1.11,
    equity: [
      { date: "Jan", equity: 100000 },
      { date: "Feb", equity: 100800 },
      { date: "Mar", equity: 102100 },
      { date: "Apr", equity: 100400 },
      { date: "May", equity: 103500 },
      { date: "Jun", equity: 106800 },
    ],
    monthly: [
      { month: "Jan", ret: 0.80 }, { month: "Feb", ret: 1.29 },
      { month: "Mar", ret: -1.67 }, { month: "Apr", ret: 3.09 },
      { month: "May", ret: -0.94 }, { month: "Jun", ret: 3.14 },
    ],
    trades: [
      { id: 1, symbol: "AAPL", direction: "LONG",  entry: "2024-04-08", exit: "2024-04-15", pnl: 160 },
      { id: 2, symbol: "TSLA", direction: "SHORT", entry: "2024-05-10", exit: "2024-05-17", pnl: -200 },
      { id: 3, symbol: "AMZN", direction: "LONG",  entry: "2024-06-03", exit: "2024-06-12", pnl: 240 },
    ],
  },
];

type BacktestEntry = typeof MOCK_CATALOG[0];

function StatCard({
  label, value, sub, color,
}: { label: string; value: string; sub?: string; color?: string }) {
  const c = color ?? "#00D4FF";
  return (
    <div className="apex-card flex-1 min-w-[110px]" style={{ borderLeft: `2px solid ${c}60` }}>
      <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-1">{label}</div>
      <div className="font-heading text-xl font-bold" style={{ color: c, textShadow: `0 0 10px ${c}40` }}>
        {value}
      </div>
      {sub && <div className="text-[9px] font-mono text-[#4a6a8a] mt-0.5">{sub}</div>}
    </div>
  );
}

// ── Custom equity tooltip ────────────────────────────────────────────────
const EquityTooltip = ({ active, payload, label }: { active?: boolean; payload?: {value:number}[]; label?: string }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="apex-card !py-2 !px-3 text-[10px] font-mono">
      <div style={{ color: "#4a6a8a" }}>{label}</div>
      <div style={{ color: "#00D4FF" }}>${(payload[0].value as number).toLocaleString()}</div>
    </div>
  );
};

// ── Custom dropdown ──────────────────────────────────────────────────────
function FileDropdown({
  catalog, selected, onSelect,
}: { catalog: typeof MOCK_CATALOG; selected: BacktestEntry | null; onSelect: (e: BacktestEntry) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const sharpeColor = (s: string) =>
    parseFloat(s) >= 1.3 ? "#00FF88" : parseFloat(s) >= 1.0 ? "#FFB800" : "#FF2D55";

  return (
    <div ref={ref} className="relative" style={{ minWidth: 280 }}>
      {/* Trigger button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-between w-full px-3 py-2 rounded font-mono text-[11px] transition-all"
        style={{
          background: "rgba(0,212,255,0.06)",
          border:     `1px solid ${open ? "rgba(0,212,255,0.5)" : "rgba(0,212,255,0.22)"}`,
          color:      selected ? "#e2f0ff" : "#4a6a8a",
          boxShadow:  open ? "0 0 12px rgba(0,212,255,0.15)" : "none",
        }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <BarChart2 style={{ width: 13, height: 13, color: "#00D4FF", flexShrink: 0 }} />
          <span className="truncate">
            {selected ? selected.label : "— SELECT BACKTEST FILE —"}
          </span>
        </div>
        <ChevronDown
          style={{
            width: 13, height: 13, color: "#00D4FF", flexShrink: 0, marginLeft: 8,
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 0.2s ease",
          }}
        />
      </button>

      {/* Dropdown menu */}
      {open && (
        <div
          className="absolute left-0 top-full mt-1.5 w-full rounded overflow-hidden z-50"
          style={{
            background: "#0a1020",
            border:     "1px solid rgba(0,212,255,0.3)",
            boxShadow:  "0 8px 32px rgba(0,0,0,0.6), 0 0 20px rgba(0,212,255,0.08)",
          }}
        >
          <div
            className="px-3 py-2 text-[9px] font-mono uppercase tracking-widest"
            style={{ color: "#4a6a8a", borderBottom: "1px solid rgba(0,212,255,0.1)" }}
          >
            — SELECT BACKTEST FILE —
          </div>
          {catalog.map((entry, i) => {
            const isActive = selected?.filename === entry.filename;
            const sc = sharpeColor(entry.sharpe_label);
            return (
              <button
                key={entry.filename}
                onClick={() => { onSelect(entry); setOpen(false); }}
                className="w-full text-left px-3 py-2.5 flex items-center gap-3 transition-all"
                style={{
                  background:  isActive ? "rgba(0,212,255,0.1)" : "transparent",
                  borderBottom: i < catalog.length - 1 ? "1px solid rgba(0,212,255,0.07)" : "none",
                  cursor: "pointer",
                }}
                onMouseEnter={(e) => { if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = "rgba(0,212,255,0.06)"; }}
                onMouseLeave={(e) => { if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
              >
                <BarChart2 style={{ width: 14, height: 14, color: "#00D4FF", flexShrink: 0 }} />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[11px] truncate" style={{ color: isActive ? "#00D4FF" : "#c2d8ee" }}>
                    {entry.label}
                  </div>
                  <div className="font-mono text-[9px] mt-0.5 flex items-center gap-2" style={{ color: "#4a6a8a" }}>
                    <span>{entry.period}</span>
                    <span>·</span>
                    <span>Sharpe <span style={{ color: sc, fontWeight: 700 }}>{entry.sharpe_label}</span></span>
                    <span>·</span>
                    <span style={{ color: entry.total_return_pct >= 0 ? "#00FF88" : "#FF2D55" }}>
                      {entry.total_return_pct >= 0 ? "+" : ""}{entry.total_return_pct}%
                    </span>
                  </div>
                </div>
                {isActive && (
                  <span className="text-[9px] font-mono font-bold" style={{ color: "#00D4FF" }}>✓</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────
export default function BacktestPage() {
  const [selected, setSelected] = useState<BacktestEntry | null>(null);
  const [apiFiles, setApiFiles] = useState<string[]>([]);

  // Try to load real file list from API; if empty, fall back to mock catalog
  useEffect(() => {
    fetch("/api/backtests")
      .then((r) => r.json())
      .then((data) => { setApiFiles(data.files ?? []); })
      .catch(() => {});
  }, []);

  // Use API files if present, otherwise mock catalog
  const effectiveCatalog = apiFiles.length > 0
    ? MOCK_CATALOG.filter((c) => apiFiles.includes(c.filename))
    : MOCK_CATALOG;

  const result = selected;

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1
            className="font-heading text-2xl font-bold uppercase tracking-widest"
            style={{ color: "#00D4FF", textShadow: "0 0 20px rgba(0,212,255,0.5)" }}
          >
            Backtest Results
          </h1>
          <p className="text-[10px] font-mono text-[#4a6a8a] mt-0.5">Historical strategy performance analysis</p>
        </div>
        <FileDropdown catalog={effectiveCatalog} selected={selected} onSelect={setSelected} />
      </div>

      {/* ── Empty state ── */}
      {!result && (
        <div className="apex-card flex flex-col items-center justify-center py-20 gap-4">
          <div style={{ fontSize: 48, filter: "drop-shadow(0 0 12px rgba(0,212,255,0.4))" }}>📊</div>
          <div className="font-mono text-[11px] uppercase tracking-widest text-[#4a6a8a]">
            Select a backtest file above to view results
          </div>
          <div className="flex items-center gap-2">
            <span className="demo-badge">DEMO</span>
            <span className="text-[9px] font-mono text-[#4a6a8a]">3 mock files available</span>
          </div>
        </div>
      )}

      {/* ── Results ── */}
      {result && (
        <div className="space-y-4 animate-slide-up">
          {/* Stats row */}
          <div className="flex gap-3 flex-wrap">
            <StatCard
              label="Total Return"
              value={`${result.total_return_pct > 0 ? "+" : ""}${result.total_return_pct.toFixed(1)}%`}
              color={result.total_return_pct >= 0 ? "#00FF88" : "#FF2D55"}
              sub="period return"
            />
            <StatCard
              label="Sharpe Ratio"
              value={result.sharpe_ratio.toFixed(2)}
              color={result.sharpe_ratio >= 1.2 ? "#00FF88" : result.sharpe_ratio >= 0.8 ? "#FFB800" : "#FF2D55"}
              sub="risk-adjusted"
            />
            <StatCard
              label="Max Drawdown"
              value={`${result.max_drawdown_pct.toFixed(1)}%`}
              color="#FF2D55"
              sub="peak-to-trough"
            />
            <StatCard
              label="Win Rate"
              value={`${result.win_rate.toFixed(1)}%`}
              color={result.win_rate >= 50 ? "#00FF88" : "#FFB800"}
              sub={`${result.total_trades} trades`}
            />
            <StatCard
              label="Profit Factor"
              value={result.profit_factor.toFixed(2)}
              color={result.profit_factor >= 1.3 ? "#00FF88" : result.profit_factor >= 1 ? "#FFB800" : "#FF2D55"}
              sub="gross P / gross L"
            />
          </div>

          {/* Equity curve */}
          <div className="apex-card">
            <div className="text-[10px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-3">
              Equity Curve — $100,000 Initial
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={result.equity} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%"   stopColor="#00D4FF" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#00D4FF" stopOpacity={0.01} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.06)" />
                <XAxis dataKey="date" tick={{ fill: "#4a6a8a", fontSize: 9 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: "#4a6a8a", fontSize: 9 }}
                  axisLine={false} tickLine={false}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                  width={38}
                />
                <Tooltip content={(p) => <EquityTooltip {...(p as Parameters<typeof EquityTooltip>[0])} />} />
                <ReferenceLine y={100000} stroke="rgba(255,255,255,0.1)" strokeDasharray="4 4" />
                <Area type="monotone" dataKey="equity" stroke="#00D4FF" strokeWidth={1.5} fill="url(#eqGrad)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Monthly returns */}
          <div className="apex-card">
            <div className="text-[10px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-3">
              Monthly Returns
            </div>
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={result.monthly} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.06)" vertical={false} />
                <XAxis dataKey="month" tick={{ fill: "#4a6a8a", fontSize: 9 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: "#4a6a8a", fontSize: 9 }}
                  axisLine={false} tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                  width={30}
                />
                <Tooltip
                  formatter={(v: number) => [`${v.toFixed(2)}%`, "Return"]}
                  contentStyle={{ background: "#050810", border: "1px solid rgba(0,212,255,0.25)", fontSize: 10, color: "#e2f0ff" }}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                <Bar dataKey="ret" maxBarSize={24} radius={[2, 2, 0, 0]}>
                  {result.monthly.map((m, i) => (
                    <Cell key={i} fill={m.ret >= 0 ? "#00FF88" : "#FF2D55"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Trade log */}
          <div className="apex-card p-0 overflow-x-auto">
            <div className="px-4 py-3 border-b" style={{ borderColor: "rgba(0,212,255,0.1)" }}>
              <span className="text-[10px] font-mono uppercase tracking-widest text-[#4a6a8a]">Recent Trades</span>
            </div>
            <table className="apex-table w-full">
              <thead>
                <tr>
                  <th>#</th>
                  <th>SYMBOL</th>
                  <th>DIRECTION</th>
                  <th>ENTRY</th>
                  <th>EXIT</th>
                  <th>PNL</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((t) => {
                  const isLong = t.direction === "LONG";
                  const pnlPos = t.pnl >= 0;
                  return (
                    <tr key={t.id} style={{ borderLeft: `3px solid ${pnlPos ? "#00FF88" : "#FF2D55"}` }}>
                      <td className="font-mono text-[10px] text-[#4a6a8a]">{t.id}</td>
                      <td><span className="font-mono font-bold text-white">{t.symbol}</span></td>
                      <td>
                        <span
                          className="inline-flex items-center gap-1 text-[10px] font-mono font-bold px-2 py-0.5 rounded"
                          style={{
                            color:      isLong ? "#00FF88" : "#FF2D55",
                            background: isLong ? "rgba(0,255,136,0.12)" : "rgba(255,45,85,0.12)",
                            border:     `1px solid ${isLong ? "rgba(0,255,136,0.3)" : "rgba(255,45,85,0.3)"}`,
                          }}
                        >
                          {isLong
                            ? <TrendingUp  style={{ width: 10 }} />
                            : <TrendingDown style={{ width: 10 }} />}
                          {t.direction}
                        </span>
                      </td>
                      <td className="font-mono text-[10px] text-[#4a6a8a]">{t.entry}</td>
                      <td className="font-mono text-[10px] text-[#4a6a8a]">{t.exit}</td>
                      <td>
                        <span
                          className="font-mono text-sm font-bold tabular-nums"
                          style={{
                            color:      pnlPos ? "#00FF88" : "#FF2D55",
                            textShadow: pnlPos ? "0 0 6px rgba(0,255,136,0.4)" : "0 0 6px rgba(255,45,85,0.4)",
                          }}
                        >
                          {pnlPos ? "+" : ""}${t.pnl}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
