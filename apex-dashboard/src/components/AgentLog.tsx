"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { formatDistanceToNow } from "date-fns";

// ── Types ────────────────────────────────────────────────────────────────────
export interface AgentLogEntry {
  id:        string;
  type:      AgentLogType;
  details:   string;
  symbol?:   string;
  price?:    number;
  timestamp: string;
}

export type AgentLogType =
  | "SIGNAL"
  | "RISK_PASS"
  | "RISK_FAIL"
  | "SUBMITTED"
  | "FILLED"
  | "REJECTED"
  | "ENGINE"
  | "EVALUATED"
  | "MARKET"
  | "KILLSWITCH";

// ── Type config ──────────────────────────────────────────────────────────────
const TYPE_CONFIG: Record<AgentLogType, {
  icon:        string;
  color:       string;
  borderColor: string;
}> = {
  SIGNAL:     { icon: "▲",  color: "#00D4FF",  borderColor: "#00D4FF" },
  RISK_PASS:  { icon: "✓",  color: "#00FF88",  borderColor: "#00FF88" },
  RISK_FAIL:  { icon: "✗",  color: "#FF2D55",  borderColor: "#FF2D55" },
  SUBMITTED:  { icon: "→",  color: "#00D4FF",  borderColor: "#00D4FF" },
  FILLED:     { icon: "✓",  color: "#00FF88",  borderColor: "#00FF88" },
  REJECTED:   { icon: "✗",  color: "#FF2D55",  borderColor: "#FF2D55" },
  ENGINE:     { icon: "⚡", color: "#FFB800",  borderColor: "#FFB800" },
  EVALUATED:  { icon: "◆",  color: "#4a6a8a",  borderColor: "#2a4a6a" },
  MARKET:     { icon: "●",  color: "#00D4FF",  borderColor: "#00D4FF" },
  KILLSWITCH: { icon: "🛡", color: "#FF2D55",  borderColor: "#FF2D55" },
};

