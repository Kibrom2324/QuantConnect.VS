"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Zap, AlertTriangle, X, Pause } from "lucide-react";

interface TradingConfig {
  auto_trading_enabled: boolean;
  account_mode: "paper" | "live";
  trades_today: number;
  max_daily_trades: number;
  is_market_open: boolean;
}

export default function AutoTradingBanner() {
  const [cfg,      setCfg]      = useState<TradingConfig | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const load = () => {
      fetch("/api/trading-mode")
        .then((r) => r.json())
        .then((d: TradingConfig) => {
          setCfg(d);
          // If trading is re-enabled, show banner again
          if (d.auto_trading_enabled) setDismissed(false);
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, []);

  const handlePause = async () => {
    await fetch("/api/trading-mode", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ auto_trading_enabled: false }),
    });
    setCfg((prev) => prev ? { ...prev, auto_trading_enabled: false } : prev);
  };

  if (!cfg || !cfg.auto_trading_enabled || dismissed) return null;

  const isLive = cfg.account_mode === "live";

  return (
    <div
      className="fixed top-0 left-0 right-0 z-[100] flex items-center gap-3 px-4 py-2.5 font-mono text-[11px]"
      style={{
        background: isLive
          ? "linear-gradient(90deg, rgba(10,6,0,0.98), rgba(20,10,0,0.98))"
          : "linear-gradient(90deg, rgba(0,6,14,0.97), rgba(0,10,20,0.97))",
        borderBottom: `1px solid ${isLive ? "rgba(255,184,0,0.5)" : "rgba(0,212,255,0.3)"}`,
        boxShadow: isLive
          ? "0 0 20px rgba(255,184,0,0.15)"
          : "0 0 14px rgba(0,212,255,0.08)",
        animation: isLive ? "glow-red 2.5s ease-in-out infinite alternate" : "none",
      }}
    >
      {/* Icon */}
      {isLive ? (
        <AlertTriangle
          style={{ width: 14, height: 14, color: "#FFB800", flexShrink: 0,
            animation: "blink-dot 1.5s step-end infinite" }}
        />
      ) : (
        <Zap
          style={{ width: 14, height: 14, color: "#00D4FF", flexShrink: 0,
            animation: "blink-dot 2s step-end infinite" }}
        />
      )}

      {/* Main text */}
      <span
        className="font-bold uppercase tracking-widest"
        style={{ color: isLive ? "#FFB800" : "#00D4FF" }}
      >
        ⚡ AUTO TRADING ACTIVE —{" "}
        {isLive ? "⚠ LIVE MONEY" : "PAPER MODE"}
      </span>

      {/* Details */}
      <span style={{ color: "#4a6a8a" }}>
        {isLive ? "Real orders being placed" : "Simulated orders only"}
        {" · "}
        <span style={{ color: isLive ? "#FFB800" : "#00D4FF" }}>
          {cfg.trades_today}/{cfg.max_daily_trades}
        </span>
        {" today"}
        {" · "}
        <span style={{ color: cfg.is_market_open ? "#00FF88" : "#4a6a8a" }}>
          {cfg.is_market_open ? "MARKET OPEN" : "MARKET CLOSED"}
        </span>
      </span>

      {/* Right side actions */}
      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={handlePause}
          className="flex items-center gap-1 px-2.5 py-1 rounded font-mono text-[10px] uppercase font-bold tracking-wider transition-all"
          style={{
            background: isLive ? "rgba(255,184,0,0.15)" : "rgba(0,212,255,0.12)",
            border:     `1px solid ${isLive ? "rgba(255,184,0,0.4)" : "rgba(0,212,255,0.3)"}`,
            color:      isLive ? "#FFB800" : "#00D4FF",
          }}
        >
          <Pause style={{ width: 9, height: 9 }} />
          PAUSE
        </button>
        <button
          onClick={() => router.push("/trading")}
          className="flex items-center gap-1 px-2.5 py-1 rounded font-mono text-[10px] uppercase font-bold tracking-wider transition-all"
          style={{
            background: "rgba(0,212,255,0.08)",
            border:     "1px solid rgba(0,212,255,0.2)",
            color:      "#8aadcc",
          }}
        >
          →
        </button>
        <button
          onClick={() => setDismissed(true)}
          className="flex items-center rounded p-0.5 transition-all"
          style={{ color: "#2a4a6a" }}
          title="Dismiss banner"
        >
          <X style={{ width: 12, height: 12 }} />
        </button>
      </div>
    </div>
  );
}
