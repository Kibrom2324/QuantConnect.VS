"use client";

import { useEffect, useRef, useState } from "react";

interface Position {
  symbol: string;
  qty: number;
  side: "long" | "short";
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  age_minutes?: number;
  exchange?: string;
}

interface PositionFlash { [sym: string]: "green" | "red" | null }

function fmt(n: number) {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function ageLabel(mins?: number) {
  if (mins == null) return null;
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h${m}m` : `${h}h`;
}

export default function PositionsTable({ refreshKey }: { refreshKey: number }) {
  const [positions, setPositions] = useState<Position[]>([]);
  const [isMock, setIsMock] = useState(false);
  const [flash, setFlash] = useState<PositionFlash>({});
  const prevPrices = useRef<Record<string, number>>({});

  useEffect(() => {
    fetch("/api/positions")
      .then((r) => r.json())
      .then((data) => {
        const list: Position[] = data.positions ?? [];
        setIsMock(!!data.is_mock);

        // Detect price changes for flash animation
        const newFlash: PositionFlash = {};
        list.forEach((p) => {
          const prev = prevPrices.current[p.symbol];
          if (prev != null && prev !== p.current_price) {
            newFlash[p.symbol] = p.current_price > prev ? "green" : "red";
          }
          prevPrices.current[p.symbol] = p.current_price;
        });
        if (Object.keys(newFlash).length > 0) {
          setFlash(newFlash);
          setTimeout(() => setFlash({}), 700);
        }

        setPositions(list);
      })
      .catch(() => setPositions([]));
  }, [refreshKey]);

  if (positions.length === 0) {
    return (
      <div className="apex-card">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-[10px] text-[#4a6a8a] uppercase tracking-widest mb-0.5">Live Positions</div>
            <div className="font-heading text-sm font-semibold text-[#e2f0ff]">OPEN POSITIONS</div>
          </div>
          {isMock && <span className="demo-badge">DEMO</span>}
        </div>
        {/* Animated empty state */}
        <div className="flex flex-col items-center justify-center py-10 gap-3">
          <div className="relative">
            <svg width="48" height="48" viewBox="0 0 48 48" className="animate-radar opacity-40">
              <circle cx="24" cy="24" r="20" fill="none" stroke="rgba(0,212,255,0.3)" strokeWidth="1" />
              <circle cx="24" cy="24" r="12" fill="none" stroke="rgba(0,212,255,0.2)" strokeWidth="1" />
              <circle cx="24" cy="24" r="4" fill="none" stroke="rgba(0,212,255,0.4)" strokeWidth="1" />
              <line x1="24" y1="24" x2="44" y2="24" stroke="rgba(0,212,255,0.6)" strokeWidth="1.5" />
            </svg>
          </div>
          <div className="text-[#4a6a8a] font-mono text-xs tracking-widest uppercase animate-blink">
            SCANNING MARKETS
          </div>
          <div className="text-[#2a4a6a] text-[10px] font-mono">No open positions</div>
        </div>
      </div>
    );
  }

  return (
    <div className="apex-card">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] text-[#4a6a8a] uppercase tracking-widest mb-0.5">Live Positions</div>
          <div className="font-heading text-sm font-semibold text-[#e2f0ff]">OPEN POSITIONS</div>
        </div>
        <div className="flex items-center gap-2">
          {isMock && <span className="demo-badge">DEMO</span>}
          <span className="badge-blue">{positions.length} active</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="apex-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Entry</th>
              <th className="text-right">Current</th>
              <th className="text-right">Mkt Val</th>
              <th className="text-right">P&L</th>
              <th className="text-right">Age</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const up = p.unrealized_pl >= 0;
              const plColor = up ? "#00FF88" : "#FF2D55";
              const flashClass = flash[p.symbol] === "green"
                ? "animate-flash-green"
                : flash[p.symbol] === "red"
                ? "animate-flash-red"
                : "";
              return (
                <tr key={p.symbol} className={flashClass}>
                  <td>
                    <div className="flex items-center gap-2">
                      <span
                        className="w-0.5 h-5 rounded-full flex-shrink-0"
                        style={{ background: p.side === "long" ? "#00FF88" : "#FF2D55" }}
                      />
                      <div>
                        <div className="font-bold text-[#00D4FF] font-mono text-sm">{p.symbol}</div>
                        {p.exchange && (
                          <div className="text-[8px] text-[#4a6a8a] uppercase">{p.exchange}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>
                    <span className={p.side === "long" ? "badge-green" : "badge-red"}>
                      {p.side.toUpperCase()}
                    </span>
                  </td>
                  <td className="text-right font-mono text-[#e2f0ff]">{p.qty}</td>
                  <td className="text-right font-mono text-[#8aadcc] text-xs">${fmt(p.avg_entry_price)}</td>
                  <td className="text-right font-mono text-[#e2f0ff]">${fmt(p.current_price)}</td>
                  <td className="text-right font-mono text-[#8aadcc] text-xs">${fmt(p.market_value)}</td>
                  <td className="text-right">
                    <div className="font-bold font-mono text-sm" style={{ color: plColor }}>
                      {up ? "+" : ""}${fmt(p.unrealized_pl)}
                    </div>
                    <div className="text-[10px] font-mono" style={{ color: plColor }}>
                      {(p.unrealized_plpc * 100).toFixed(2)}%
                    </div>
                  </td>
                  <td className="text-right">
                    {p.age_minutes != null ? (
                      <span className="text-[10px] font-mono text-[#4a6a8a]">{ageLabel(p.age_minutes)}</span>
                    ) : (
                      <span className="text-[#2a4a6a]">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