// ── Single log row ────────────────────────────────────────────────────────────
function LogRow({ entry, isNew }: { entry: AgentLogEntry; isNew: boolean }) {
  const cfg  = TYPE_CONFIG[entry.type] ?? TYPE_CONFIG.EVALUATED;
  const time = (() => {
    try {
      return new Date(entry.timestamp).toLocaleTimeString("en-US", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
      });
    } catch { return "--:--:--"; }
  })();

  return (
    <div
      className={`flex items-start gap-2 px-3 py-2 transition-all hover:opacity-90 cursor-default
        ${isNew ? "animate-slide-in-right animate-log-new" : ""}`}
      style={{
        borderLeft:   `2px solid ${cfg.borderColor}33`,
        borderBottom: "1px solid rgba(0,212,255,0.04)",
        background:   "rgba(0,0,0,0.15)",
      }}
    >
      {/* Timestamp */}
      <span className="font-mono text-[9px] shrink-0 mt-0.5" style={{ color: "#2a4a6a", minWidth: 56 }}>
        {time}
      </span>

      {/* Icon + type */}
      <span className="font-mono text-[10px] font-bold shrink-0" style={{ color: cfg.color, minWidth: 64 }}>
        {cfg.icon} {entry.type}
      </span>

      {/* Details */}
      <span className="font-mono text-[10px]" style={{ color: "#8aadcc", lineHeight: "1.4" }}>
        {entry.details}
        {entry.symbol && (
          <span className="ml-1 font-bold" style={{ color: "#e2f0ff" }}>{entry.symbol}</span>
        )}
        {entry.price != null && (
          <span className="ml-1 tabular-nums" style={{ color: "#00D4FF" }}>@ ${entry.price.toFixed(2)}</span>
        )}
      </span>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
interface AgentLogProps {
  maxEntries?: number;
  compact?:    boolean;
}

export default function AgentLog({ maxEntries = 50, compact = false }: AgentLogProps) {
  const [entries,    setEntries]    = useState<AgentLogEntry[]>([]);
  const [newIds,     setNewIds]     = useState<Set<string>>(new Set());
  const [loading,    setLoading]    = useState(true);
  const [connected,  setConnected]  = useState(false);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());
  const eventSourceRef = useRef<EventSource | null>(null);
  const mountedRef     = useRef(true);
  const lastIdRef      = useRef<string>("");

  const fetchInitial = useCallback(async () => {
    try {
      const res  = await fetch("/api/agent-log?limit=50", { cache: "no-store" });
      const data = await res.json() as { entries: AgentLogEntry[] };
      if (mountedRef.current && data.entries) {
        setEntries(data.entries.slice(0, maxEntries));
        if (data.entries[0]) lastIdRef.current = data.entries[0].id;
        setLastUpdate(new Date());
      }
    } catch { /* silent */ } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [maxEntries]);

  // ── SSE connection ─────────────────────────────────────────────────────────
  const connectSSE = useCallback(() => {
    if (typeof window === "undefined") return;

    try {
      const es = new EventSource("/api/agent-log/stream");
      eventSourceRef.current = es;

      es.onopen = () => { if (mountedRef.current) setConnected(true); };

      es.onmessage = (ev) => {
        if (!mountedRef.current) return;
        try {
          const newEntries = JSON.parse(ev.data as string) as AgentLogEntry[];
          if (!newEntries.length) return;

          setNewIds(prev => {
            const next = new Set(prev);
            for (const e of newEntries) next.add(e.id);
            // Clear after animation
            setTimeout(() => {
              setNewIds(old => {
                const c = new Set(old);
                for (const e of newEntries) c.delete(e.id);
                return c;
              });
            }, 2000);
            return next;
          });

          setEntries(prev => {
            const combined = [...newEntries, ...prev];
            const seen     = new Set<string>();
            return combined.filter(e => {
              if (seen.has(e.id)) return false;
              seen.add(e.id);
              return true;
            }).slice(0, maxEntries);
          });

          setLastUpdate(new Date());
        } catch { /* ignore */ }
      };

      es.onerror = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        es.close();
        // Reconnect after 5s
        setTimeout(connectSSE, 5_000);
      };
    } catch { /* SSE not available */ }
  }, [maxEntries]);

  useEffect(() => {
    mountedRef.current = true;
    fetchInitial();
    connectSSE();
    return () => {
      mountedRef.current = false;
      eventSourceRef.current?.close();
    };
  }, [fetchInitial, connectSSE]);

  const handleClear = () => setEntries([]);

  const displayedEntries = entries.slice(0, compact ? 10 : maxEntries);

  return (
    <div className="apex-card p-0 overflow-hidden flex flex-col" style={{ maxHeight: compact ? 320 : 520 }}>
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-3 py-2 shrink-0"
        style={{ borderBottom: "1px solid rgba(0,212,255,0.1)" }}>
        <div className="flex items-center gap-2">
          <span className="font-heading text-xs font-bold uppercase tracking-widest" style={{ color: "#e2f0ff" }}>
            Agent Activity
          </span>
          <span className="font-mono text-[9px] text-[#4a6a8a]">
            {entries.length} events
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[9px] flex items-center gap-1" style={{ color: connected ? "#00FF88" : "#4a6a8a" }}>
            <span className={`w-1.5 h-1.5 rounded-full inline-block ${connected ? "animate-blink" : ""}`}
              style={{ background: connected ? "#00FF88" : "#4a6a8a" }} />
            {connected ? "LIVE" : "POLLING"}
          </span>
          <button
            onClick={handleClear}
            className="font-mono text-[9px] uppercase tracking-wider px-2 py-0.5 rounded hover:opacity-80 transition-opacity"
            style={{ color: "#4a6a8a", border: "1px solid rgba(74,106,138,0.2)" }}
          >
            CLEAR
          </button>
        </div>
      </div>

      {/* ── Sub-header ─────────────────────────────────────────────────── */}
      <div className="px-3 py-1 shrink-0" style={{ borderBottom: "1px solid rgba(0,212,255,0.05)" }}>
        <span className="font-mono text-[9px] text-[#2a4a6a]">
          Last 24h · updated {formatDistanceToNow(lastUpdate, { addSuffix: true })}
        </span>
      </div>

      {/* ── Entries ────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-8 rounded animate-pulse" style={{ background: "rgba(0,212,255,0.04)" }} />
            ))}
          </div>
        ) : displayedEntries.length === 0 ? (
          <div className="py-8 text-center font-mono text-[10px] uppercase tracking-widest text-[#2a4a6a]">
            No activity recorded yet
          </div>
        ) : (
          displayedEntries.map(entry => (
            <LogRow key={entry.id} entry={entry} isNew={newIds.has(entry.id)} />
          ))
        )}
      </div>
    </div>
  );
}
