"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import Sidebar from "@/components/Sidebar";
import SignalRadar from "@/components/SignalRadar";

// Dynamic import to avoid SSR crash
const CandlestickChart = dynamic(() => import("@/components/CandlestickChart"), { ssr: false });
const ChartModal       = dynamic(() => import("@/components/ChartModal"),       { ssr: false });

// ── Watchlist ─────────────────────────────────────────────────────────────
const WATCHLIST = [
  { symbol: "NVDA",  name: "NVIDIA Corp",        sector: "Tech"      },
  { symbol: "AAPL",  name: "Apple Inc",           sector: "Tech"      },
  { symbol: "MSFT",  name: "Microsoft Corp",      sector: "Tech"      },
  { symbol: "TSLA",  name: "Tesla Inc",           sector: "EV/Auto"   },
  { symbol: "AMZN",  name: "Amazon.com",          sector: "Consumer"  },
  { symbol: "META",  name: "Meta Platforms",      sector: "Tech"      },
  { symbol: "GOOGL", name: "Alphabet Inc",        sector: "Tech"      },
  { symbol: "AMD",   name: "Advanced Micro",      sector: "Semi"      },
  { symbol: "SPY",   name: "S&P 500 ETF",         sector: "ETF"       },
  { symbol: "QQQ",   name: "Nasdaq 100 ETF",      sector: "ETF"       },
];

// ── Quick trade ────────────────────────────────────────────────────────────
async function quickTrade(symbol: string, side: "buy" | "sell") {
  await fetch("/api/orders", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ symbol, side, qty: 1, type: "market", time_in_force: "day" }),
  });
}

// ── Main page ─────────────────────────────────────────────────────────────
export default function ChartsPage() {
  const [selected,    setSelected]    = useState("NVDA");
  const [modalSymbol, setModalSymbol] = useState<string | null>(null);
  const [orderSent,   setOrderSent]   = useState(false);
  const [qty,         setQty]         = useState("1");
  const [chartHeight, setChartHeight] = useState(500);

  useEffect(() => {
    const calculate = () => {
      // viewport - topbar(64) - chart-header(48) - padding(32) - buffer(100)
      setChartHeight(Math.max(400, window.innerHeight - 64 - 48 - 32 - 100));
    };
    calculate();
    window.addEventListener("resize", calculate);
    return () => window.removeEventListener("resize", calculate);
  }, []);

  const handleTrade = async (side: "buy" | "sell") => {
    setOrderSent(true);
    await quickTrade(selected, side);
    setTimeout(() => setOrderSent(false), 2000);
  };

  return (
    <div className="flex h-screen bg-[#050810] text-white overflow-hidden">
      <Sidebar />

      {/* Left watchlist panel */}
      <div
        className="w-[200px] shrink-0 flex flex-col border-r border-[#1e2d40] overflow-y-auto"
        style={{ background: "rgba(7,12,24,0.95)" }}
      >
        <div className="px-3 py-3 border-b border-[#1e2d40]">
          <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">Watchlist</span>
        </div>
        {WATCHLIST.map(w => (
          <button
            key={w.symbol}
            onClick={() => setSelected(w.symbol)}
            className="flex flex-col items-start px-3 py-2.5 text-left transition-all border-b border-[#0d1520] hover:bg-[#0d1520]"
            style={selected === w.symbol ? {
              background: "rgba(0,212,255,0.08)",
              borderLeft: "2px solid #00D4FF",
            } : { borderLeft: "2px solid transparent" }}
          >
            <span className={`font-mono text-xs font-bold ${selected === w.symbol ? "text-cyan-400" : "text-gray-300"}`}>
              {w.symbol}
            </span>
            <span className="text-[9px] text-gray-600 truncate w-full">{w.name}</span>
            <span className="text-[9px] text-gray-700">{w.sector}</span>
          </button>
        ))}
      </div>

      {/* Main chart area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Chart header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1e2d40]">
          <div className="flex items-center gap-3">
            <span className="text-lg font-bold font-mono text-white">{selected}</span>
            <span className="text-[10px] text-gray-600">
              {WATCHLIST.find(w => w.symbol === selected)?.name}
            </span>
          </div>
          <button
            onClick={() => setModalSymbol(selected)}
            className="text-[10px] text-gray-600 hover:text-cyan-400 transition-colors border border-[#1e2d40] hover:border-cyan-500/40 px-2 py-1 rounded"
          >
            ⤢ fullscreen
          </button>
        </div>

        {/* Chart */}
        <div className="flex-1 overflow-hidden">
          <CandlestickChart
            key={selected}
            symbol={selected}
            height={chartHeight}
            showSignals
            showPositions
            onExpand={() => setModalSymbol(selected)}
          />
        </div>
      </div>

      {/* Right sidebar — Signal radar + quick trade */}
      <div
        className="w-[240px] shrink-0 flex flex-col border-l border-[#1e2d40] p-4 gap-4 overflow-y-auto"
        style={{ background: "rgba(7,12,24,0.95)" }}
      >
        {/* Signal radar */}
        <div className="fold-card p-3">
          <SignalRadar symbol={selected} height={200} />
        </div>

        {/* Quick trade */}
        <div className="fold-card p-3 space-y-3">
          <div className="text-[10px] text-gray-500 uppercase tracking-widest">Quick Trade</div>
          <div className="text-center">
            <span className="font-mono font-bold text-sm text-white">{selected}</span>
          </div>
          <div>
            <label className="text-[10px] text-gray-600 mb-1 block">Quantity</label>
            <input
              type="number"
              value={qty}
              onChange={e => setQty(e.target.value)}
              min="1"
              className="w-full bg-[#0d1520] border border-[#1e2d40] rounded px-3 py-2 text-sm text-white outline-none focus:border-cyan-500/50 text-center font-mono"
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => handleTrade("buy")}
              disabled={orderSent}
              className="py-2.5 rounded text-xs font-bold text-black transition-all disabled:opacity-50"
              style={{ background: "#00FF88" }}
            >
              {orderSent ? "✓ SENT" : "▲ BUY"}
            </button>
            <button
              onClick={() => handleTrade("sell")}
              disabled={orderSent}
              className="py-2.5 rounded text-xs font-bold text-white transition-all disabled:opacity-50"
              style={{ background: "#FF2D55" }}
            >
              {orderSent ? "✓ SENT" : "▼ SELL"}
            </button>
          </div>
          <p className="text-[9px] text-gray-700 text-center">Market order · Paper mode</p>
        </div>

        {/* Keyboard shortcuts info */}
        <div className="text-[9px] text-gray-700 space-y-1 mt-auto">
          {[
            ["⌘K",  "Command palette"],
            ["⌘1",  "Dashboard"],
            ["⌘2",  "Charts"],
            ["⌘5",  "Risk page"],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <kbd className="bg-[#1e2d40] px-1 rounded font-mono">{k}</kbd>
              <span>{v}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Chart modal */}
      {modalSymbol && (
        <ChartModal symbol={modalSymbol} onClose={() => setModalSymbol(null)} />
      )}
    </div>
  );
}
