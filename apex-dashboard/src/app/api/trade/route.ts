import { NextRequest, NextResponse } from "next/server";
import { existsSync, readFileSync } from "fs";

const USE_MOCK = process.env.USE_MOCK_DATA === "true";
const KILL_PATH = "/tmp/apex_kill_switch.json";

interface TradeRequest {
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  order_type: "market" | "limit";
  limit_price?: number;
  account_mode: "paper" | "live";
  confirmed: boolean;
}

function isKillSwitchActive(): boolean {
  try {
    if (existsSync(KILL_PATH)) {
      const data = JSON.parse(readFileSync(KILL_PATH, "utf-8"));
      return data.active === true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

export async function POST(req: NextRequest) {
  let body: TradeRequest;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  // Safety gate 1: confirmation required
  if (!body.confirmed) {
    return NextResponse.json(
      { error: "Order not confirmed. Set confirmed: true to proceed." },
      { status: 400 }
    );
  }

  // Safety gate 2: kill switch
  if (isKillSwitchActive()) {
    return NextResponse.json(
      { error: "Kill switch is active. Resume trading before placing orders." },
      { status: 403 }
    );
  }

  // Safety gate 3: field validation
  if (!body.symbol || typeof body.symbol !== "string") {
    return NextResponse.json({ error: "symbol is required" }, { status: 400 });
  }
  if (body.side !== "buy" && body.side !== "sell") {
    return NextResponse.json({ error: "side must be 'buy' or 'sell'" }, { status: 400 });
  }
  if (!body.qty || body.qty <= 0) {
    return NextResponse.json({ error: "qty must be a positive number" }, { status: 400 });
  }
  if (body.order_type === "limit" && !body.limit_price) {
    return NextResponse.json({ error: "limit_price required for limit orders" }, { status: 400 });
  }

  // Safety gate 4: live mode guard
  if (body.account_mode === "live") {
    if (!process.env.ALPACA_LIVE_KEY || !process.env.ALPACA_LIVE_SECRET) {
      return NextResponse.json(
        { error: "Live trading requires ALPACA_LIVE_KEY and ALPACA_LIVE_SECRET env vars." },
        { status: 403 }
      );
    }
  }

  // Mock mode: return simulated fill
  if (USE_MOCK) {
    const mockPrice =
      body.symbol === "NVDA" ? 492.80 :
      body.symbol === "AAPL" ? 179.90 :
      body.symbol === "MSFT" ? 419.30 :
      body.symbol === "TSLA" ? 248.50 :
      body.symbol === "AMZN" ? 198.20 :
      body.symbol === "GOOGL" ? 175.40 : 100.00;

    return NextResponse.json({
      id: `mock-${Date.now()}`,
      client_order_id: `apex-${Date.now()}`,
      status: "filled",
      symbol: body.symbol.toUpperCase(),
      side: body.side,
      qty: body.qty,
      order_type: body.order_type,
      filled_avg_price: mockPrice,
      filled_qty: body.qty,
      filled_at: new Date().toISOString(),
      submitted_at: new Date().toISOString(),
      account_mode: body.account_mode,
      is_mock: true,
    });
  }

  // Real Alpaca order submission
  const isPaper = body.account_mode !== "live";
  const baseUrl = isPaper
    ? (process.env.ALPACA_PAPER_URL ?? "https://paper-api.alpaca.markets")
    : (process.env.ALPACA_LIVE_URL ?? "https://api.alpaca.markets");
  const apiKey = isPaper
    ? process.env.ALPACA_PAPER_KEY
    : process.env.ALPACA_LIVE_KEY;
  const apiSecret = isPaper
    ? process.env.ALPACA_PAPER_SECRET
    : process.env.ALPACA_LIVE_SECRET;

  if (!apiKey || !apiSecret) {
    return NextResponse.json(
      { error: `Missing Alpaca credentials for ${body.account_mode} mode.` },
      { status: 500 }
    );
  }

  const orderPayload: Record<string, unknown> = {
    symbol: body.symbol.toUpperCase(),
    qty: String(body.qty),
    side: body.side,
    type: body.order_type,
    time_in_force: "day",
  };
  if (body.order_type === "limit" && body.limit_price) {
    orderPayload.limit_price = String(body.limit_price);
  }

  try {
    const res = await fetch(`${baseUrl}/v2/orders`, {
      method: "POST",
      headers: {
        "APCA-API-KEY-ID": apiKey,
        "APCA-API-SECRET-KEY": apiSecret,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(orderPayload),
    });

    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: data.message ?? "Alpaca rejected the order." },
        { status: res.status }
      );
    }

    return NextResponse.json({ ...data, account_mode: body.account_mode, is_mock: false });
  } catch (err) {
    return NextResponse.json(
      { error: `Failed to reach Alpaca: ${String(err)}` },
      { status: 502 }
    );
  }
}
