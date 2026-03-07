// Positions route: tries Alpaca paper trading first, falls back to mock data.
// Set ALPACA_PAPER_KEY + ALPACA_PAPER_SECRET in .env.local to connect to real account.

import { NextResponse } from "next/server";

const MOCK_POSITIONS = {
  is_mock: true,
  positions: [
    {
      symbol: "NVDA",
      qty: 10,
      side: "long",
      avg_entry_price: 485.20,
      current_price: 492.80,
      market_value: 4928.00,
      unrealized_pl: 76.00,
      unrealized_plpc: 0.0157,
      age_minutes: null,
      exchange: "NASDAQ",
    },
    {
      symbol: "AAPL",
      qty: 15,
      side: "long",
      avg_entry_price: 182.40,
      current_price: 179.90,
      market_value: 2698.50,
      unrealized_pl: -37.50,
      unrealized_plpc: -0.0137,
      age_minutes: null,
      exchange: "NASDAQ",
    },
    {
      symbol: "MSFT",
      qty: 8,
      side: "long",
      avg_entry_price: 415.60,
      current_price: 419.30,
      market_value: 3354.40,
      unrealized_pl: 29.60,
      unrealized_plpc: 0.0089,
      age_minutes: null,
      exchange: "NASDAQ",
    },
  ],
};

export async function GET() {
  const apiKey    = process.env.ALPACA_PAPER_KEY;
  const apiSecret = process.env.ALPACA_PAPER_SECRET;
  const baseUrl   = process.env.ALPACA_PAPER_URL ?? "https://paper-api.alpaca.markets";
  const useMock   = process.env.USE_MOCK_DATA === "true" || !apiKey || !apiSecret;

  if (useMock) {
    return NextResponse.json(MOCK_POSITIONS);
  }

  try {
    const res = await fetch(`${baseUrl}/v2/positions`, {
      headers: {
        "APCA-API-KEY-ID":     apiKey!,
        "APCA-API-SECRET-KEY": apiSecret!,
      },
      signal: AbortSignal.timeout(4000),
      cache: "no-store",
    });

    if (!res.ok) {
      return NextResponse.json(MOCK_POSITIONS);
    }

    const data = await res.json() as Record<string, unknown>[];
    const positions = data.map((p) => ({
      symbol:          p.symbol,
      qty:             parseFloat(String(p.qty ?? 0)),
      side:            (p.side as string) === "short" ? "short" : "long",
      avg_entry_price: parseFloat(String(p.avg_entry_price ?? 0)),
      current_price:   parseFloat(String(p.current_price  ?? 0)),
      market_value:    parseFloat(String(p.market_value   ?? 0)),
      unrealized_pl:   parseFloat(String(p.unrealized_pl  ?? 0)),
      unrealized_plpc: parseFloat(String(p.unrealized_plpc ?? 0)),
      age_minutes:     null,   // Alpaca doesn’t provide this
      exchange:        p.exchange ?? "",
    }));

    return NextResponse.json({ positions, is_mock: false });
  } catch {
    return NextResponse.json(MOCK_POSITIONS);
  }
}
