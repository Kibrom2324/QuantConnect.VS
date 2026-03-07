"use client";
import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import dynamic from "next/dynamic";
import ServiceStatusGrid from "@/components/ServiceStatusGrid";
import KillSwitch from "@/components/KillSwitch";
import PnLTicker from "@/components/PnLTicker";
import PositionsTable from "@/components/PositionsTable";
import RecentSignals from "@/components/RecentSignals";
import EnsembleWeights from "@/components/EnsembleWeights";
import AgentLog from "@/components/AgentLog";

// Dynamic import to avoid SSR crash (lightweight-charts requires window)
const CandlestickChart = dynamic(() => import("@/components/CandlestickChart"), { ssr: false });
const ChartModal       = dynamic(() => import("@/components/ChartModal"), { ssr: false });

const REFRESH_INTERVAL = 5_000;

export default function DashboardPage() {
  const [refreshKey,    setRefreshKey]    = useState(0);
  const [lastRefresh,   setLastRefresh]   = useState<Date>(new Date());
  const [auto,          setAuto]          = useState(true);
  const [chartSymbol,   setChartSymbol]   = useState("NVDA");
  const [modalSymbol,   setModalSymbol]   = useState<string | null>(null);

  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => {
      setRefreshKey((k) => k + 1);
      setLastRefresh(new Date());
    }, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [auto]);

  const manualRefresh = () => {
    setRefreshKey((k) => k + 1);
    setLastRefresh(new Date());
  };

  return (
    <div className="space-y-5">

      {/* ── Page Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1
            className="font-heading text-2xl font-bold uppercase tracking-widest"
            style={{ color: "#00D4FF", textShadow: "0 0 20px rgba(0,212,255,0.5)" }}
          >
            System Overview
          </h1>
          <p className="text-[10px] font-mono text-apex-subtext mt-0.5 tracking-wider">
            LAST UPDATE:{" "}
            <span style={{ color: "#00D4FF" }}>{lastRefresh.toLocaleTimeString()}</span>
          </p>
        </div>

        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-[10px] font-mono text-apex-subtext cursor-pointer select-none">
            <span
              className="w-2 h-2 rounded-full inline-block"
              style={{
                background: auto ? "#00FF88" : "#4a6a8a",
                boxShadow: auto ? "0 0 6px rgba(0,255,136,0.8)" : "none",
                animation: auto ? "blink-dot 1s step-end infinite" : undefined,
              }}
            />
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="sr-only" />
            AUTO 5s
          </label>
          <button
            onClick={manualRefresh}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded font-mono text-[10px] uppercase tracking-wider transition-all"
            style={{
              background: "rgba(0,212,255,0.08)",
              border: "1px solid rgba(0,212,255,0.25)",
              color: "#00D4FF",
            }}
          >
            <RefreshCw style={{ width: 11, height: 11 }} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Row 1: PnL Banner (full width) ── */}
      <PnLTicker refreshKey={refreshKey} />

      {/* ── Row 2: Chart (left 2/3) + Agent Log (right 1/3) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 fold-card overflow-hidden">
          {/* Symbol selector tabs */}
          <div className="flex items-center gap-1 px-3 pt-3">
            {["NVDA", "AAPL", "MSFT", "TSLA", "SPY"].map(sym => (
              <button
                key={sym}
                onClick={() => setChartSymbol(sym)}
                className="px-2 py-1 text-[10px] font-mono font-bold uppercase rounded transition-all"
                style={chartSymbol === sym ? {
                  background: "rgba(0,212,255,0.12)",
                  border: "1px solid rgba(0,212,255,0.4)",
                  color: "#00D4FF",
                } : {
                  border: "1px solid transparent",
                  color: "#4a6a8a",
                }}
              >
                {sym}
              </button>
            ))}
            <button
              onClick={() => setModalSymbol(chartSymbol)}
              className="ml-auto text-[10px] text-gray-600 hover:text-cyan-400 transition-colors pr-2"
              title="Expand chart"
            >
              ⤢ expand
            </button>
          </div>
          <CandlestickChart
            symbol={chartSymbol}
            height={320}
            showSignals
            showPositions
            compact
            onExpand={() => setModalSymbol(chartSymbol)}
          />
        </div>

        <div className="lg:col-span-1 fold-card overflow-hidden">
          <AgentLog maxEntries={50} compact />
        </div>
      </div>

      {/* ── Row 3: Services | Kill Switch ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ServiceStatusGrid refreshKey={refreshKey} />
        <KillSwitch refreshKey={refreshKey} />
      </div>

      {/* ── Row 4: Positions ── */}
      <PositionsTable refreshKey={refreshKey} />

      {/* ── Row 5: Recent Signals | Ensemble Weights ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <RecentSignals refreshKey={refreshKey} limit={10} />
        </div>
        <EnsembleWeights />
      </div>

      {/* ── Chart modal ── */}
      {modalSymbol && (
        <ChartModal symbol={modalSymbol} onClose={() => setModalSymbol(null)} />
      )}
    </div>
  );
}
