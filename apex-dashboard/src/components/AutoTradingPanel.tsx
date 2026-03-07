"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Power, Zap, AlertTriangle, Clock, TrendingUp, Settings } from "lucide-react";

interface TradingConfig {
  auto_trading_enabled: boolean;
  account_mode: "paper" | "live";
  min_confidence: number;
  market_hours_only: boolean;
  max_position_size_usd: number;
  max_daily_trades: number;
  trades_today: number;
  is_market_open: boolean;
  live_trading_available: boolean;
  last_updated: string;
}

interface KillState {
  active: boolean;
}

// ── Small inline toggle ────────────────────────────────────────────────────
function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className="relative inline-flex items-center rounded-full transition-all"
      style={{
        width: 36,
        height: 20,
        background: checked ? "rgba(0,255,136,0.3)" : "rgba(74,106,138,0.3)",
        border: `1px solid ${checked ? "rgba(0,255,136,0.6)" : "rgba(74,106,138,0.5)"}`,
        boxShadow: checked ? "0 0 8px rgba(0,255,136,0.3)" : "none",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        flexShrink: 0,
      }}
    >
      <span
        style={{
          position:   "absolute",
          width:      14,
          height:     14,
          borderRadius: "50%",
          background: checked ? "#00FF88" : "#4a6a8a",
          left:       checked ? 19 : 2,
          transition: "left 0.18s ease, background 0.18s ease",
          boxShadow:  checked ? "0 0 6px rgba(0,255,136,0.6)" : "none",
        }}
      />
    </button>
  );
}

