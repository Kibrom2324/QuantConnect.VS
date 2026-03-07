"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ── Types ────────────────────────────────────────────────────────────────────
export interface AlpacaTrade {
  T:  "t";
  S:  string;    // symbol
  p:  number;    // price
  s:  number;    // size
  t:  string;    // timestamp ISO
  c?: string[];  // conditions
}

export interface AlpacaBar {
  T:  "b";
  S:  string;
  o:  number;
  h:  number;
  l:  number;
  c:  number;
  v:  number;
  t:  string;
}

export interface AlpacaQuote {
  T:  "q";
  S:  string;
  bp: number;   // bid price
  bs: number;   // bid size
  ap: number;   // ask price
  as: number;   // ask size
  t:  string;
}

export interface LivePrice {
  symbol:     string;
  price:      number;
  bid:        number;
  ask:        number;
  change:     number;
  change_pct: number;
  volume:     number;
  updated_at: number;
}

interface StreamConfig {
  symbols:   string[];
  onTrade?:  (trade: AlpacaTrade) => void;
  onBar?:    (bar: AlpacaBar)     => void;
  onQuote?:  (quote: AlpacaQuote) => void;
  enabled?:  boolean;
}

interface StreamState {
  connected:  boolean;
  lastTrade:  AlpacaTrade | null;
  lastBar:    AlpacaBar   | null;
  lastQuote:  AlpacaQuote | null;
  error:      string;
  prices:     Record<string, LivePrice>;
}

const WS_URL  = "wss://stream.data.alpaca.markets/v2/iex";
const MAX_BACKOFF = 30_000;

function makeDefaultPrice(symbol: string): LivePrice {
  const BASE: Record<string, number> = {
    NVDA: 492.80, AAPL: 179.90, MSFT: 419.30,
    TSLA: 248.50, AMZN: 198.40, SPY:  521.40,
    QQQ:  441.20, GOOGL: 172.10, META: 522.60,
    AMD:  180.50,
  };
  const base = BASE[symbol] ?? 100;
  const delta = (Math.random() - 0.5) * base * 0.04;
  return {
    symbol,
    price:      Math.round((base + delta) * 100) / 100,
    bid:        Math.round((base + delta - 0.05) * 100) / 100,
    ask:        Math.round((base + delta + 0.05) * 100) / 100,
    change:     Math.round(delta * 100) / 100,
    change_pct: Math.round((delta / base) * 10000) / 100,
    volume:     Math.floor(1_000_000 + Math.random() * 5_000_000),
    updated_at: Date.now(),
  };
}

