"use client";

import { useCallback, useEffect, useState } from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ModelHealth {
  healthy:     boolean;
  weight:      number;
  latency_ms:  number;
  error_count: number;
}

interface EnsembleData {
  healthy:                  boolean;
  degraded_mode:            boolean;
  halt_active:              boolean;
  models:                   { tft: ModelHealth; xgb: ModelHealth; lstm: ModelHealth };
  current_weights:          { tft: number; xgb: number; lstm: number };
  next_optimize_at:         string;
  recent_predictions_count: number;
  _mock?:                   boolean;
}

const MODEL_META: Record<string, { label: string; color: string; hex: string }> = {
  tft:  { label: "TFT",     color: "cyan",    hex: "#22d3ee" },
  xgb:  { label: "XGBoost", color: "purple",  hex: "#a855f7" },
  lstm: { label: "LSTM",    color: "amber",   hex: "#f59e0b" },
};

const DEFAULT_WEIGHTS = { tft: 0.45, xgb: 0.35, lstm: 0.20 };

// ─── Component ────────────────────────────────────────────────────────────────

export default function EnsembleWeights({ className = "" }: { className?: string }) {
  const [data,        setData]        = useState<EnsembleData | null>(null);
  const [editMode,    setEditMode]    = useState(false);
  const [editWeights, setEditWeights] = useState({ tft: 45, xgb: 35, lstm: 20 });
  const [saving,      setSaving]      = useState(false);
  const [optimizing,  setOptimizing]  = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/ensemble", { cache: "no-store" });
      if (res.ok) {
        const d = await res.json() as EnsembleData;
        setData(d);
        const w = d.current_weights;
        setEditWeights({ tft: Math.round(w.tft * 100), xgb: Math.round(w.xgb * 100), lstm: Math.round(w.lstm * 100) });
      }
    } catch (_) {}
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const editSum = editWeights.tft + editWeights.xgb + editWeights.lstm;

  const handleSave = async () => {
    if (Math.abs(editSum - 100) > 0.5) return;
    setSaving(true);
    try {
      await fetch("/api/ensemble", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ weights: { tft: editWeights.tft / 100, xgb: editWeights.xgb / 100, lstm: editWeights.lstm / 100 } }),
      });
      await load();
      setEditMode(false);
    } catch (_) {}
    setSaving(false);
  };

  const handleReset = async () => {
    setSaving(true);
    try {
      await fetch("/api/ensemble", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "reset" }) });
      await load();
      setEditMode(false);
    } catch (_) {}
    setSaving(false);
  };

  const handleOptimize = async () => {
    setOptimizing(true);
    try {
      await fetch("/api/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_type: "ensemble", triggered_by: "optimize_now" }) });
      setTimeout(load, 2000);
    } catch (_) {}
    setOptimizing(false);
  };

  const weights  = data?.current_weights ?? DEFAULT_WEIGHTS;
  const models   = data?.models ?? {
    tft:  { healthy: true,  weight: 0.45, latency_ms: 42, error_count: 0 },
    xgb:  { healthy: true,  weight: 0.35, latency_ms: 12, error_count: 0 },
    lstm: { healthy: true,  weight: 0.20, latency_ms: 38, error_count: 0 },
  };
  const degraded       = data?.degraded_mode ?? false;
  const halt           = data?.halt_active   ?? false;
  const degradedModels = Object.entries(models).filter(([, m]) => !m.healthy).map(([k]) => k);

  const chartData = Object.entries(weights).map(([k, v]) => ({
    name:  MODEL_META[k]?.label ?? k,
    value: Math.round(v * 100),
    hex:   MODEL_META[k]?.hex ?? "#888",
    key:   k,
  }));

  const nextOpt = data?.next_optimize_at ? new Date(data.next_optimize_at) : null;

  return (
    <div className={`rounded-2xl border bg-gray-900/60 flex flex-col ${
      halt    ? "border-red-500/40 shadow-[0_0_20px_rgba(239,68,68,0.1)]" :
      degraded ? "border-amber-500/40 shadow-[0_0_20px_rgba(245,158,11,0.08)]" :
                 "border-cyan-500/20"
    } ${className}`}>

      {/* ─── Header ───────────────────────────────────────────────── */}
      <div className={`px-4 py-3 rounded-t-2xl flex items-center justify-between border-b ${
        halt     ? "bg-red-950/30 border-red-500/20" :
        degraded ? "bg-amber-950/30 border-amber-500/20" :
                   "bg-gray-800/30 border-cyan-500/10"
      }`}>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${halt ? "bg-red-400" : degraded ? "bg-amber-400 animate-pulse" : "bg-cyan-400 animate-pulse"}`} />
          <span className={`text-xs font-semibold uppercase tracking-widest ${halt ? "text-red-400" : degraded ? "text-amber-400" : "text-cyan-400"}`}>
            {halt ? "🛑 HALT ACTIVE" : degraded ? `⚠ DEGRADED — ${3 - degradedModels.length}/3 models` : "ENSEMBLE WEIGHTS"}
          </span>
          {data?._mock && <span className="text-xs text-gray-600">(mock)</span>}
        </div>
        <div className="flex items-center gap-1.5">
          {!editMode && (
            <button
              onClick={() => setEditMode(true)}
              className="px-2 py-0.5 text-xs rounded-lg bg-gray-700/40 text-gray-400 border border-gray-600/30 hover:bg-gray-600/40 transition-colors"
            >
              Edit
            </button>
          )}
        </div>
      </div>

      {/* ─── Body ─────────────────────────────────────────────────── */}
      <div className="p-4 flex gap-4 flex-col sm:flex-row">

        {/* Donut chart */}
        <div className="w-28 h-28 flex-shrink-0 mx-auto sm:mx-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={chartData} cx="50%" cy="50%" innerRadius={28} outerRadius={52} paddingAngle={2} dataKey="value" strokeWidth={0}>
                {chartData.map((d) => (
                  <Cell key={d.key} fill={models[d.key as keyof typeof models]?.healthy ? d.hex : "#374151"} opacity={models[d.key as keyof typeof models]?.healthy ? 1 : 0.4} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, fontSize: 11 }}
                formatter={(v: number, name: string) => [`${v}%`, name]}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Model rows */}
        <div className="flex-1 space-y-2.5 min-w-0">
          {(["tft", "xgb", "lstm"] as const).map((key) => {
            const meta    = MODEL_META[key];
            const health  = models[key];
            const pct     = Math.round((weights[key] ?? 0) * 100);
            const healthy = health?.healthy ?? true;

            return (
              <div key={key} className={`space-y-1 ${healthy ? "" : "opacity-50"}`}>
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full ${healthy ? `bg-${meta.color}-400` : "bg-gray-600"}`} />
                    <span className={`font-medium ${healthy ? `text-${meta.color}-400` : "text-gray-500"}`}>{meta.label}</span>
                    {!healthy && <span className="text-gray-600 text-[10px]">DEGRADED</span>}
                  </div>
                  <div className="flex items-center gap-2 text-gray-500 font-mono">
                    <span>{health?.latency_ms?.toFixed(0) ?? "–"}ms</span>
                    <span className={healthy ? `text-${meta.color}-400` : "text-gray-600"}>{pct}%</span>
                  </div>
                </div>
                <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                  <div
                    style={{ width: `${pct}%`, background: healthy ? meta.hex : "#374151" }}
                    className="h-full rounded-full transition-all duration-500"
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ─── Manual Weight Editor ─────────────────────────────────── */}
      {editMode && (
        <div className="mx-4 mb-3 p-3 rounded-xl border border-gray-700/40 bg-gray-800/30 space-y-3">
          <p className="text-xs text-gray-500 uppercase tracking-wider">Manual Weight Override</p>
          <div className="grid grid-cols-3 gap-2">
            {(["tft", "xgb", "lstm"] as const).map((key) => (
              <div key={key} className="space-y-1">
                <label className={`text-xs text-${MODEL_META[key].color}-400`}>{MODEL_META[key].label} %</label>
                <input
                  type="number"
                  min={1}
                  max={90}
                  value={editWeights[key]}
                  onChange={(e) => setEditWeights((prev) => ({ ...prev, [key]: +e.target.value }))}
                  className="w-full bg-gray-900 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-white font-mono text-center focus:outline-none focus:border-cyan-500"
                />
              </div>
            ))}
          </div>
          <div className={`text-xs text-center font-mono ${Math.abs(editSum - 100) > 0.5 ? "text-red-400" : "text-emerald-400"}`}>
            Sum: {editSum}% {Math.abs(editSum - 100) > 0.5 ? "⚠ must equal 100%" : "✓"}
          </div>
          <div className="flex gap-2">
            <button onClick={() => setEditMode(false)} className="flex-1 py-1.5 text-xs rounded-lg bg-gray-700/40 text-gray-400 border border-gray-600/30 hover:bg-gray-600/40 transition-colors">Cancel</button>
            <button onClick={handleReset}  className="flex-1 py-1.5 text-xs rounded-lg bg-gray-700/40 text-gray-400 border border-gray-600/30 hover:bg-gray-600/40 transition-colors" disabled={saving}>Reset</button>
            <button onClick={handleSave}   className="flex-1 py-1.5 text-xs rounded-lg bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/20 transition-colors disabled:opacity-50" disabled={saving || Math.abs(editSum - 100) > 0.5}>{saving ? "Saving…" : "Save"}</button>
          </div>
        </div>
      )}

      {/* ─── Footer ───────────────────────────────────────────────── */}
      <div className="px-4 pb-3 flex items-center justify-between text-xs text-gray-600 border-t border-gray-800/50 pt-2 mt-auto">
        <span className="font-mono">
          {nextOpt ? `Next opt: ${nextOpt.toLocaleDateString("en", { weekday: "short", month: "short", day: "numeric" })}` : "Optimize weekly"}
        </span>
        <button
          onClick={handleOptimize}
          disabled={optimizing}
          className="px-2 py-0.5 rounded-lg bg-gray-700/40 text-gray-400 border border-gray-600/30 hover:bg-gray-600/40 transition-colors disabled:opacity-50"
        >
          {optimizing ? "⏳" : "⚡ OPTIMIZE NOW"}
        </button>
      </div>
    </div>
  );
}
