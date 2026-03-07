import { NextRequest, NextResponse } from "next/server";

const USE_MOCK = process.env.USE_MOCK_DATA === "true";

const MOCK_QUOTES: Record<string, { price: number; change: number; change_pct: number; name: string }> = {
  NVDA:  { price: 492.80, change:  1.57, change_pct:  0.32, name: "NVIDIA Corp" },
  AAPL:  { price: 179.90, change: -2.50, change_pct: -1.37, name: "Apple Inc" },
  MSFT:  { price: 419.30, change:  4.20, change_pct:  1.01, name: "Microsoft Corp" },
  TSLA:  { price: 248.50, change: -3.80, change_pct: -1.51, name: "Tesla Inc" },
  AMZN:  { price: 198.20, change:  2.10, change_pct:  1.07, name: "Amazon.com Inc" },
  GOOGL: { price: 175.40, change:  1.80, change_pct:  1.04, name: "Alphabet Inc" },
  META:  { price: 502.60, change:  6.30, change_pct:  1.27, name: "Meta Platforms Inc" },
  AMD:   { price: 182.70, change: -1.40, change_pct: -0.76, name: "Advanced Micro Devices" },
  NFLX:  { price: 634.20, change:  3.70, change_pct:  0.59, name: "Netflix Inc" },
  SPY:   { price: 481.50, change:  0.90, change_pct:  0.19, name: "SPDR S&P 500 ETF" },
  QQQ:   { price: 421.30, change:  1.40, change_pct:  0.33, name: "Invesco QQQ Trust" },
};

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const rawSymbol = searchParams.get("symbol") ?? "";
  const symbol = rawSymbol.toUpperCase().trim();

  if (!symbol) {
    return NextResponse.json({ error: "symbol query param required" }, { status: 400 });
  }

  if (USE_MOCK) {
    const q = MOCK_QUOTES[symbol];
    if (q) {
      return NextResponse.json({ symbol, ...q, is_mock: true });
    }
    // Unknown symbol in mock mode — return fabricated price
    return NextResponse.json({
      symbol,
      price: 100.00,
      change: 0.00,
      change_pct: 0.00,
      name: symbol,
      is_mock: true,
    });
  }

  // Real quote via Alpaca (paper credentials can fetch market data)
  const apiKey    = process.env.ALPACA_PAPER_KEY;
  const apiSecret = process.env.ALPACA_PAPER_SECRET;

  if (!apiKey || !apiSecret) {
    // Fall back to mock
    const q = MOCK_QUOTES[symbol];
    return NextResponse.json({
      symbol,
      ...(q ?? { price: 100.00, change: 0, change_pct: 0, name: symbol }),
      is_mock: true,
    });
  }

  try {
    const res = await fetch(
      `https://data.alpaca.markets/v2/stocks/${symbol}/snapshot`,
      {
        headers: {
          "APCA-API-KEY-ID":     apiKey,
          "APCA-API-SECRET-KEY": apiSecret,
        },
        next: { revalidate: 15 },
      }
    );

    if (!res.ok) {
      const q = MOCK_QUOTES[symbol];
      return NextResponse.json({
        symbol,
        ...(q ?? { price: 100.00, change: 0, change_pct: 0, name: symbol }),
        is_mock: true,
      });
    }

    const snap = await res.json() as {
      latestTrade?: { p?: number };
      dailyBar?:    { c?: number; o?: number };
    };

    const price     = snap.latestTrade?.p ?? snap.dailyBar?.c ?? 0;
    const openPrice = snap.dailyBar?.o ?? price;
    const change    = price - openPrice;
    const changePct = openPrice > 0 ? (change / openPrice) * 100 : 0;

    return NextResponse.json({
      symbol,
      price,
      change:     parseFloat(change.toFixed(2)),
      change_pct: parseFloat(changePct.toFixed(2)),
      name:       symbol,
      is_mock: false,
    });
  } catch {
    const q = MOCK_QUOTES[symbol];
    return NextResponse.json({
      symbol,
      ...(q ?? { price: 100.00, change: 0, change_pct: 0, name: symbol }),
      is_mock: true,
    });
  }
}
