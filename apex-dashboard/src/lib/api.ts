// ─── Client-side API helper functions ────────────────────────────────────────
// These call our own Next.js /api/* routes (BFF), never third-party APIs directly.

import type {
  ServiceHealth, KillSwitchState, PnLToday,
  Position, Signal, EnsembleWeights,
  BacktestFile, BacktestResult, MLflowExperiment,
} from "./types";

const BASE = "";  // same origin

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
export const fetchServiceHealth  = ()  => get<ServiceHealth>("/api/health");
export const fetchKillSwitch     = ()  => get<KillSwitchState>("/api/kill-switch");
export const enableKillSwitch    = ()  => post<KillSwitchState>("/api/kill-switch/enable");
export const disableKillSwitch   = ()  => post<KillSwitchState>("/api/kill-switch/disable");
export const fetchPnL            = ()  => get<PnLToday>("/api/pnl");
export const fetchPositions      = ()  => get<{ positions: Position[] }>("/api/positions");
export const fetchRecentSignals  = (limit = 10) => get<{ signals: Signal[] }>(`/api/signals?limit=${limit}`);
export const fetchWeights        = ()  => get<EnsembleWeights>("/api/weights");

// ─── Backtests ────────────────────────────────────────────────────────────────
export const fetchBacktestFiles  = ()           => get<{ files: BacktestFile[] }>("/api/backtests");
export const fetchBacktestResult = (fn: string) => get<BacktestResult>(`/api/backtests/${encodeURIComponent(fn)}`);

// ─── Models ──────────────────────────────────────────────────────────────────
export const fetchMLflowModels   = ()  => get<{ experiments: MLflowExperiment[] }>("/api/models");
