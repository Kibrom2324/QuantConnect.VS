// ─── Shared TypeScript types for APEX Dashboard ─────────────────────────────

export type ServiceStatus = "running" | "stopped" | "unknown";

export interface ServiceHealth {
  services: Record<string, ServiceStatus>;
}

export interface KillSwitchState {
  active: boolean;
  error?: string;
}

export interface PnLToday {
  equity: number;
  last_equity: number;
  pnl_today: number;
  pnl_today_pct: number;
  buying_power: number;
  portfolio_value: number;
  error?: string;
}

export interface Position {
  symbol: string;
  qty: number;
  side: "long" | "short";
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  current_price: number;
  avg_entry_price: number;
}

export interface Signal {
  id?: number;
  symbol: string;
  timestamp: string;
  direction: "UP" | "DOWN" | "NEUTRAL" | "HOLD";
  ensemble_score: number;
  confidence: number;
  regime: "BULL" | "BEAR" | "SIDEWAYS";
  alpha_breakdown: {
    rsi?: number;
    ema?: number;
    macd?: number;
    stochastic?: number;
    sentiment?: number;
    tft?: number;
    [key: string]: number | undefined;
  };
}

export interface EnsembleWeights {
  weights: { TFT: number; XGB: number; Factor: number };
}

export interface BacktestFile {
  filename: string;
  size_bytes: number;
  modified: string;
}

export interface BacktestResult {
  Statistics?: Record<string, string>;
  Charts?: Record<string, { Series: Record<string, { Values: Array<{ x: number; y: number }> }> }>;
  Orders?: Record<string, {
    Symbol: { Value: string };
    Direction: number; // 0=buy, 1=sell
    Quantity: number;
    Price: number;
    LastFillTime: string;
    Value: number;
  }>;
  // QuantConnect JSON can have other keys
  [key: string]: unknown;
}

export interface MLflowRun {
  run_id: string;
  run_name: string;
  status: string;
  start_time: number;
  end_time?: number;
  metrics: Record<string, number>;
  params: Record<string, string>;
  tags: Record<string, string>;
}

export interface MLflowExperiment {
  experiment_id: string;
  name: string;
  lifecycle_stage: string;
  runs: MLflowRun[];
}
