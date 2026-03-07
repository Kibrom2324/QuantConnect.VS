"use client";

import { useEffect, useState, useCallback } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import {
  RefreshCw,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Wallet,
  BarChart3,
  Activity,
  LayoutDashboard,
  LineChart,
  ShoppingCart,
  BrainCircuit,
  Settings,
} from "lucide-react";

// ─────────────────────────────────────────────────────────────────────────────
// SAFE PRIMITIVES — never throws on null / undefined / NaN
// ─────────────────────────────────────────────────────────────────────────────

const safeNum = (v: unknown): number => {
  const n = typeof v === "string" ? parseFloat(v) : Number(v);
  return Number.isFinite(n) ? n : 0;
};

const safeArr = <T,>(v: unknown): T[] =>
  Array.isArray(v) ? (v as T[]) : [];

const safeStr = (v: unknown, fallback = ""): string =>
  v != null ? String(v) : fallback;

const fmtUSD = (v: unknown): string =>
  safeNum(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

const fmtDelta = (v: unknown): string => {
  const n = safeNum(v);
  return `${n >= 0 ? "+" : ""}$${Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

const fmtPct = (v: unknown): string => {
  const n = safeNum(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
};

// ─────────────────────────────────────────────────────────────────────────────
// MOCK FALLBACK DATA
// ─────────────────────────────────────────────────────────────────────────────

const MOCK_ACCOUNT = {
  portfolio_value: 127450,
  cash: 45230.1,
  buying_power: 90460.2,
  day_pnl: 1243.5,
  day_pnl_pct: 0.98,
  equity: 127450,
  last_equity: 126206.5,
  win_rate: 54.2,
  sharpe: 1.35,
  max_drawdown: -3.2,
  avg_win: 284,
  avg_loss: -156,
  profit_factor: 1.82,
  total_trades: 47,
  status: "ACTIVE",
  account_number: "PA3XXXXXXX",
  _mock: true,
};

const MOCK_POSITIONS = [
  { symbol: "NVDA", side: "long",  qty: 10, avg_entry_price: 485.2,  current_price: 492.8,  unrealized_pl: 76.0,   unrealized_plpc: 0.0157  },
  { symbol: "AAPL", side: "long",  qty: 15, avg_entry_price: 182.4,  current_price: 179.9,  unrealized_pl: -37.5,  unrealized_plpc: -0.0137 },
  { symbol: "MSFT", side: "long",  qty: 8,  avg_entry_price: 415.6,  current_price: 419.3,  unrealized_pl: 29.6,   unrealized_plpc: 0.0089  },
  { symbol: "AMD",  side: "long",  qty: 12, avg_entry_price: 172.1,  current_price: 176.4,  unrealized_pl: 51.6,   unrealized_plpc: 0.025   },
];

interface OrderRow {
  id?: unknown; symbol?: unknown; side?: unknown; type?: unknown;
  qty?: unknown; filled_qty?: unknown; filled_avg_price?: unknown;
  limit_price?: unknown; status?: unknown; created_at?: unknown;
}

const MOCK_ORDERS: OrderRow[] = [
  { id: "1", symbol: "NVDA",  side: "buy",  type: "market", qty: 10, filled_avg_price: 485.2,  limit_price: null, filled_qty: 10, status: "filled",   created_at: "2026-03-01T14:32:00Z" },
  { id: "2", symbol: "AAPL",  side: "sell", type: "market", qty: 15, filled_avg_price: 182.4,  limit_price: null, filled_qty: 15, status: "filled",   created_at: "2026-03-01T13:10:00Z" },
  { id: "3", symbol: "MSFT",  side: "buy",  type: "market", qty: 8,  filled_avg_price: 415.6,  limit_price: null, filled_qty: 8,  status: "filled",   created_at: "2026-02-28T15:05:00Z" },
  { id: "4", symbol: "TSLA",  side: "buy",  type: "limit",  qty: 5,  filled_avg_price: null,   limit_price: 190,  filled_qty: 0,  status: "canceled", created_at: "2026-02-28T10:22:00Z" },
  { id: "5", symbol: "AMD",   side: "buy",  type: "market", qty: 20, filled_avg_price: 177.4,  limit_price: null, filled_qty: 20, status: "filled",   created_at: "2026-02-27T14:00:00Z" },
];

function genMockPnL(): { date: string; value: number; pnl: number }[] {
  let v = 120_000;
  const now = new Date();
  const pts: { date: string; value: number; pnl: number }[] = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    if (d.getDay() === 0 || d.getDay() === 6) continue;
    v += (Math.random() - 0.45) * v * 0.009;
    pts.push({
      date: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      value: parseFloat(v.toFixed(2)),
      pnl: parseFloat((v - 120_000).toFixed(2)),
    });
  }
  return pts;
}

// ─────────────────────────────────────────────────────────────────────────────
// INLINE NAV SIDEBAR (self-contained, no external import)
// ─────────────────────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { label: "Dashboard",  href: "/",         Icon: LayoutDashboard },
  { label: "Trading",    href: "/trading",  Icon: LineChart        },
  { label: "Orders",     href: "/orders",   Icon: ShoppingCart     },
  { label: "Models",     href: "/models",   Icon: BrainCircuit     },
  { label: "Wallet",     href: "/wallet",   Icon: Wallet           },
  { label: "Settings",   href: "/settings", Icon: Settings         },
];

function InlineSidebar() {
  return (
    <aside
      className="w-14 shrink-0 flex flex-col items-center py-4 gap-2 border-r"
      style={{ background: "rgba(5,8,16,0.95)", borderColor: "#1a2a3a" }}
    >
      <div
        className="w-8 h-8 rounded-lg flex items-center justify-center mb-4 text-[10px] font-black"
        style={{ background: "rgba(0,212,255,0.15)", color: "#00D4FF", border: "1px solid rgba(0,212,255,0.3)" }}
      >
        A
      </div>
      {NAV_ITEMS.map(({ label, href, Icon }) => {
        const active = typeof window !== "undefined" && window.location.pathname === href;
        return (
          <a
            key={href}
            href={href}
            title={label}
            className="w-9 h-9 rounded-lg flex items-center justify-center transition-all"
            style={{
              background: active ? "rgba(0,212,255,0.15)" : "transparent",
              border: `1px solid ${active ? "rgba(0,212,255,0.35)" : "transparent"}`,
              color: active ? "#00D4FF" : "#4a6a8a",
            }}
          >
            <Icon className="w-4 h-4" />
          </a>
        );
      })}
    </aside>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// STAT CARD
// ─────────────────────────────────────────────────────────────────────────────

function StatCard({
  Icon,
  label,
  value,
  sub,
  accent = "#00D4FF",
}: {
  Icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div
      className="rounded-xl p-5 flex flex-col gap-2"
      style={{
        background: "rgba(7,12,24,0.85)",
        border: `1px solid ${accent}22`,
      }}
    >
      <div className="flex items-center gap-2">
        <div
          className="w-7 h-7 rounded flex items-center justify-center shrink-0"
          style={{ background: `${accent}18`, border: `1px solid ${accent}33` }}
        >
          <Icon style={{ width: 13, height: 13, color: accent }} />
        </div>
        <span className="font-mono text-[10px] uppercase tracking-widest text-gray-500">
          {label}
        </span>
      </div>
      <div className="font-mono text-[22px] font-bold leading-tight" style={{ color: accent }}>
        {value}
      </div>
      {sub && (
        <div className="font-mono text-[11px] text-gray-500">{sub}</div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LOADING SKELETON
// ─────────────────────────────────────────────────────────────────────────────

function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`rounded animate-pulse ${className}`}
      style={{ background: "rgba(255,255,255,0.05)" }}
    />
  );
}

function LoadingSkeleton() {
  return (
    <div className="flex-1 p-6 space-y-6">
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}
      </div>
      <Skeleton className="h-52" />
      <Skeleton className="h-64" />
      <Skeleton className="h-48" />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CUSTOM CHART TOOLTIP
// ─────────────────────────────────────────────────────────────────────────────

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { payload: { value: number; pnl: number } }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  const pnl = safeNum(d?.pnl);
  return (
    <div
      className="rounded-lg px-3 py-2.5 font-mono text-xs border"
      style={{
        background: "#070c18",
        borderColor: pnl >= 0 ? "rgba(0,255,136,0.3)" : "rgba(255,45,85,0.3)",
      }}
    >
      <div className="text-gray-400 mb-1">{label}</div>
      <div className="text-white font-bold">${fmtUSD(d?.value)}</div>
      <div style={{ color: pnl >= 0 ? "#00FF88" : "#FF2D55" }}>
        {fmtDelta(pnl)} all-time
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN PAGE
// ─────────────────────────────────────────────────────────────────────────────

export default function WalletPage() {
  // mounted guard — prevents recharts SSR hydration crash
  const [mounted, setMounted] = useState(false);
  const [loaded,  setLoaded]  = useState(false);

  const [account,   setAccount]   = useState<typeof MOCK_ACCOUNT>(MOCK_ACCOUNT);
  const [positions, setPositions] = useState<typeof MOCK_POSITIONS>(MOCK_POSITIONS);
  const [orders,    setOrders]    = useState<OrderRow[]>(MOCK_ORDERS);
  const [pnlData,   setPnlData]   = useState<ReturnType<typeof genMockPnL>>([]);
  const [isMock,    setIsMock]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    setMounted(true);
    setPnlData(genMockPnL());
  }, []);

  const fetchAll = useCallback(async () => {
    setRefreshing(true);

    // ── account ──────────────────────────────────────────────────────────────
    try {
      const res = await fetch("/api/account", { cache: "no-store" });
      if (res.ok) {
        const json = await res.json().catch(() => ({}));
        const acc = json?.account ?? json ?? {};
        if (acc && Object.keys(acc).length > 0) {
          setAccount({ ...MOCK_ACCOUNT, ...acc });
          setIsMock(!!(acc._mock || acc.is_mock));
        }
      }
    } catch {
      /* keep mock */
    }

    // ── positions ─────────────────────────────────────────────────────────────
    try {
      const res = await fetch("/api/positions", { cache: "no-store" });
      if (res.ok) {
        const json = await res.json().catch(() => ({}));
        const arr = safeArr(json?.positions ?? json);
        if (arr.length > 0) setPositions(arr as typeof MOCK_POSITIONS);
      }
    } catch {
      /* keep mock */
    }

    // ── orders ────────────────────────────────────────────────────────────────
    try {
      const res = await fetch("/api/orders", { cache: "no-store" });
      if (res.ok) {
        const json = await res.json().catch(() => ({}));
        const arr = safeArr(json?.orders ?? json);
        if (arr.length > 0) setOrders(arr as typeof MOCK_ORDERS);
      }
    } catch {
      /* keep mock */
    }

    // ── pnl history ───────────────────────────────────────────────────────────
    try {
      const res = await fetch("/api/pnl", { cache: "no-store" });
      if (res.ok) {
        const json = await res.json().catch(() => ({}));
        const arr = safeArr<{ date: string; value: number; pnl: number }>(
          json?.history ?? json
        );
        if (arr.length > 1) setPnlData(arr);
      }
    } catch {
      /* keep mock pnl */
    }

    setRefreshing(false);
    setLoaded(true);
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 30_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  // ── derived values (all via safeNum — never throws) ────────────────────────

  const portfolioVal = safeNum(account?.portfolio_value);
  const cashVal      = safeNum(account?.cash);
  const buyingPower  = safeNum(account?.buying_power);
  const dayPnL       = safeNum(account?.day_pnl);
  const dayPnLPct    = safeNum(account?.day_pnl_pct);
  const winRate      = safeNum(account?.win_rate ?? 54.2);
  const sharpe       = safeNum(account?.sharpe ?? 1.35);
  const maxDD        = safeNum(account?.max_drawdown ?? -3.2);
  const avgWin       = safeNum(account?.avg_win ?? 284);
  const avgLoss      = safeNum(account?.avg_loss ?? -156);
  const profitFactor = safeNum(account?.profit_factor ?? 1.82);
  const totalTrades  = safeNum(account?.total_trades ?? 47);

  const pnlUp = pnlData.length < 2 ||
    safeNum(pnlData[pnlData.length - 1]?.value) >=
    safeNum(pnlData[0]?.value);
  const baseValue = safeNum(pnlData[0]?.value ?? 120_000);

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "#050810", color: "#e2f0ff" }}>
      {/* Sidebar — inline, no external import */}
      {mounted && <InlineSidebar />}

      {/* Main scroll area */}
      <main className="flex-1 overflow-y-auto">
        {/* Show skeleton until first fetch completes */}
        {!loaded ? (
          <LoadingSkeleton />
        ) : (
          <div className="p-6 space-y-6 max-w-[1400px]">

            {/* ── Header ──────────────────────────────────────────────────── */}
            <div className="flex items-center justify-between">
              <div>
                <h1
                  className="text-lg font-bold tracking-tight flex items-center gap-2"
                  style={{ fontFamily: "'JetBrains Mono', monospace" }}
                >
                  <Wallet className="w-5 h-5" style={{ color: "#00D4FF" }} />
                  WALLET
                </h1>
                <p className="text-[11px] mt-0.5" style={{ color: "#4a6a8a", fontFamily: "'JetBrains Mono', monospace" }}>
                  {isMock
                    ? "⚠ Mock data — add Alpaca keys to enable live account"
                    : `Paper Trading · ${safeStr(account?.account_number, "PA3XXXXXXX")}`}
                </p>
              </div>
              <button
                onClick={fetchAll}
                disabled={refreshing}
                className="p-2 rounded-lg border transition-all"
                style={{
                  background: "rgba(0,212,255,0.05)",
                  borderColor: "rgba(0,212,255,0.2)",
                  color: refreshing ? "#00D4FF" : "#4a6a8a",
                }}
              >
                <RefreshCw
                  className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`}
                />
              </button>
            </div>

            {/* ── 4 Stat Cards ────────────────────────────────────────────── */}
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
              <StatCard
                Icon={BarChart3}
                label="Portfolio Value"
                value={`$${fmtUSD(portfolioVal)}`}
                sub={`${fmtDelta(dayPnL)} today`}
                accent={dayPnL >= 0 ? "#00FF88" : "#FF2D55"}
              />
              <StatCard
                Icon={DollarSign}
                label="Cash Balance"
                value={`$${fmtUSD(cashVal)}`}
                sub="Available to deploy"
                accent="#00D4FF"
              />
              <StatCard
                Icon={Activity}
                label="Buying Power"
                value={`$${fmtUSD(buyingPower)}`}
                sub={`2× = $${fmtUSD(buyingPower * 2)}`}
                accent="#9945FF"
              />
              <StatCard
                Icon={dayPnL >= 0 ? TrendingUp : TrendingDown}
                label="Day P&L"
                value={fmtDelta(dayPnL)}
                sub={fmtPct(dayPnLPct)}
                accent={dayPnL >= 0 ? "#00FF88" : "#FF2D55"}
              />
            </div>

            {/* ── P&L History Chart (recharts, only rendered after mount) ── */}
            <div
              className="rounded-xl p-5"
              style={{ background: "rgba(7,12,24,0.85)", border: "1px solid #1a2a3a" }}
            >
              <p
                className="text-[10px] uppercase tracking-widest mb-4"
                style={{ color: "#4a6a8a", fontFamily: "'JetBrains Mono', monospace" }}
              >
                P&L History (30 days)
              </p>
              {mounted && pnlData.length > 1 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart
                    data={pnlData}
                    margin={{ top: 4, right: 16, left: 8, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient id="walletGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop
                          offset="5%"
                          stopColor={pnlUp ? "#00FF88" : "#FF2D55"}
                          stopOpacity={0.25}
                        />
                        <stop
                          offset="95%"
                          stopColor={pnlUp ? "#00FF88" : "#FF2D55"}
                          stopOpacity={0}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#0d1f2d" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: "#4a6a8a", fontSize: 9, fontFamily: "JetBrains Mono" }}
                      tickLine={false}
                      axisLine={{ stroke: "#0d1f2d" }}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      tickFormatter={(v: number) =>
                        `$${(safeNum(v) / 1000).toFixed(0)}k`
                      }
                      tick={{ fill: "#4a6a8a", fontSize: 9, fontFamily: "JetBrains Mono" }}
                      tickLine={false}
                      axisLine={false}
                      width={44}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <ReferenceLine
                      y={baseValue}
                      stroke="#4a6a8a"
                      strokeDasharray="4 4"
                      strokeWidth={1}
                    />
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke={pnlUp ? "#00FF88" : "#FF2D55"}
                      strokeWidth={2}
                      fill="url(#walletGrad)"
                      dot={false}
                      activeDot={{ r: 4, fill: pnlUp ? "#00FF88" : "#FF2D55", strokeWidth: 0 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-[200px] flex items-center justify-center" style={{ color: "#4a6a8a", fontSize: 12 }}>
                  Loading chart…
                </div>
              )}
            </div>

            {/* ── Two-column row: positions + performance ──────────────────── */}
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">

              {/* Open Positions (2/3) */}
              <div
                className="xl:col-span-2 rounded-xl overflow-hidden"
                style={{ background: "rgba(7,12,24,0.85)", border: "1px solid #1a2a3a" }}
              >
                <div
                  className="flex items-center justify-between px-5 py-3.5"
                  style={{ borderBottom: "1px solid #1a2a3a" }}
                >
                  <span
                    className="text-[10px] uppercase tracking-widest"
                    style={{ color: "#4a6a8a", fontFamily: "'JetBrains Mono', monospace" }}
                  >
                    Open Positions
                  </span>
                  <span
                    className="font-mono text-[10px] px-2 py-0.5 rounded-full"
                    style={{
                      background: "rgba(0,212,255,0.08)",
                      color: "#00D4FF",
                      border: "1px solid rgba(0,212,255,0.2)",
                    }}
                  >
                    {positions.length}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #0d1f2d" }}>
                        {["SYMBOL", "SIDE", "QTY", "ENTRY", "CURRENT", "P&L", "P&L %"].map((h) => (
                          <th
                            key={h}
                            className="px-4 py-2.5 text-left text-[10px] font-normal uppercase tracking-wider"
                            style={{ color: "#4a6a8a" }}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {positions.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="px-4 py-10 text-center" style={{ color: "#4a6a8a" }}>
                            No open positions
                          </td>
                        </tr>
                      ) : (
                        positions.map((pos, idx) => {
                          const sym     = safeStr(pos?.symbol, "—");
                          const side    = safeStr(pos?.side, "long");
                          const qty     = safeNum(pos?.qty);
                          const entry   = safeNum(pos?.avg_entry_price);
                          const cur     = safeNum(pos?.current_price ?? pos?.avg_entry_price);
                          const upl     = safeNum(pos?.unrealized_pl);
                          const uplPct  = safeNum(pos?.unrealized_plpc) * 100;
                          const isLong  = side !== "short";
                          const upColor = upl >= 0 ? "#00FF88" : "#FF2D55";
                          return (
                            <tr
                              key={sym + idx}
                              style={{ borderBottom: "1px solid #0a1420" }}
                            >
                              <td className="px-4 py-3 font-bold" style={{ color: "#e2f0ff" }}>{sym}</td>
                              <td className="px-4 py-3">
                                <span
                                  className="px-2 py-0.5 rounded text-[10px] font-bold uppercase"
                                  style={{
                                    background: isLong ? "rgba(0,255,136,0.1)" : "rgba(255,45,85,0.1)",
                                    color: isLong ? "#00FF88" : "#FF2D55",
                                  }}
                                >
                                  {side}
                                </span>
                              </td>
                              <td className="px-4 py-3" style={{ color: "#a0b4c8" }}>{qty}</td>
                              <td className="px-4 py-3" style={{ color: "#a0b4c8" }}>${fmtUSD(entry)}</td>
                              <td className="px-4 py-3" style={{ color: "#e2f0ff" }}>${fmtUSD(cur)}</td>
                              <td className="px-4 py-3 font-bold" style={{ color: upColor }}>{fmtDelta(upl)}</td>
                              <td className="px-4 py-3 font-bold" style={{ color: upColor }}>{fmtPct(uplPct)}</td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Performance Stats (1/3) */}
              <div
                className="rounded-xl p-5"
                style={{ background: "rgba(7,12,24,0.85)", border: "1px solid #1a2a3a" }}
              >
                <p
                  className="text-[10px] uppercase tracking-widest mb-4"
                  style={{ color: "#4a6a8a", fontFamily: "'JetBrains Mono', monospace" }}
                >
                  Performance
                </p>
                <div className="space-y-0" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                  {[
                    { label: "Win Rate",      value: fmtPct(winRate),      accent: "#00FF88" },
                    { label: "Sharpe Ratio",  value: sharpe.toFixed(2),    accent: "#00D4FF" },
                    { label: "Max Drawdown",  value: fmtPct(maxDD),        accent: "#FF2D55" },
                    { label: "Avg Win",       value: `+$${safeNum(avgWin).toFixed(0)}`,  accent: "#00FF88" },
                    { label: "Avg Loss",      value: `-$${Math.abs(safeNum(avgLoss)).toFixed(0)}`, accent: "#FF2D55" },
                    { label: "Profit Factor", value: profitFactor.toFixed(2), accent: "#9945FF" },
                    { label: "Total Trades",  value: String(Math.round(totalTrades)), accent: "#a0b4c8" },
                  ].map(({ label, value, accent }) => (
                    <div
                      key={label}
                      className="flex items-center justify-between py-2"
                      style={{ borderBottom: "1px solid #0a1420" }}
                    >
                      <span className="text-[11px]" style={{ color: "#4a6a8a" }}>{label}</span>
                      <span className="text-[11px] font-bold" style={{ color: accent }}>{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* ── Transaction History ──────────────────────────────────────── */}
            <div
              className="rounded-xl overflow-hidden"
              style={{ background: "rgba(7,12,24,0.85)", border: "1px solid #1a2a3a" }}
            >
              <div
                className="flex items-center justify-between px-5 py-3.5"
                style={{ borderBottom: "1px solid #1a2a3a" }}
              >
                <span
                  className="text-[10px] uppercase tracking-widest"
                  style={{ color: "#4a6a8a", fontFamily: "'JetBrains Mono', monospace" }}
                >
                  Transaction History
                </span>
                <span
                  className="font-mono text-[10px] px-2 py-0.5 rounded-full"
                  style={{
                    background: "rgba(0,212,255,0.08)",
                    color: "#00D4FF",
                    border: "1px solid rgba(0,212,255,0.2)",
                  }}
                >
                  {orders.length}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #0d1f2d" }}>
                      {["DATE", "SYMBOL", "SIDE", "TYPE", "QTY", "PRICE", "TOTAL", "STATUS"].map((h) => (
                        <th
                          key={h}
                          className="px-4 py-2.5 text-left text-[10px] font-normal uppercase tracking-wider"
                          style={{ color: "#4a6a8a" }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {orders.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-4 py-10 text-center" style={{ color: "#4a6a8a" }}>
                          No orders found
                        </td>
                      </tr>
                    ) : (
                      orders.slice(0, 25).map((ord, idx) => {
                        const sym       = safeStr(ord?.symbol, "—");
                        const side      = safeStr(ord?.side, "buy");
                        const ordType   = safeStr(ord?.type, "market");
                        const qty       = safeNum(ord?.qty);
                        const filledQty = safeNum(ord?.filled_qty ?? ord?.qty);
                        const price     = safeNum(ord?.filled_avg_price ?? ord?.limit_price ?? 0);
                        const total     = price > 0 ? price * filledQty : 0;
                        const status    = safeStr(ord?.status, "unknown");
                        let dateStr = "—";
                        try {
                          if (ord?.created_at) {
                            dateStr = new Date(String(ord.created_at)).toLocaleDateString("en-US", {
                              month: "short", day: "numeric",
                            });
                          }
                        } catch { /* ignore bad date */ }
                        const isBuy      = side === "buy";
                        const isFilled   = status === "filled";
                        const isCanceled = status === "canceled";
                        const statusColor = isFilled ? "#00FF88" : isCanceled ? "#4a6a8a" : "#FFB800";
                        return (
                          <tr key={safeStr(ord?.id, String(idx))} style={{ borderBottom: "1px solid #0a1420" }}>
                            <td className="px-4 py-3" style={{ color: "#4a6a8a" }}>{dateStr}</td>
                            <td className="px-4 py-3 font-bold" style={{ color: "#e2f0ff" }}>{sym}</td>
                            <td className="px-4 py-3">
                              <span
                                className="px-2 py-0.5 rounded text-[10px] font-bold uppercase"
                                style={{
                                  background: isBuy ? "rgba(0,255,136,0.1)" : "rgba(255,45,85,0.1)",
                                  color: isBuy ? "#00FF88" : "#FF2D55",
                                }}
                              >
                                {side}
                              </span>
                            </td>
                            <td className="px-4 py-3 uppercase" style={{ color: "#4a6a8a" }}>{ordType}</td>
                            <td className="px-4 py-3" style={{ color: "#a0b4c8" }}>{qty}</td>
                            <td className="px-4 py-3" style={{ color: "#a0b4c8" }}>
                              {price > 0 ? `$${fmtUSD(price)}` : "—"}
                            </td>
                            <td className="px-4 py-3" style={{ color: "#e2f0ff" }}>
                              {total > 0
                                ? `$${total.toLocaleString("en-US", { maximumFractionDigits: 0 })}`
                                : "—"}
                            </td>
                            <td className="px-4 py-3 font-bold uppercase text-[10px]" style={{ color: statusColor }}>
                              {status}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* ── Account Mode Strip ───────────────────────────────────────── */}
            <div
              className="rounded-xl px-5 py-4 flex items-center gap-6 flex-wrap"
              style={{
                background: "rgba(7,12,24,0.85)",
                border: "1px solid #1a2a3a",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-widest" style={{ color: "#4a6a8a" }}>Mode</span>
                <span
                  className="px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase"
                  style={{
                    background: "rgba(0,212,255,0.1)",
                    color: "#00D4FF",
                    border: "1px solid rgba(0,212,255,0.25)",
                  }}
                >
                  PAPER
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-widest" style={{ color: "#4a6a8a" }}>Status</span>
                <span
                  className="text-[11px] font-bold"
                  style={{
                    color: safeStr(account?.status) === "ACTIVE" ? "#00FF88" : "#FF2D55",
                  }}
                >
                  {safeStr(account?.status, "ACTIVE")}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-widest" style={{ color: "#4a6a8a" }}>Account</span>
                <span className="text-[11px]" style={{ color: "#e2f0ff" }}>
                  {safeStr(account?.account_number, "PA3XXXXXXX")}
                </span>
              </div>
              {isMock && (
                <span
                  className="ml-auto text-[10px] px-2.5 py-0.5 rounded-full font-bold"
                  style={{
                    background: "rgba(255,184,0,0.1)",
                    color: "#FFB800",
                    border: "1px solid rgba(255,184,0,0.25)",
                  }}
                >
                  ⚠ MOCK DATA
                </span>
              )}
            </div>

          </div>
        )}
      </main>
    </div>
  );
}
