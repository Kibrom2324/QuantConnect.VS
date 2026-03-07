"use client";

import { useEffect, useState, useCallback } from "react";
import dynamic from "next/dynamic";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ModelVersion {
  model_id: string;
  model_type: "tft" | "xgb" | "lstm" | "ensemble";
  version: string;
  status: "live" | "staging" | "training" | "retired";
  val_sharpe: number;
  val_hit_rate: number;
  created_at: string;
  trained_by: string;
  mlflow_run_id?: string;
  auto_promoted?: boolean;
  component_models?: Record<string, string>;
}

interface ABTest {
  model_a_id: string;
  model_b_id: string;
  weight_b: number;
  started_at: string;
  metrics_a: { trades: number; sharpe: number; pnl: number };
  metrics_b: { trades: number; sharpe: number; pnl: number };
  status: string;
}

interface ModelsData {
  models: ModelVersion[];
  grouped: Record<string, ModelVersion[]>;
  live_model: ModelVersion | null;
  ab_test_active: ABTest | null;
  alerts_count: number;
  _mock?: boolean;
}

interface Schedule {
  daily_retrain?: { enabled: boolean; cron: string; description: string };
  weekly_optimize?: { enabled: boolean; cron: string; description: string };
  hourly_monitor?: { enabled: boolean; cron: string; description: string };
  auto_promote?: { enabled: boolean; threshold_sharpe: number; threshold_hit_rate: number; improvement_pct: number };
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  live:     "text-emerald-400 bg-emerald-500/10 border-emerald-500/40",
  staging:  "text-cyan-400   bg-cyan-500/10    border-cyan-500/40",
  training: "text-amber-400  bg-amber-500/10   border-amber-500/40",
  retired:  "text-gray-500   bg-gray-700/30    border-gray-600/30",
};

const TYPE_ACCENT: Record<string, { color: string; label: string; icon: string }> = {
  tft:      { color: "cyan",    label: "TFT",      icon: "🧠" },
  xgb:      { color: "purple",  label: "XGBoost",  icon: "⚡" },
  lstm:     { color: "amber",   label: "LSTM",     icon: "🔁" },
  ensemble: { color: "emerald", label: "Ensemble", icon: "🎯" },
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLOR[status] ?? "text-gray-400 bg-gray-700/30 border-gray-600/30";
  return (
    <span className={`px-2 py-0.5 rounded border text-xs font-mono uppercase tracking-wider ${cls}`}>
      {status}
    </span>
  );
}

// ─── Model Card ───────────────────────────────────────────────────────────────

