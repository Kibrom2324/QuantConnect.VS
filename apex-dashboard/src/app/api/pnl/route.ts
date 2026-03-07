import { NextResponse } from "next/server";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";
const USE_MOCK = process.env.USE_MOCK_DATA === "true";

const MOCK_PNL = {
  equity: 127450.00,
  last_equity: 126206.50,
  pnl_today: 1243.50,
  pnl_today_pct: 0.98,
  buying_power: 48200.00,
  portfolio_value: 127450.00,
  week_pnl: 3820.00,
  win_rate: 54.2,
  sharpe: 1.35,
  max_dd: -1.8,
  total_trades_today: 7,
  is_mock: true,
};

export async function GET() {
  try {
    const res = await fetch(`${APEX}/pnl/today`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return NextResponse.json({ ...data, is_mock: false });
  } catch {
    if (USE_MOCK) return NextResponse.json(MOCK_PNL);
    return NextResponse.json({
      equity: 0, last_equity: 0, pnl_today: 0, pnl_today_pct: 0,
      buying_power: 0, portfolio_value: 0, week_pnl: 0,
      win_rate: 0, sharpe: 0, max_dd: 0, total_trades_today: 0,
      is_mock: false, error: "offline",
    });
  }
}
