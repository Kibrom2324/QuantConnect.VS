"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { LayoutDashboard, Activity, BarChart2, Brain, Zap, LineChart, ShieldAlert, Wallet, ShoppingCart } from "lucide-react";

const NAV = [
  { href: "/dashboard", label: "Dashboard",  icon: LayoutDashboard, accent: "#00D4FF" },
  { href: "/charts",    label: "Charts",     icon: LineChart,        accent: "#00D4FF" },
  { href: "/signals",   label: "Signals",    icon: Activity,         accent: "#00FF88" },
  { href: "/trading",   label: "Trading",    icon: Zap,              accent: "#00D4FF" },
  { href: "/orders",    label: "Orders",     icon: ShoppingCart,     accent: "#00D4FF" },
  { href: "/wallet",    label: "Wallet",     icon: Wallet,           accent: "#FFB800" },
  { href: "/risk",      label: "Risk",       icon: ShieldAlert,      accent: "#FF2D55" },
  { href: "/backtest",  label: "Backtest",   icon: BarChart2,        accent: "#FFB800" },
  { href: "/models",    label: "Models",     icon: Brain,            accent: "#8B5CF6" },
];

function MarketClock() {
  const [time, setTime] = useState("");
  const [status, setStatus] = useState<{ label: string; color: string }>({ label: "CLOSED", color: "#4a6a8a" });

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTime(now.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }));
      const utcH = now.getUTCHours();
      const utcM = now.getUTCMinutes();
      const utcTotal = utcH * 60 + utcM;
      const day = now.getUTCDay(); // 0=Sun,6=Sat
      if (day === 0 || day === 6) {
        setStatus({ label: "CLOSED", color: "#4a6a8a" });
      } else if (utcTotal >= 840 && utcTotal < 870) {
        setStatus({ label: "PRE-MARKET", color: "#FFB800" });
      } else if (utcTotal >= 870 && utcTotal < 1260) {
        setStatus({ label: "MARKET OPEN", color: "#00FF88" });
      } else if (utcTotal >= 1260 && utcTotal < 1320) {
        setStatus({ label: "AFTER-HOURS", color: "#8B5CF6" });
      } else {
        setStatus({ label: "CLOSED", color: "#4a6a8a" });
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="px-4 py-3 border-b border-apex-border/50">
      <p className="text-apex-subtext text-[9px] uppercase tracking-widest mb-0.5">Market Session</p>
      <p className="font-mono text-xs font-semibold" style={{ color: status.color }}>
        {status.label}
      </p>
      <p className="font-mono text-[11px] text-apex-subtext mt-0.5">{time} UTC</p>
    </div>
  );
}

export default function Sidebar() {
  const path = usePathname();

  return (
    <aside
      className="w-52 shrink-0 flex flex-col"
      style={{
        background: "linear-gradient(180deg, #070c18 0%, #050810 100%)",
        borderRight: "1px solid rgba(0,212,255,0.12)",
      }}
    >
      {/* Logo */}
      <div className="px-4 py-4 border-b border-apex-border/50">
        <div className="flex items-center gap-2">
          <div
            className="w-7 h-7 rounded flex items-center justify-center shrink-0"
            style={{
              background: "rgba(0,212,255,0.1)",
              border: "1px solid rgba(0,212,255,0.35)",
              boxShadow: "0 0 14px rgba(0,212,255,0.2)",
            }}
          >
            <Zap className="w-4 h-4" style={{ color: "#00D4FF" }} />
          </div>
          <div>
            <span
              className="font-bold text-[13px] tracking-widest"
              style={{ fontFamily: "'Syne', sans-serif", color: "#00D4FF", textShadow: "0 0 10px rgba(0,212,255,0.5)" }}
            >
              APEX
            </span>
            <span className="text-apex-subtext text-[10px] ml-1.5 font-mono">v3.0</span>
          </div>
        </div>
        <p className="text-[9px] text-apex-subtext mt-1 font-mono tracking-widest uppercase">Algorithmic Trading</p>
      </div>

      {/* Market clock */}
      <MarketClock />

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV.map(({ href, label, icon: Icon, accent }) => {
          const active = path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-3 px-3 py-2.5 rounded-md text-xs transition-all duration-200 group relative overflow-hidden"
              style={active ? {
                background: `rgba(${accent === "#00D4FF" ? "0,212,255" : accent === "#00FF88" ? "0,255,136" : accent === "#FFB800" ? "255,184,0" : "139,92,246"}, 0.1)`,
                border: `1px solid ${accent}40`,
                color: accent,
                boxShadow: `0 0 12px ${accent}20`,
              } : {
                color: "#4a6a8a",
                border: "1px solid transparent",
              }}
            >
              {active && (
                <span
                  className="absolute left-0 top-0 bottom-0 w-0.5 rounded-r"
                  style={{ background: accent, boxShadow: `0 0 8px ${accent}` }}
                />
              )}
              <Icon
                className="w-4 h-4 shrink-0 transition-all duration-200"
                style={{ color: active ? accent : "#4a6a8a" }}
              />
              <span
                className="font-mono text-[11px] tracking-wider uppercase transition-colors duration-200"
                style={{ color: active ? accent : "#4a6a8a" }}
              >
                {label}
              </span>
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-apex-border/50">
        <div className="flex items-center gap-1.5 mb-1">
          <span className="w-1.5 h-1.5 rounded-full bg-apex-green animate-blink" />
          <span className="text-[9px] font-mono text-apex-subtext uppercase tracking-widest">Paper Trading</span>
        </div>
        <p className="text-[9px] text-apex-subtext font-mono">
          API <span className="text-apex-cyan">:8000</span>
          {" · "}UI <span className="text-apex-cyan">:3001</span>
        </p>
      </div>
    </aside>
  );
}