function ModelCard({
  model,
  onPromote,
  onDemote,
}: {
  model: ModelVersion;
  onPromote: (id: string) => void;
  onDemote: (id: string) => void;
}) {
  const info = TYPE_ACCENT[model.model_type] ?? TYPE_ACCENT.ensemble;
  const c    = info.color;

  return (
    <div className={`rounded-xl border bg-gray-900/60 p-4 flex flex-col gap-3
      border-${c}-500/20 hover:border-${c}-400/50 transition-all`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-lg">{info.icon}</span>
          <div>
            <p className={`text-sm font-semibold text-${c}-400`}>{model.model_id}</p>
            <p className="text-xs text-gray-500">{info.label} v{model.version}</p>
          </div>
        </div>
        <StatusBadge status={model.status} />
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="bg-gray-800/50 rounded-lg p-2 text-center">
          <p className="text-gray-500 mb-0.5">Val Sharpe</p>
          <p className={`font-mono font-bold text-base text-${c}-400`}>{model.val_sharpe.toFixed(2)}</p>
        </div>
        <div className="bg-gray-800/50 rounded-lg p-2 text-center">
          <p className="text-gray-500 mb-0.5">Hit Rate</p>
          <p className={`font-mono font-bold text-base text-${c}-400`}>{model.val_hit_rate.toFixed(1)}%</p>
        </div>
      </div>

      <div className="text-xs text-gray-600 font-mono truncate">
        Trained by {model.trained_by} · {new Date(model.created_at).toLocaleDateString()}
        {model.auto_promoted && <span className="ml-1 text-emerald-600">⬆ auto</span>}
      </div>

      <div className="flex gap-2 mt-auto">
        {model.status === "staging" && (
          <button
            onClick={() => onPromote(model.model_id)}
            className={`flex-1 py-1.5 rounded-lg text-xs font-medium
              bg-${c}-500/10 text-${c}-400 border border-${c}-500/30
              hover:bg-${c}-500/20 transition-colors`}
          >
            ⬆ PROMOTE LIVE
          </button>
        )}
        {model.status === "live" && (
          <button
            onClick={() => onDemote(model.model_id)}
            className="flex-1 py-1.5 rounded-lg text-xs font-medium
              bg-red-500/10 text-red-400 border border-red-500/30
              hover:bg-red-500/20 transition-colors"
          >
            ⬇ DEMOTE
          </button>
        )}
        {model.mlflow_run_id && (
          <a
            href={`${process.env.NEXT_PUBLIC_MLFLOW_URL ?? 'http://localhost:5001'}/#/experiments/0/runs/${model.mlflow_run_id}`}
            target="_blank"
            rel="noreferrer"
            className="px-3 py-1.5 rounded-lg text-xs font-medium
              bg-gray-700/40 text-gray-400 border border-gray-600/30
              hover:bg-gray-600/40 transition-colors"
          >
            MLflow ↗
          </a>
        )}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ModelsPage() {
  const [data,        setData]        = useState<ModelsData | null>(null);
  const [schedule,    setSchedule]    = useState<Schedule | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [trainTarget, setTrainTarget] = useState<string>("ensemble");
  const [trainBusy,   setTrainBusy]   = useState(false);
  const [abDialog,    setAbDialog]    = useState(false);
  const [abModelA,    setAbModelA]    = useState("");
  const [abModelB,    setAbModelB]    = useState("");
  const [abWeightB,   setAbWeightB]   = useState(0.2);
  const [confirmDemote, setConfirmDemote] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [mRes, sRes] = await Promise.all([
        fetch("/api/models", { cache: "no-store" }),
        fetch("/api/models/schedule", { cache: "no-store" }),
      ]);
      if (mRes.ok) {
        const mData = await mRes.json();
        // Auto-seed when the registry is empty (first run or Redis was flushed)
        if (!mData.models || mData.models.length === 0) {
          try {
            const seedRes = await fetch("/api/models/seed", { method: "POST" });
            if (seedRes.ok) {
              const sd = await seedRes.json();
              setData({ ...mData, models: sd.models, live_model: sd.live_model, grouped: sd.grouped ?? mData.grouped, _mock: sd._mock });
            } else {
              setData(mData);
            }
          } catch (_) { setData(mData); }
        } else {
          setData(mData);
        }
      }
      if (sRes.ok) { const s = await sRes.json(); setSchedule(s.schedule); }
    } catch (_) {}
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); const t = setInterval(fetchData, 30000); return () => clearInterval(t); }, [fetchData]);

  const handlePromote = async (modelId: string) => {
    await fetch(`/api/models/${modelId}/promote`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ promoted_by: "manual" }) });
    fetchData();
  };

  const handleDemote = async (modelId: string) => {
    setConfirmDemote(modelId);
  };

  const confirmDemoteAction = async () => {
    if (!confirmDemote) return;
    await fetch(`/api/models/${confirmDemote}/demote`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "manual demote" }) });
    setConfirmDemote(null);
    fetchData();
  };

  const handleTriggerTrain = async () => {
    setTrainBusy(true);
    await fetch("/api/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_type: trainTarget, triggered_by: "manual" }) });
    setTrainBusy(false);
    fetchData();
  };

  const handleStartAB = async () => {
    if (!abModelA || !abModelB) return;
    await fetch("/api/models/ab-test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "start", model_a_id: abModelA, model_b_id: abModelB, weight_b: abWeightB }) });
    setAbDialog(false);
    fetchData();
  };

  const handleStopAB = async (winnerId?: string) => {
    await fetch("/api/models/ab-test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "stop", winner_id: winnerId }) });
    fetchData();
  };

  const toggleSchedule = async (key: string, enabled: boolean) => {
    const updated = { ...schedule, [key]: { ...(schedule as Record<string, unknown>)[key] as object, enabled } };
    setSchedule(updated as Schedule);
    await fetch("/api/models/schedule", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [key]: { ...(schedule as Record<string, unknown>)?.[key] as object, enabled } }) });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-cyan-400 font-mono text-sm animate-pulse">Loading model registry…</div>
      </div>
    );
  }

  const live      = data?.live_model;
  const grouped   = data?.grouped ?? { tft: [], xgb: [], lstm: [], ensemble: [] };
  const abTest    = data?.ab_test_active;

  return (
    <div className="space-y-6 p-6">

      {/* ─── Header ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">MODEL REGISTRY</h1>
          <p className="text-xs text-gray-500 mt-0.5 font-mono">
            {data?._mock ? "⚠ MOCK DATA — Redis offline" : `${data?.models.length ?? 0} models · ${data?.alerts_count ?? 0} alerts`}
          </p>
        </div>
        {!abTest && live && (
          <button
            onClick={() => setAbDialog(true)}
            className="px-4 py-2 rounded-lg text-xs font-medium bg-purple-500/10 text-purple-400 border border-purple-500/30 hover:bg-purple-500/20 transition-colors"
          >
            ⚗ LAUNCH A/B TEST
          </button>
        )}
      </div>

      {/* ─── Live Model Hero ─────────────────────────────────────────── */}
      {live && (
        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-5
          shadow-[0_0_30px_rgba(16,185,129,0.07)]">
          <div className="flex items-start justify-between mb-4">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-xs text-emerald-500 font-mono uppercase tracking-widest">LIVE MODEL</span>
              </div>
              <p className="text-2xl font-bold text-white">{live.model_id}</p>
              <p className="text-sm text-gray-400 mt-0.5">
                {TYPE_ACCENT[live.model_type]?.label} v{live.version} · promoted by {live.trained_by}
              </p>
            </div>
            <StatusBadge status="live" />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: "Sharpe", value: live.val_sharpe.toFixed(2) },
              { label: "Hit Rate", value: `${live.val_hit_rate.toFixed(1)}%` },
              { label: "Type", value: live.model_type.toUpperCase() },
              { label: "Deployed", value: new Date(live.created_at).toLocaleDateString() },
            ].map((s) => (
              <div key={s.label} className="bg-emerald-900/20 rounded-xl p-3 text-center border border-emerald-500/10">
                <p className="text-xs text-emerald-700 mb-1">{s.label}</p>
                <p className="text-base font-mono font-bold text-emerald-400">{s.value}</p>
              </div>
            ))}
          </div>
          {live.component_models && (
            <div className="mt-3 text-xs text-gray-600 font-mono flex gap-3">
              {Object.entries(live.component_models).map(([k, v]) => (
                <span key={k} className="bg-gray-800/50 px-2 py-0.5 rounded">{k.toUpperCase()}: {v}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ─── A/B Test Panel ──────────────────────────────────────────── */}
      {abTest && (
        <div className="rounded-xl border border-purple-500/30 bg-purple-950/20 p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-purple-400">⚗</span>
              <span className="text-sm font-semibold text-purple-400 uppercase tracking-wider">A/B Test Active</span>
              <span className="text-xs text-gray-500 font-mono">since {new Date(abTest.started_at).toLocaleTimeString()}</span>
            </div>
            <div className="flex gap-2">
              <button onClick={() => handleStopAB(abTest.model_a_id)} className="px-3 py-1 text-xs rounded-lg bg-gray-700/40 text-gray-300 border border-gray-600/30 hover:bg-gray-600/40">A wins</button>
              <button onClick={() => handleStopAB(abTest.model_b_id)} className="px-3 py-1 text-xs rounded-lg bg-gray-700/40 text-gray-300 border border-gray-600/30 hover:bg-gray-600/40">B wins</button>
              <button onClick={() => handleStopAB()} className="px-3 py-1 text-xs rounded-lg bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20">Stop</button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs font-mono">
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-gray-500 mb-1">Model A — {abTest.model_a_id} ({((1 - abTest.weight_b) * 100).toFixed(0)}% traffic)</p>
              <div className="h-2 bg-gray-700 rounded-full"><div style={{ width: `${(1 - abTest.weight_b) * 100}%` }} className="h-2 bg-emerald-500 rounded-full" /></div>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-gray-500 mb-1">Model B — {abTest.model_b_id} ({(abTest.weight_b * 100).toFixed(0)}% traffic)</p>
              <div className="h-2 bg-gray-700 rounded-full"><div style={{ width: `${abTest.weight_b * 100}%` }} className="h-2 bg-purple-500 rounded-full" /></div>
            </div>
          </div>
        </div>
      )}

      {/* ─── 4-Column Model Grids ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {(["ensemble", "tft", "xgb", "lstm"] as const).map((type) => {
          const models = grouped[type] ?? [];
          const info   = TYPE_ACCENT[type];
          return (
            <div key={type} className="space-y-3">
              <div className="flex items-center gap-2">
                <span>{info.icon}</span>
                <span className={`text-xs font-semibold uppercase tracking-wider text-${info.color}-400`}>{info.label}</span>
                <span className="text-xs text-gray-600">({models.length})</span>
              </div>
              {models.length === 0 ? (
                <div className="rounded-xl border border-gray-700/40 bg-gray-900/40 p-4 text-center text-xs text-gray-600">
                  No models
                </div>
              ) : (
                models.slice(0, 3).map((m) => (
                  <ModelCard key={m.model_id} model={m} onPromote={handlePromote} onDemote={handleDemote} />
                ))
              )}
            </div>
          );
        })}
      </div>

      {/* ─── Training Controls ────────────────────────────────────────── */}
      <div className="rounded-xl border border-gray-700/40 bg-gray-900/60 p-4">
        <h3 className="text-sm font-semibold text-white mb-4 uppercase tracking-wider">Training Schedule & Controls</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

          {/* Schedule toggles */}
          <div className="space-y-3">
            {schedule && Object.entries(schedule)
              .filter(([k]) => k !== "auto_promote")
              .map(([key, cfg]) => {
                const s = cfg as { enabled: boolean; description: string };
                return (
                  <div key={key} className="flex items-center justify-between bg-gray-800/40 rounded-lg px-3 py-2">
                    <span className="text-xs text-gray-400">{s.description}</span>
                    <button
                      onClick={() => toggleSchedule(key, !s.enabled)}
                      className={`w-10 h-5 rounded-full transition-colors ${s.enabled ? "bg-cyan-500" : "bg-gray-700"}`}
                    >
                      <span className={`block w-4 h-4 rounded-full bg-white shadow transition-transform mx-0.5 ${s.enabled ? "translate-x-5" : "translate-x-0"}`} />
                    </button>
                  </div>
                );
              })}
          </div>

          {/* Manual train trigger */}
          <div className="bg-gray-800/40 rounded-xl p-4 space-y-3">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Manual Retrain</p>
            <div className="flex gap-2">
              {(["tft", "xgb", "lstm", "ensemble"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTrainTarget(t)}
                  className={`flex-1 py-1.5 rounded-lg text-xs font-mono transition-colors
                    ${trainTarget === t
                      ? `bg-${TYPE_ACCENT[t].color}-500/20 text-${TYPE_ACCENT[t].color}-400 border border-${TYPE_ACCENT[t].color}-500/40`
                      : "bg-gray-700/40 text-gray-500 border border-gray-600/30 hover:text-gray-300"}`}
                >
                  {t.toUpperCase()}
                </button>
              ))}
            </div>
            <button
              onClick={handleTriggerTrain}
              disabled={trainBusy}
              className="w-full py-2.5 rounded-xl text-sm font-medium
                bg-cyan-500/10 text-cyan-400 border border-cyan-500/30
                hover:bg-cyan-500/20 disabled:opacity-50 transition-colors"
            >
              {trainBusy ? "Queuing…" : `▶ TRIGGER ${trainTarget.toUpperCase()} RETRAIN`}
            </button>
          </div>
        </div>
      </div>

      {/* ─── A/B Dialog ──────────────────────────────────────────────── */}
      {abDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
          <div className="w-96 rounded-2xl border border-purple-500/30 bg-gray-900 p-6 space-y-4">
            <h3 className="text-sm font-semibold text-purple-400 uppercase tracking-wider">Launch A/B Test</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500">Model A (champion)</label>
                <input value={abModelA} onChange={(e) => setAbModelA(e.target.value)} placeholder="e.g. ENS_v5" className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500" />
              </div>
              <div>
                <label className="text-xs text-gray-500">Model B (challenger)</label>
                <input value={abModelB} onChange={(e) => setAbModelB(e.target.value)} placeholder="e.g. ENS_v6" className="w-full mt-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-purple-500" />
              </div>
              <div>
                <label className="text-xs text-gray-500">Traffic to B: {(abWeightB * 100).toFixed(0)}%</label>
                <input type="range" min={5} max={50} step={5} value={abWeightB * 100} onChange={(e) => setAbWeightB(+e.target.value / 100)} className="w-full mt-1 accent-purple-500" />
              </div>
            </div>
            <div className="flex gap-3">
              <button onClick={() => setAbDialog(false)} className="flex-1 py-2 rounded-xl text-sm text-gray-400 bg-gray-800 hover:bg-gray-700 transition-colors">Cancel</button>
              <button onClick={handleStartAB} className="flex-1 py-2 rounded-xl text-sm font-medium text-purple-400 bg-purple-500/10 border border-purple-500/30 hover:bg-purple-500/20 transition-colors">Launch Test</button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Demote Confirm ──────────────────────────────────────────── */}
      {confirmDemote && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
          <div className="w-80 rounded-2xl border border-red-500/30 bg-gray-900 p-6 space-y-4">
            <h3 className="text-sm font-semibold text-red-400 uppercase tracking-wider">⚠ Demote {confirmDemote}?</h3>
            <p className="text-xs text-gray-400">This will retire the model and clear the active model pointer. There will be no live model until another is promoted.</p>
            <div className="flex gap-3">
              <button onClick={() => setConfirmDemote(null)} className="flex-1 py-2 rounded-xl text-sm text-gray-400 bg-gray-800 hover:bg-gray-700 transition-colors">Cancel</button>
              <button onClick={confirmDemoteAction} className="flex-1 py-2 rounded-xl text-sm font-medium text-red-400 bg-red-500/10 border border-red-500/30 hover:bg-red-500/20 transition-colors">Demote</button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
