"use client";

import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

// ── Types ─────────────────────────────────────────────────────────────────
interface SignalScores {
  tft:   number;   // 0-100 — TFT model confidence
  rsi:   number;   // 0-100 — RSI signal strength
  ema:   number;   // 0-100 — EMA trend alignment
  macd:  number;   // 0-100 — MACD momentum
  stoch: number;   // 0-100 — Stochastics
  sent:  number;   // 0-100 — Sentiment score
}

interface Props {
  symbol:  string;
  scores?: Partial<SignalScores>;
  height?: number;
  compact?: boolean;
}

// ── Defaults ──────────────────────────────────────────────────────────────
const DEFAULT_SCORES: SignalScores = {
  tft:   82,
  rsi:   61,
  ema:   74,
  macd:  58,
  stoch: 45,
  sent:  70,
};

function composite(s: SignalScores): number {
  return (s.tft * 0.40 + s.rsi * 0.15 + s.ema * 0.20 + s.macd * 0.10 + s.stoch * 0.05 + s.sent * 0.10);
}

function scoreColor(v: number): string {
  if (v >= 70) return "#00FF88";
  if (v >= 50) return "#00D4FF";
  if (v >= 35) return "#FFB800";
  return "#FF2D55";
}

// ── Custom radar dot ──────────────────────────────────────────────────────
function RadarDot(props: { cx?: number; cy?: number; value?: number }) {
  const { cx = 0, cy = 0, value = 0 } = props;
  const color = scoreColor(value);
  return <circle cx={cx} cy={cy} r={3} fill={color} stroke={color} strokeWidth={1} />;
}

// ── Main component ─────────────────────────────────────────────────────────
export default function SignalRadar({ symbol, scores, height = 260, compact = false }: Props) {
  const merged: SignalScores = { ...DEFAULT_SCORES, ...scores };
  const comp    = composite(merged);
  const color   = scoreColor(comp);

  const data = [
    { axis: "TFT",   value: merged.tft   },
    { axis: "RSI",   value: merged.rsi   },
    { axis: "EMA",   value: merged.ema   },
    { axis: "MACD",  value: merged.macd  },
    { axis: "STOCH", value: merged.stoch },
    { axis: "SENT",  value: merged.sent  },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <div>
          <span className="text-xs font-bold text-gray-400 uppercase tracking-widest">Signal Radar</span>
          <span className="ml-2 text-xs font-mono text-white">{symbol}</span>
        </div>
        <div className="text-right">
          <div className="text-lg font-bold" style={{ color }}>{comp.toFixed(0)}</div>
          <div className="text-[9px] text-gray-600">composite</div>
        </div>
      </div>

      {/* Radar */}
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={data} outerRadius={compact ? 65 : 90}>
          <PolarGrid stroke="#1e2d40" />
          <PolarAngleAxis
            dataKey="axis"
            tick={{ fill: "#9ca3af", fontSize: compact ? 9 : 11 }}
          />
          <Tooltip
            contentStyle={{ background: "#0d1520", border: "1px solid #1e2d40", fontSize: 11 }}
            formatter={(v: number) => [v.toFixed(0), "Score"]}
          />
          <Radar
            dataKey="value"
            fill={color}
            fillOpacity={0.12}
            stroke={color}
            strokeWidth={1.5}
            dot={<RadarDot />}
          />
        </RadarChart>
      </ResponsiveContainer>

      {/* Scores grid */}
      {!compact && (
        <div className="grid grid-cols-3 gap-1 mt-1">
          {data.map(d => (
            <div key={d.axis} className="text-center">
              <div className="text-[9px] text-gray-600 uppercase">{d.axis}</div>
              <div
                className="text-xs font-bold"
                style={{ color: scoreColor(d.value) }}
              >
                {d.value}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
