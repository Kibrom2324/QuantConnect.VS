"use client";

import { useEffect, useState, useCallback } from "react";
import { ShieldCheck, AlertTriangle } from "lucide-react";

interface KillState {
  active: boolean;
  method?: string;
  activated_at?: string;
  is_mock?: boolean;
  error?: string;
}

function timeAgo(ts: string | undefined) {
  if (!ts) return null;
  const d = (Date.now() - new Date(ts).getTime()) / 1000;
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  return `${Math.floor(d / 3600)}h ago`;
}

export default function KillSwitch({ refreshKey }: { refreshKey: number }) {
  const [state, setState]   = useState<KillState | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);

  const load = useCallback(() => {
    fetch("/api/kill-switch")
      .then((r) => r.json())
      .then((d) => { setState(d); setLastCheck(new Date()); })
      .catch(() => setState({ active: false, error: "offline" }));
  }, []);

  useEffect(() => { load(); }, [refreshKey, load]);

  const toggle = async () => {
    setLoading(true);
    const endpoint = state?.active ? "/api/kill-switch/disable" : "/api/kill-switch/enable";
    try {
      const res  = await fetch(endpoint, { method: "POST" });
      const data = await res.json();
      setState((prev) => ({
        ...prev,
        active:       data.activated,
        method:       data.method,
        activated_at: data.activated_at,
      }));
      setLastCheck(new Date());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  const isActive  = state?.active === true;
  const isLoading = loading || state === null;

  // card accent colours
  const accentColor  = isActive ? "#FF2D55" : "#00FF88";
  const accentShadow = isActive
    ? "0 0 0 1px rgba(255,45,85,0.35), 0 0 24px rgba(255,45,85,0.18)"
    : "0 0 0 1px rgba(0,255,136,0.25), 0 0 16px rgba(0,255,136,0.08)";
  const accentAnim   = isActive ? "glow-red 1.4s ease-in-out infinite alternate" : "none";

  return (
    <div
      className="apex-card flex flex-col"
      style={{
        boxShadow: accentShadow,
        animation: accentAnim,
        position:  "relative",
        overflow:  "hidden",
        minHeight: 260,
      }}
    >
      {/* Active background pulse */}
      {isActive && (
        <div
          aria-hidden
          style={{
            position:   "absolute", inset: 0, pointerEvents: "none",
            background: "radial-gradient(ellipse at 50% 0%, rgba(255,45,85,0.10) 0%, transparent 70%)",
          }}
        />
      )}

      {/* ── Header ─────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="text-[10px] text-[#4a6a8a] uppercase tracking-widest mb-0.5">Emergency Control</div>
          <div className="flex items-center gap-2">
            <span className="font-heading text-sm font-semibold text-[#e2f0ff]">KILL SWITCH</span>
            {state?.is_mock && <span className="demo-badge">DEMO</span>}
          </div>
        </div>

        {/* Status badge */}
        <div
          className="text-[10px] font-mono font-bold uppercase tracking-wider px-2.5 py-1 rounded"
          style={{
            color:      accentColor,
            background: `${accentColor}18`,
            border:     `1px solid ${accentColor}45`,
            boxShadow:  `0 0 8px ${accentColor}30`,
            animation:  isActive ? "blink-dot 1.2s step-end infinite" : "none",
          }}
        >
          {isLoading ? "…" : isActive ? "● HALTED" : "● ACTIVE"}
        </div>
      </div>

      {/* ── Body ───────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col items-center justify-center text-center py-3 gap-3">

        {/* Big icon */}
        {isActive ? (
          <div
            style={{
              width: 56, height: 56, borderRadius: "50%",
              background: "rgba(255,45,85,0.12)",
              border: "2px solid rgba(255,45,85,0.4)",
              display: "flex", alignItems: "center", justifyContent: "center",
              animation: "glow-red 1.4s ease-in-out infinite alternate",
            }}
          >
            <AlertTriangle style={{ width: 28, height: 28, color: "#FF2D55" }} />
          </div>
        ) : (
          <div
            style={{
              width: 56, height: 56, borderRadius: "50%",
              background: "rgba(0,255,136,0.08)",
              border: "2px solid rgba(0,255,136,0.3)",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <ShieldCheck style={{ width: 28, height: 28, color: "#00FF88" }} />
          </div>
        )}

        {/* Status text */}
        {isActive ? (
          <div>
            <div
              className="font-heading font-bold text-base uppercase tracking-wider"
              style={{ color: "#FF2D55", textShadow: "0 0 12px rgba(255,45,85,0.6)" }}
            >
              ⚠ EMERGENCY HALT ACTIVE
            </div>
            <div className="text-[11px] font-mono mt-0.5" style={{ color: "#FF6B80" }}>
              ALL TRADING SUSPENDED
            </div>
          </div>
        ) : (
          <div>
            <div
              className="font-heading font-bold text-base uppercase tracking-wider"
              style={{ color: "#00FF88", textShadow: "0 0 10px rgba(0,255,136,0.5)" }}
            >
              ✓ TRADING ACTIVE
            </div>
            <div className="text-[11px] font-mono mt-0.5" style={{ color: "#4a8a6a" }}>
              All systems nominal
            </div>
          </div>
        )}

        {/* Detail lines */}
        <div className="text-[10px] font-mono space-y-0.5 text-[#4a6a8a]">
          {isActive && state?.activated_at && (
            <div>
              Activated:{" "}
              <span style={{ color: "#FFB800" }}>
                {new Date(state.activated_at).toLocaleTimeString("en-GB", { hour12: false })} UTC
              </span>
            </div>
          )}
          {state?.method && (
            <div>
              {state.is_mock
                ? <>Mode: <span style={{ color: "#FFB800" }}>Demo</span> · No Redis required</>
                : <>Via: <span style={{ color: "#8aadcc" }}>{state.method.replace(/_/g, " + ")}</span> · Dual layer active</>
              }
            </div>
          )}
          {!isActive && lastCheck && (
            <div>Orders flowing normally · checked {timeAgo(lastCheck.toISOString())}</div>
          )}
        </div>
      </div>

      {/* ── Button ─────────────────────────────────────────── */}
      <button
        onClick={toggle}
        disabled={isLoading}
        className="w-full font-mono font-bold text-sm rounded transition-all mt-2"
        style={{
          height:  44,
          cursor:  isLoading ? "not-allowed" : "pointer",
          opacity: isLoading ? 0.55 : 1,
          letterSpacing: "0.07em",
          background: isActive
            ? "linear-gradient(135deg, rgba(0,255,136,0.15) 0%, rgba(0,255,136,0.08) 100%)"
            : "linear-gradient(135deg, rgba(255,45,85,0.22) 0%, rgba(255,45,85,0.12) 100%)",
          border: `1px solid ${isActive ? "rgba(0,255,136,0.45)" : "rgba(255,45,85,0.6)"}`,
          color:  isActive ? "#00FF88" : "#FF2D55",
          boxShadow: isActive
            ? "0 0 16px rgba(0,255,136,0.15)"
            : "0 0 24px rgba(255,45,85,0.25), inset 0 1px 0 rgba(255,45,85,0.15)",
          transform: "scale(1)",
          transition: "all 0.15s ease",
        }}
        onMouseEnter={(e) => { if (!isLoading) (e.currentTarget as HTMLButtonElement).style.transform = "scale(1.01)"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.transform = "scale(1)"; }}
      >
        {isLoading
          ? "◈ PROCESSING..."
          : isActive
          ? "▶ RESUME TRADING"
          : "◉ ACTIVATE EMERGENCY HALT"}
      </button>

      <div className="mt-1.5 text-[9px] text-[#2a4a6a] text-center font-mono">
        {isActive
          ? `Kill flag active · ${state?.method ?? "api"}`
          : "Triggers Redis flag + file-based kill simultaneously"}
      </div>
    </div>
  );
}
