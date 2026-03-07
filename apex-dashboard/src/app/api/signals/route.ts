import { NextResponse } from "next/server";
import { NextRequest } from "next/server";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";
const USE_MOCK = process.env.USE_MOCK_DATA === "true";

const MOCK_SIGNALS = {
  is_mock: true,
  signals: [
    {
      id: 1,
      symbol: "NVDA",
      timestamp: new Date(Date.now() - 4 * 60000).toISOString(),
      direction: "UP",
      predicted_value: 875.50,
      ensemble_score: 0.82,
      confidence: 0.7800,
      regime: "BULL",
      alpha_breakdown: { rsi: 0.72, ema: 0.85, macd: 0.88, stochastic: 0.69, sentiment: 0.91, tft: 0.84 },
    },
    {
      id: 2,
      symbol: "AAPL",
      timestamp: new Date(Date.now() - 11 * 60000).toISOString(),
      direction: "DOWN",
      predicted_value: 213.75,
      ensemble_score: 0.71,
      confidence: 0.6500,
      regime: "BEAR",
      alpha_breakdown: { rsi: 0.68, ema: 0.74, macd: 0.71, stochastic: 0.62, sentiment: 0.55, tft: 0.77 },
    },
    {
      id: 3,
      symbol: "MSFT",
      timestamp: new Date(Date.now() - 19 * 60000).toISOString(),
      direction: "UP",
      predicted_value: 418.20,
      ensemble_score: 0.58,
      confidence: 0.5200,
      regime: "SIDEWAYS",
      alpha_breakdown: { rsi: 0.55, ema: 0.61, macd: 0.57, stochastic: 0.48, sentiment: 0.62, tft: 0.59 },
    },
  ],
};

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const symbol = searchParams.get("symbol") ?? "";
  const limit = searchParams.get("limit") ?? "50";

  try {
    const url = `${APEX}/signals?symbol=${symbol}&limit=${limit}`;
    const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return NextResponse.json({ ...data, is_mock: false });
  } catch {
    // Always fall back to mock data when APEX backend is unreachable
    const filtered = symbol
      ? { ...MOCK_SIGNALS, signals: MOCK_SIGNALS.signals.filter(s => s.symbol === symbol) }
      : MOCK_SIGNALS;
    return NextResponse.json(filtered);
  }
}
