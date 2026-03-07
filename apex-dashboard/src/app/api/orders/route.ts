import { NextRequest, NextResponse } from "next/server";

const USE_MOCK = process.env.USE_MOCK_DATA === "true";

const MOCK_ORDERS = [
  {
    id: "ord1",
    client_order_id: "apex-ord1",
    symbol: "NVDA",
    side: "buy",
    qty: 10,
    status: "filled",
    filled_avg_price: 485.20,
    filled_qty: 10,
    submitted_at: "2024-01-15T14:32:00Z",
    filled_at: "2024-01-15T14:32:05Z",
    order_type: "market",
    source: "auto",
  },
  {
    id: "ord2",
    client_order_id: "apex-ord2",
    symbol: "AAPL",
    side: "sell",
    qty: 15,
    status: "filled",
    filled_avg_price: 182.40,
    filled_qty: 15,
    submitted_at: "2024-01-15T11:15:00Z",
    filled_at: "2024-01-15T11:15:04Z",
    order_type: "market",
    source: "manual",
  },
  {
    id: "ord3",
    client_order_id: "apex-ord3",
    symbol: "MSFT",
    side: "buy",
    qty: 8,
    status: "filled",
    filled_avg_price: 415.60,
    filled_qty: 8,
    submitted_at: "2024-01-14T15:45:00Z",
    filled_at: "2024-01-14T15:45:06Z",
    order_type: "market",
    source: "auto",
  },
  {
    id: "ord4",
    client_order_id: "apex-ord4",
    symbol: "TSLA",
    side: "buy",
    qty: 5,
    status: "cancelled",
    filled_avg_price: null,
    filled_qty: 0,
    submitted_at: "2024-01-14T10:20:00Z",
    filled_at: null,
    order_type: "limit",
    limit_price: 245.00,
    source: "manual",
  },
  {
    id: "ord5",
    client_order_id: "apex-ord5",
    symbol: "NVDA",
    side: "sell",
    qty: 10,
    status: "filled",
    filled_avg_price: 501.30,
    filled_qty: 10,
    submitted_at: "2024-01-13T15:55:00Z",
    filled_at: "2024-01-13T15:55:03Z",
    order_type: "market",
    source: "auto",
  },
];

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit       = Math.min(parseInt(searchParams.get("limit") ?? "20"), 100);
  const accountMode = searchParams.get("account_mode") === "live" ? "live" : "paper";

  if (USE_MOCK) {
    return NextResponse.json({
      orders: MOCK_ORDERS.slice(0, limit),
      account_mode: accountMode,
      is_mock: true,
    });
  }

  const isPaper = accountMode !== "live";
  const baseUrl = isPaper
    ? (process.env.ALPACA_PAPER_URL ?? "https://paper-api.alpaca.markets")
    : (process.env.ALPACA_LIVE_URL ?? "https://api.alpaca.markets");
  const apiKey    = isPaper ? process.env.ALPACA_PAPER_KEY    : process.env.ALPACA_LIVE_KEY;
  const apiSecret = isPaper ? process.env.ALPACA_PAPER_SECRET : process.env.ALPACA_LIVE_SECRET;

  if (!apiKey || !apiSecret) {
    return NextResponse.json({
      orders: MOCK_ORDERS.slice(0, limit),
      account_mode: accountMode,
      is_mock: true,
    });
  }

  try {
    const res = await fetch(
      `${baseUrl}/v2/orders?status=all&limit=${limit}&direction=desc`,
      {
        headers: {
          "APCA-API-KEY-ID":     apiKey,
          "APCA-API-SECRET-KEY": apiSecret,
        },
        next: { revalidate: 10 },
      }
    );

    if (!res.ok) {
      return NextResponse.json({
        orders: MOCK_ORDERS.slice(0, limit),
        account_mode: accountMode,
        is_mock: true,
      });
    }

    const data = await res.json() as Record<string, unknown>[];
    const orders = data.map((o) => ({
      id:               o.id,
      client_order_id:  o.client_order_id,
      symbol:           o.symbol,
      side:             o.side,
      qty:              parseFloat(String(o.qty ?? 0)),
      status:           o.status,
      filled_avg_price: o.filled_avg_price != null
        ? parseFloat(String(o.filled_avg_price))
        : null,
      filled_qty:       parseFloat(String(o.filled_qty ?? 0)),
      submitted_at:     o.submitted_at,
      filled_at:        o.filled_at ?? null,
      order_type:       o.order_type ?? o.type,
      limit_price:      o.limit_price != null
        ? parseFloat(String(o.limit_price))
        : null,
      source: "manual",   // Alpaca doesn't distinguish; tag auto orders via client_order_id prefix
    }));

    return NextResponse.json({ orders, account_mode: accountMode, is_mock: false });
  } catch {
    return NextResponse.json({
      orders: MOCK_ORDERS.slice(0, limit),
      account_mode: accountMode,
      is_mock: true,
    });
  }
}

// ── DELETE /api/orders?id=<orderId>&account_mode=paper|live ─────────────────
export async function DELETE(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const orderId     = searchParams.get("id");
  const accountMode = searchParams.get("account_mode") === "live" ? "live" : "paper";

  if (!orderId) {
    return NextResponse.json({ error: "Order ID required" }, { status: 400 });
  }

  if (USE_MOCK) {
    return NextResponse.json({ status: "cancelled", is_mock: true });
  }

  const isPaper   = accountMode !== "live";
  const baseUrl   = isPaper
    ? (process.env.ALPACA_PAPER_URL ?? "https://paper-api.alpaca.markets")
    : (process.env.ALPACA_LIVE_URL  ?? "https://api.alpaca.markets");
  const apiKey    = isPaper ? process.env.ALPACA_PAPER_KEY    : process.env.ALPACA_LIVE_KEY;
  const apiSecret = isPaper ? process.env.ALPACA_PAPER_SECRET : process.env.ALPACA_LIVE_SECRET;

  if (!apiKey || !apiSecret) {
    return NextResponse.json({ status: "cancelled", is_mock: true });
  }

  try {
    const res = await fetch(`${baseUrl}/v2/orders/${orderId}`, {
      method:  "DELETE",
      headers: {
        "APCA-API-KEY-ID":     apiKey,
        "APCA-API-SECRET-KEY": apiSecret,
      },
      signal: AbortSignal.timeout(4000),
    });

    // Alpaca returns 204 No Content on success
    if (res.status === 204 || res.ok) {
      return NextResponse.json({ status: "cancelled", is_mock: false });
    }

    const err = await res.json().catch(() => ({})) as { message?: string };
    return NextResponse.json(
      { error: err.message ?? "Cancel failed" },
      { status: res.status }
    );
  } catch {
    return NextResponse.json({ error: "Network error cancelling order" }, { status: 500 });
  }
}
