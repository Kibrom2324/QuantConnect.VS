"use client";
import { useEffect, useState } from "react";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";
import { fetchWeights } from "@/lib/api";

const MODEL_CFG = [
  { key: "TFT",    color: "#00D4FF", label: "Temporal Fusion" },
  { key: "XGB",    color: "#8B5CF6", label: "XGBoost" },
  { key: "Factor", color: "#00FF88", label: "Factor Model" },
];

interface WeightEntry { name: string; value: number; color: string; label: string }

function CyberTooltip({ active, payload }: { active?: boolean; payload?: { payload: WeightEntry }[] }) {
  if (!active || !payload?.length) return null;
  const { name, value, color } = payload[0].payload;
  return (
    <div
      className="px-3 py-2 rounded font-mono text-xs"
      style={{ background: "#0a0f1e", border: `1px solid ${color}60`, boxShadow: `0 0 12px ${color}30` }}
    >
      <p style={{ color }}>{name}</p>
      <p className="text-apex-text font-bold">{(value * 100).toFixed(1)}%</p>
    </div>
  );
}

export default function EnsembleWeightsPie({ refreshKey }: { refreshKey: number }) {
  const [weights, setWeights] = useState({ TFT: 0.40, XGB: 0.35, Factor: 0.25 });

  useEffect(() => {
    fetchWeights()
      .then((w) => setWeights({ TFT: w.weights.TFT, XGB: w.weights.XGB, Factor: w.weights.Factor }))
      .catch(() => {});
  }, [refreshKey]);

  const data: WeightEntry[] = MODEL_CFG.map((m) => ({
    name:  m.key,
    value: (weights as Record<string, number>)[m.key] ?? 0,
    color: m.color,
    label: m.label,
  }));

  return (
    <div className="apex-card">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-apex-subtext font-mono">Ensemble Weights</h2>
        <span className="text-[9px] font-mono text-apex-subtext">Auto-rebalanced</span>
      </div>

      {/* Donut chart */}
      <ResponsiveContainer width="100%" height={150}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={42}
            outerRadius={65}
            paddingAngle={4}
            strokeWidth={0}
          >
            {data.map((entry, i) => (
              <Cell
                key={i}
                fill={entry.color}
                style={{ filter: `drop-shadow(0 0 6px ${entry.color}80)` }}
              />
            ))}
          </Pie>
          <Tooltip content={<CyberTooltip />} />
        </PieChart>
      </ResponsiveContainer>

      {/* Progress bars below chart */}
      <div className="flex flex-col gap-2 mt-1">
        {data.map((entry) => (
          <div key={entry.name} className="flex items-center gap-2">
            <span
              className="text-[9px] font-mono font-bold w-10 shrink-0"
              style={{ color: entry.color }}
            >
              {entry.name}
            </span>
            <div className="flex-1 h-1.5 rounded-full" style={{ background: "rgba(255,255,255,0.07)" }}>
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{
                  width: `${entry.value * 100}%`,
                  background: entry.color,
                  boxShadow: `0 0 6px ${entry.color}70`,
                }}
              />
            </div>
            <span
              className="text-[9px] font-mono w-8 text-right shrink-0"
              style={{ color: entry.color }}
            >
              {(entry.value * 100).toFixed(0)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