// ── Confirmation modal ─────────────────────────────────────────────────────
function Modal({
  title,
  children,
  onCancel,
  onConfirm,
  confirmLabel = "CONFIRM",
  confirmColor = "#00D4FF",
  requirePhrase,
}: {
  title: string;
  children: React.ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
  confirmLabel?: string;
  confirmColor?: string;
  requirePhrase?: string;
}) {
  const [phrase, setPhrase] = useState("");
  const phraseMet = !requirePhrase || phrase === requirePhrase;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(4px)" }}
    >
      <div
        className="rounded-lg p-6 max-w-sm w-full mx-4 space-y-4"
        style={{
          background: "#070c18",
          border:     "1px solid rgba(0,212,255,0.3)",
          boxShadow:  "0 0 40px rgba(0,0,0,0.8), 0 0 20px rgba(0,212,255,0.1)",
        }}
      >
        <h3 className="font-heading text-base font-bold uppercase tracking-widest text-[#e2f0ff]">
          {title}
        </h3>
        <div className="text-[11px] font-mono text-[#8aadcc] space-y-2 leading-relaxed">
          {children}
        </div>
        {requirePhrase && (
          <div className="space-y-1">
            <p className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">
              Type <span style={{ color: "#FFB800" }}>"{requirePhrase}"</span> to proceed:
            </p>
            <input
              autoFocus
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              placeholder={requirePhrase}
              className="w-full px-3 py-2 rounded font-mono text-[11px] outline-none"
              style={{
                background: "rgba(0,0,0,0.4)",
                border:     `1px solid ${phrase === requirePhrase ? "rgba(0,255,136,0.5)" : "rgba(74,106,138,0.4)"}`,
                color:      phrase === requirePhrase ? "#00FF88" : "#e2f0ff",
              }}
            />
          </div>
        )}
        <div className="flex gap-3 pt-2">
          <button
            onClick={onCancel}
            className="flex-1 py-2 rounded font-mono text-[11px] uppercase tracking-wider transition-all"
            style={{
              background: "rgba(74,106,138,0.15)",
              border:     "1px solid rgba(74,106,138,0.3)",
              color:      "#4a6a8a",
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!phraseMet}
            className="flex-1 py-2 rounded font-mono text-[11px] uppercase font-bold tracking-wider transition-all"
            style={{
              background: phraseMet ? `${confirmColor}22` : "rgba(74,106,138,0.08)",
              border:     `1px solid ${phraseMet ? `${confirmColor}60` : "rgba(74,106,138,0.2)"}`,
              color:      phraseMet ? confirmColor : "#4a6a8a",
              cursor:     phraseMet ? "pointer" : "not-allowed",
              boxShadow:  phraseMet ? `0 0 12px ${confirmColor}30` : "none",
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────
interface Props {
  refreshKey?: number;
  onConfigChange?: (cfg: TradingConfig) => void;
}

export default function AutoTradingPanel({ refreshKey = 0, onConfigChange }: Props) {
  const [cfg,       setCfg]       = useState<TradingConfig | null>(null);
  const [kill,      setKill]      = useState<KillState>({ active: false });
  const [saving,    setSaving]    = useState(false);
  const [modal,     setModal]     = useState<null | "live-switch" | "auto-on-live">(null);
  const [pendingCfg, setPendingCfg] = useState<Partial<TradingConfig> | null>(null);
  const saveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadAll = useCallback(() => {
    fetch("/api/trading-mode")
      .then((r) => r.json())
      .then((d: TradingConfig) => {
        setCfg(d);
        onConfigChange?.(d);
      })
      .catch(() => {});
    fetch("/api/kill-switch")
      .then((r) => r.json())
      .then((d: KillState) => setKill(d))
      .catch(() => {});
  }, [onConfigChange]);

  useEffect(() => { loadAll(); }, [refreshKey, loadAll]);

  const applyUpdate = useCallback(async (patch: Partial<TradingConfig>) => {
    setSaving(true);
    try {
      const res  = await fetch("/api/trading-mode", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(patch),
      });
      const data: TradingConfig = await res.json();
      if (res.ok) {
        setCfg(data);
        onConfigChange?.(data);
      }
    } catch { /* ignore */ }
    finally { setSaving(false); }
  }, [onConfigChange]);

  // Debounced update for sliders/inputs
  const debouncedUpdate = useCallback((patch: Partial<TradingConfig>) => {
    if (saveTimeout.current) clearTimeout(saveTimeout.current);
    saveTimeout.current = setTimeout(() => applyUpdate(patch), 600);
  }, [applyUpdate]);

  const handleAccountModeClick = (mode: "paper" | "live") => {
    if (!cfg || mode === cfg.account_mode) return;
    if (mode === "live") {
      if (!cfg.live_trading_available) return;
      setPendingCfg({ account_mode: "live" });
      setModal("live-switch");
    } else {
      applyUpdate({ account_mode: "paper" });
    }
  };

  const handleAutoToggle = (newVal: boolean) => {
    if (!cfg) return;
    if (newVal && cfg.account_mode === "live") {
      setPendingCfg({ auto_trading_enabled: true });
      setModal("auto-on-live");
    } else {
      applyUpdate({ auto_trading_enabled: newVal });
    }
  };

  const confirmModal = () => {
    if (pendingCfg) applyUpdate(pendingCfg);
    setModal(null);
    setPendingCfg(null);
  };

  const cancelModal = () => {
    setModal(null);
    setPendingCfg(null);
  };

  if (!cfg) {
    return (
      <div className="apex-card flex items-center justify-center py-12">
        <span className="text-[10px] font-mono text-[#4a6a8a] uppercase tracking-widest animate-pulse">
          Loading trading engine…
        </span>
      </div>
    );
  }

  const isKilled    = kill.active;
  const isEnabled   = cfg.auto_trading_enabled && !isKilled;
  const isLive      = cfg.account_mode === "live";
  const mktOpen     = cfg.is_market_open;

  // Status badge
  const statusBadge = isKilled
    ? { label: "⚠ HALTED",  color: "#FF2D55", bg: "rgba(255,45,85,0.15)",  border: "rgba(255,45,85,0.4)"  }
    : isEnabled
    ? { label: "● ACTIVE",  color: "#00FF88", bg: "rgba(0,255,136,0.12)",  border: "rgba(0,255,136,0.4)"  }
    : { label: "○ PAUSED",  color: "#4a6a8a", bg: "rgba(74,106,138,0.12)", border: "rgba(74,106,138,0.3)" };

  // Card accent
  const cardBorder = isKilled ? "rgba(255,45,85,0.3)" : isEnabled && isLive ? "rgba(255,184,0,0.3)" : isEnabled ? "rgba(0,255,136,0.25)" : "rgba(0,212,255,0.12)";

  return (
    <>
      {/* ── Modals ── */}
      {modal === "live-switch" && (
        <Modal
          title="⚠ Enable Live Trading?"
          confirmLabel="I UNDERSTAND – SWITCH TO LIVE"
          confirmColor="#FF2D55"
          requirePhrase="I UNDERSTAND"
          onCancel={cancelModal}
          onConfirm={confirmModal}
        >
          <p style={{ color: "#FF6B80", fontWeight: 700 }}>
            You are switching to LIVE trading mode.
          </p>
          <p>Real money will be at risk. All orders placed while in live mode will execute against your actual brokerage account.</p>
          <p>Ensure your strategy is fully validated on paper trading before proceeding.</p>
        </Modal>
      )}

      {modal === "auto-on-live" && (
        <Modal
          title="⚠ Enable Auto Trading in LIVE Mode?"
          confirmLabel="ACTIVATE LIVE AUTO TRADING"
          confirmColor="#FF2D55"
          requirePhrase="CONFIRM"
          onCancel={cancelModal}
          onConfirm={confirmModal}
        >
          <p style={{ color: "#FF6B80", fontWeight: 700 }}>
            The APEX engine will place REAL orders automatically.
          </p>
          <p>This means orders may execute without any further confirmation from you. Real money is at stake.</p>
          <p>Kill switch remains available at all times to halt trading immediately.</p>
        </Modal>
      )}

      <div
        className="apex-card flex flex-col gap-4"
        style={{
          border:    `1px solid ${cardBorder}`,
          boxShadow: isEnabled
            ? isLive
              ? "0 0 20px rgba(255,184,0,0.08)"
              : "0 0 16px rgba(0,255,136,0.05)"
            : "none",
          transition: "border-color 0.3s ease, box-shadow 0.3s ease",
        }}
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-0.5">
              Trading Engine
            </div>
            <div className="flex items-center gap-2">
              <Zap style={{ width: 14, height: 14, color: "#00D4FF" }} />
              <span className="font-heading text-sm font-bold text-[#e2f0ff] uppercase tracking-widest">
                Auto Trading Engine
              </span>
              {saving && (
                <span className="text-[8px] font-mono text-[#4a6a8a] animate-pulse">saving…</span>
              )}
            </div>
          </div>
          <span
            className="text-[10px] font-mono font-bold uppercase tracking-wider px-2 py-1 rounded"
            style={{
              color:      statusBadge.color,
              background: statusBadge.bg,
              border:     `1px solid ${statusBadge.border}`,
              boxShadow:  isEnabled ? `0 0 8px ${statusBadge.color}30` : "none",
              animation:  isEnabled && !isKilled ? "blink-dot 2s step-end infinite" : "none",
            }}
          >
            {statusBadge.label}
          </span>
        </div>

        {/* ── Kill switch warning ── */}
        {isKilled && (
          <div
            className="flex items-center gap-2 px-3 py-2 rounded text-[10px] font-mono"
            style={{
              background: "rgba(255,45,85,0.1)",
              border:     "1px solid rgba(255,45,85,0.35)",
              color:      "#FF6B80",
            }}
          >
            <AlertTriangle style={{ width: 13, height: 13, flexShrink: 0 }} />
            Kill switch active — resume trading in Kill Switch panel first
          </div>
        )}

        {/* ── Account mode selector ── */}
        <div>
          <div className="text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a] mb-2">
            Account Mode
          </div>
          <div className="flex gap-2">
            {/* Paper */}
            <button
              onClick={() => handleAccountModeClick("paper")}
              disabled={isKilled}
              className="flex-1 py-2 rounded font-mono text-[11px] uppercase font-bold tracking-wider transition-all"
              style={{
                background: cfg.account_mode === "paper"
                  ? "rgba(0,212,255,0.15)"
                  : "rgba(0,212,255,0.04)",
                border: cfg.account_mode === "paper"
                  ? "2px solid rgba(0,212,255,0.6)"
                  : "1px solid rgba(0,212,255,0.2)",
                color:  cfg.account_mode === "paper" ? "#00D4FF" : "#4a6a8a",
                cursor: isKilled ? "not-allowed" : "pointer",
              }}
              title={isKilled ? "Kill switch active — resume trading first" : undefined}
            >
              PAPER TRADING
            </button>

            {/* Live */}
            <button
              onClick={() => handleAccountModeClick("live")}
              disabled={isKilled || !cfg.live_trading_available}
              className="flex-1 py-2 rounded font-mono text-[11px] uppercase font-bold tracking-wider transition-all relative"
              style={{
                background: cfg.account_mode === "live"
                  ? "rgba(255,45,85,0.18)"
                  : "rgba(255,45,85,0.04)",
                border: cfg.account_mode === "live"
                  ? "2px solid rgba(255,45,85,0.6)"
                  : "1px solid rgba(255,45,85,0.2)",
                color:  cfg.live_trading_available
                  ? cfg.account_mode === "live" ? "#FF2D55" : "#4a6a8a"
                  : "#2a4a6a",
                cursor: isKilled || !cfg.live_trading_available ? "not-allowed" : "pointer",
                opacity: cfg.live_trading_available ? 1 : 0.6,
              }}
              title={
                isKilled
                  ? "Kill switch active — resume trading first"
                  : !cfg.live_trading_available
                  ? "Live trading requires API key configuration"
                  : undefined
              }
            >
              {cfg.live_trading_available ? "LIVE TRADING" : "LIVE (NO KEY)"}
            </button>
          </div>

          {/* Live warning badge */}
          {isLive && (
            <div
              className="flex items-center gap-2 mt-2 px-3 py-2 rounded text-[10px] font-mono"
              style={{
                background: "rgba(255,45,85,0.08)",
                border:     "1px solid rgba(255,45,85,0.3)",
                color:      "#FF6B80",
              }}
            >
              <AlertTriangle style={{ width: 12, height: 12, flexShrink: 0 }} />
              <span>
                <strong>LIVE MODE — Real money at risk.</strong>&nbsp;
                Ensure strategy is fully validated before enabling auto trading.
              </span>
            </div>
          )}
        </div>

        {/* ── Main toggle ── */}
        <div
          className="rounded-lg px-4 py-3 flex items-center justify-between gap-3 transition-all"
          style={{
            background: isEnabled
              ? isLive ? "rgba(255,184,0,0.08)" : "rgba(0,255,136,0.07)"
              : "rgba(0,0,0,0.3)",
            border: isEnabled
              ? isLive ? "1px solid rgba(255,184,0,0.3)" : "1px solid rgba(0,255,136,0.25)"
              : "1px solid rgba(0,212,255,0.08)",
          }}
        >
          <div>
            <div
              className="font-mono font-bold text-sm uppercase tracking-wider"
              style={{
                color:      isEnabled ? (isLive ? "#FFB800" : "#00FF88") : "#4a6a8a",
                textShadow: isEnabled ? `0 0 8px ${isLive ? "#FFB800" : "#00FF88"}60` : "none",
                animation:  isEnabled && isLive ? "glow-red 2s ease-in-out infinite alternate" : "none",
              }}
            >
              {isEnabled ? (isLive ? "⚡ AUTO TRADING ACTIVE — LIVE" : "⚡ AUTO TRADING ACTIVE") : "AUTO TRADING DISABLED"}
            </div>
            <div className="text-[10px] font-mono mt-0.5" style={{ color: "#4a6a8a" }}>
              {isEnabled
                ? "Engine placing orders automatically"
                : "System monitoring only"}
            </div>
          </div>
          <div
            className="flex flex-col items-center gap-1"
            title={isKilled ? "Kill switch active — resume trading first" : undefined}
          >
            <Toggle
              checked={cfg.auto_trading_enabled}
              onChange={handleAutoToggle}
              disabled={isKilled || saving}
            />
            <Power
              style={{
                width: 12, height: 12,
                color: isEnabled ? (isLive ? "#FFB800" : "#00FF88") : "#2a4a6a",
              }}
            />
          </div>
        </div>

        {/* ── Controls (when enabled) ── */}
        {cfg.auto_trading_enabled && !isKilled && (
          <div className="space-y-3">
            <div className="flex items-center gap-1.5 text-[9px] font-mono uppercase tracking-widest text-[#4a6a8a]">
              <Settings style={{ width: 10, height: 10 }} />
              Engine Settings
            </div>

            {/* Min confidence slider */}
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono text-[#8aadcc]">Min Confidence</span>
                <span
                  className="font-mono text-[11px] font-bold tabular-nums"
                  style={{ color: "#00D4FF" }}
                >
                  {cfg.min_confidence}%
                </span>
              </div>
              <input
                type="range"
                min={50}
                max={95}
                step={5}
                value={cfg.min_confidence}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  setCfg((prev) => prev ? { ...prev, min_confidence: v } : prev);
                  debouncedUpdate({ min_confidence: v });
                }}
                className="w-full h-1 appearance-none rounded cursor-pointer"
                style={{
                  background: `linear-gradient(to right, #00D4FF ${((cfg.min_confidence - 50) / 45) * 100}%, rgba(74,106,138,0.3) 0%)`,
                  accentColor: "#00D4FF",
                }}
              />
              <p className="text-[9px] font-mono text-[#4a6a8a]">
                Only trade signals above this threshold
              </p>
            </div>

            {/* Market hours only */}
            <div className="flex items-center justify-between py-1">
              <div>
                <span className="text-[10px] font-mono text-[#8aadcc]">Market Hours Only</span>
                <p className="text-[9px] font-mono text-[#4a6a8a]">Restrict trading to 9:30–16:00 ET</p>
              </div>
              <Toggle
                checked={cfg.market_hours_only}
                onChange={(v) => {
                  setCfg((prev) => prev ? { ...prev, market_hours_only: v } : prev);
                  applyUpdate({ market_hours_only: v });
                }}
              />
            </div>

            {/* Max position size */}
            <div className="space-y-1">
              <span className="text-[10px] font-mono text-[#8aadcc]">Max Position Size</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-[#4a6a8a]">$</span>
                <input
                  type="number"
                  min={100}
                  max={50000}
                  step={500}
                  value={cfg.max_position_size_usd}
                  onChange={(e) => {
                    const v = parseInt(e.target.value) || 5000;
                    setCfg((prev) => prev ? { ...prev, max_position_size_usd: v } : prev);
                    debouncedUpdate({ max_position_size_usd: v });
                  }}
                  className="flex-1 px-2 py-1.5 rounded font-mono text-[11px] outline-none"
                  style={{
                    background: "rgba(0,212,255,0.06)",
                    border:     "1px solid rgba(0,212,255,0.2)",
                    color:      "#e2f0ff",
                  }}
                />
              </div>
              <p className="text-[9px] font-mono text-[#4a6a8a]">Maximum USD per single position</p>
            </div>

            {/* Max daily trades */}
            <div className="space-y-1">
              <span className="text-[10px] font-mono text-[#8aadcc]">Max Daily Trades</span>
              <input
                type="number"
                min={1}
                max={100}
                value={cfg.max_daily_trades}
                onChange={(e) => {
                  const v = parseInt(e.target.value) || 10;
                  setCfg((prev) => prev ? { ...prev, max_daily_trades: v } : prev);
                  debouncedUpdate({ max_daily_trades: v });
                }}
                className="w-full px-2 py-1.5 rounded font-mono text-[11px] outline-none"
                style={{
                  background: "rgba(0,212,255,0.06)",
                  border:     "1px solid rgba(0,212,255,0.2)",
                  color:      "#e2f0ff",
                }}
              />
              <p className="text-[9px] font-mono text-[#4a6a8a]">
                Stop auto-trading after N trades today
              </p>
            </div>
          </div>
        )}

        {/* ── Live status bar (when enabled) ── */}
        {isEnabled && (
          <div
            className="rounded px-3 py-2 space-y-1.5"
            style={{
              background: "rgba(0,212,255,0.04)",
              border:     "1px solid rgba(0,212,255,0.1)",
            }}
          >
            <div className="flex items-center gap-1.5 text-[9px] font-mono text-[#4a6a8a]">
              <span
                className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                style={{ background: "#00FF88", animation: "blink-dot 1s step-end infinite" }}
              />
              Engine checking signals every 30s
            </div>
            <div className="flex items-center gap-4 flex-wrap">
              <span className="text-[9px] font-mono">
                <span className="text-[#4a6a8a]">Today: </span>
                <span
                  style={{
                    color: cfg.trades_today >= cfg.max_daily_trades ? "#FF2D55" : "#00D4FF",
                    fontWeight: 700,
                  }}
                >
                  {cfg.trades_today}/{cfg.max_daily_trades}
                </span>
                <span className="text-[#4a6a8a]"> trades used</span>
              </span>
              <span className="text-[9px] font-mono flex items-center gap-1">
                <Clock style={{ width: 9, height: 9, color: "#4a6a8a" }} />
                <span className="text-[#4a6a8a]">Market: </span>
                <span style={{ color: mktOpen ? "#00FF88" : "#4a6a8a", fontWeight: 700 }}>
                  {mktOpen ? "OPEN" : "CLOSED"}
                </span>
              </span>
              <span className="text-[9px] font-mono flex items-center gap-1">
                <TrendingUp style={{ width: 9, height: 9, color: "#4a6a8a" }} />
                <span className="text-[#4a6a8a]">Min confidence: </span>
                <span style={{ color: "#00D4FF" }}>{cfg.min_confidence}%</span>
              </span>
            </div>
            {cfg.market_hours_only && !mktOpen && (
              <div className="text-[9px] font-mono" style={{ color: "#FFB800" }}>
                ⚠ Market closed — engine paused (market_hours_only enabled)
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