export function useAlpacaStream(config: StreamConfig): StreamState {
  const { symbols, onTrade, onBar, onQuote, enabled = true } = config;

  const [state, setState] = useState<StreamState>({
    connected:  false,
    lastTrade:  null,
    lastBar:    null,
    lastQuote:  null,
    error:      "",
    prices:     {} as Record<string, LivePrice>,
  });

  const wsRef       = useRef<WebSocket | null>(null);
  const retryRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef  = useRef(1_000);
  const mountedRef  = useRef(true);

  // ── Mock price simulator (when WS not available) ───────────────────────────
  const simulatePrices = useCallback(() => {
    const initial: Record<string, LivePrice> = {};
    for (const sym of symbols) {
      initial[sym] = makeDefaultPrice(sym);
    }
    setState(prev => ({ ...prev, prices: initial }));

    const id = setInterval(() => {
      if (!mountedRef.current) return;
      setState(prev => {
        const next = { ...prev.prices };
        for (const sym of symbols) {
          const cur = next[sym] ?? makeDefaultPrice(sym);
          const drift = (Math.random() - 0.498) * cur.price * 0.0008;
          const newPrice = Math.max(1, Math.round((cur.price + drift) * 100) / 100);
          const base = Object.values(next)[0]?.price ?? newPrice;
          next[sym] = {
            ...cur,
            price:      newPrice,
            bid:        Math.round((newPrice - 0.05) * 100) / 100,
            ask:        Math.round((newPrice + 0.05) * 100) / 100,
            change:     Math.round((newPrice - (cur.price - cur.change)) * 100) / 100,
            change_pct: Math.round(((newPrice - (cur.price - cur.change)) / (cur.price - cur.change)) * 10000) / 100,
            updated_at: Date.now(),
          };
          void base;
        }
        return { ...prev, prices: next };
      });
    }, 3_000);

    return () => clearInterval(id);
  }, [symbols]);

  // ── Real WebSocket connection ──────────────────────────────────────────────
  const connect = useCallback(() => {
    const apiKey    = typeof window !== "undefined" ? "" : "";
    // Keys are server-side only; streaming from client requires a proxy.
    // For now, skip if no keys exposed on window (production uses SSE/polling).
    if (!apiKey) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        // Auth
        ws.send(JSON.stringify({
          action: "auth",
          key:    process.env.NEXT_PUBLIC_ALPACA_PAPER_KEY    ?? "",
          secret: process.env.NEXT_PUBLIC_ALPACA_PAPER_SECRET ?? "",
        }));
      };

      ws.onmessage = (ev: MessageEvent) => {
        try {
          const msgs = JSON.parse(ev.data as string) as (AlpacaTrade | AlpacaBar | AlpacaQuote | { T: "success" | "subscription"; msg?: string })[];
          for (const msg of msgs) {
            if (msg.T === "success") {
              if ((msg as { T: "success"; msg?: string }).msg === "authenticated") {
                ws.send(JSON.stringify({ action: "subscribe", bars: symbols, trades: symbols, quotes: symbols }));
                setState(prev => ({ ...prev, connected: true, error: "" }));
                backoffRef.current = 1_000;
              }
            } else if (msg.T === "t") {
              const t = msg as AlpacaTrade;
              setState(prev => {
                const cur = prev.prices[t.S] ?? makeDefaultPrice(t.S);
                const openRef = cur.price - cur.change;
                return {
                  ...prev,
                  lastTrade: t,
                  prices: {
                    ...prev.prices,
                    [t.S]: {
                      ...cur,
                      price:      t.p,
                      change:     Math.round((t.p - openRef) * 100) / 100,
                      change_pct: Math.round(((t.p - openRef) / openRef) * 10000) / 100,
                      updated_at: Date.now(),
                    }
                  }
                };
              });
              onTrade?.(t);
            } else if (msg.T === "q") {
              const q = msg as AlpacaQuote;
              setState(prev => ({
                ...prev,
                lastQuote: q,
                prices: {
                  ...prev.prices,
                  [q.S]: { ...(prev.prices[q.S] ?? makeDefaultPrice(q.S)), bid: q.bp, ask: q.ap, updated_at: Date.now() }
                }
              }));
              onQuote?.(q);
            } else if (msg.T === "b") {
              setState(prev => ({ ...prev, lastBar: msg as AlpacaBar }));
              onBar?.(msg as AlpacaBar);
            }
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setState(prev => ({ ...prev, connected: false }));
        // Exponential backoff reconnect
        const delay = backoffRef.current;
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF);
        retryRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        setState(prev => ({ ...prev, error: "WebSocket error" }));
        ws.close();
      };
    } catch {
      // WS not available; fall through to mock
    }
  }, [symbols, onTrade, onBar, onQuote]);

  useEffect(() => {
    if (!enabled) return;

    mountedRef.current = true;

    // Try real WebSocket if PUBLIC keys are configured
    const hasPublicKey = !!(
      typeof window !== "undefined" &&
      (window as Window & { __ALPACA_KEY__?: string }).__ALPACA_KEY__
    );

    let stopMock: (() => void) | undefined;
    if (!hasPublicKey) {
      stopMock = simulatePrices();
    } else {
      connect();
    }

    return () => {
      mountedRef.current = false;
      if (retryRef.current) clearTimeout(retryRef.current);
      wsRef.current?.close();
      stopMock?.();
    };
  }, [enabled, connect, simulatePrices]);

  return state;
}
