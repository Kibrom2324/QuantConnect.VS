// To connect to Alpaca paper trading:
// 1. Go to https://alpaca.markets
// 2. Create account → Paper Trading
// 3. Generate API keys in Paper Trading section
// 4. Add to .env.local:
//    ALPACA_PAPER_KEY=PKxxxxxxxxxxxxxxxx
//    ALPACA_PAPER_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
//    ALPACA_PAPER_URL=https://paper-api.alpaca.markets
//    USE_MOCK_DATA=false
// 5. Restart Next.js dev server

import { NextRequest, NextResponse } from "next/server";

const MOCK_ACCOUNT = {
  buying_power:    45230.50,
  portfolio_value: 127450.00,
  cash:            45230.50,
  day_pnl:         1243.50,
  day_pnl_pct:     0.98,
  pattern_day_trader: false,
  trading_blocked: false,
  account_mode:    "paper" as const,
  is_mock:         true,
};

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const accountMode = searchParams.get("account_mode") === "live" ? "live" : "paper";

  const isPaper   = accountMode !== "live";
  const baseUrl   = isPaper
    ? (process.env.ALPACA_PAPER_URL   ?? "https://paper-api.alpaca.markets")
    : (process.env.ALPACA_LIVE_URL    ?? "https://api.alpaca.markets");
  const apiKey    = isPaper ? process.env.ALPACA_PAPER_KEY    : process.env.ALPACA_LIVE_KEY;
  const apiSecret = isPaper ? process.env.ALPACA_PAPER_SECRET : process.env.ALPACA_LIVE_SECRET;
  const useMock   = process.env.USE_MOCK_DATA === "true" || !apiKey || !apiSecret;

  if (useMock) {
    return NextResponse.json({ ...MOCK_ACCOUNT, account_mode: accountMode });
  }

  try {
    const res = await fetch(`${baseUrl}/v2/account`, {
      headers: {
        "APCA-API-KEY-ID":     apiKey!,
        "APCA-API-SECRET-KEY": apiSecret!,
      },
      signal: AbortSignal.timeout(4000),
      cache: "no-store",
    });

    if (!res.ok) {
      return NextResponse.json({ ...MOCK_ACCOUNT, account_mode: accountMode, is_mock: true });
    }

    const data = await res.json() as Record<string, unknown>;
    const equity     = parseFloat(String(data.equity      ?? 0));
    const lastEquity = parseFloat(String(data.last_equity ?? equity));
    const dayPnl     = equity - lastEquity;
    const dayPnlPct  = lastEquity > 0 ? (dayPnl / lastEquity) * 100 : 0;

    return NextResponse.json({
      buying_power:       parseFloat(String(data.buying_power    ?? 0)),
      portfolio_value:    parseFloat(String(data.portfolio_value ?? 0)),
      cash:               parseFloat(String(data.cash            ?? 0)),
      day_pnl:            parseFloat(dayPnl.toFixed(2)),
      day_pnl_pct:        parseFloat(dayPnlPct.toFixed(4)),
      pattern_day_trader: data.pattern_day_trader === true,
      trading_blocked:    data.trading_blocked === true,
      account_mode:       accountMode,
      is_mock:            false,
    });
  } catch {
    return NextResponse.json({ ...MOCK_ACCOUNT, account_mode: accountMode, is_mock: true });
  }
}
