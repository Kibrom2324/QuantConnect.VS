"use client";

import { useEffect, useState } from "react";

interface ServiceInfo {
  status: "running" | "stopped" | "degraded" | string;
  latency_ms?: number | null;
  uptime_pct?: number;
}

interface HealthData {
  status: string;
  is_mock?: boolean;
  services: Record<string, ServiceInfo | string>;
}

export default function ServiceStatusGrid({ refreshKey }: { refreshKey: number }) {
  const [health, setHealth] = useState<HealthData | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth({ status: "offline", services: {} }));
  }, [refreshKey]);

  const services = health?.services ?? {};
  const entries = Object.entries(services);

  const normalize = (val: ServiceInfo | string): ServiceInfo => {
    if (typeof val === "string") return { status: val };
    return val;
  };

  const running  = entries.filter(([, v]) => normalize(v).status === "running").length;
  const degraded = entries.filter(([, v]) => normalize(v).status === "degraded").length;
  const stopped  = entries.filter(([, v]) => normalize(v).status === "stopped").length;
  const total    = entries.length;

  const statusColor  = (s: string) => s === "running" ? "#00FF88" : s === "degraded" ? "#FFB800" : "#FF2D55";
  const statusBg     = (s: string) => s === "running" ? "rgba(0,255,136,0.06)" : s === "degraded" ? "rgba(255,184,0,0.06)" : "rgba(255,45,85,0.06)";
  const statusBorder = (s: string) => s === "running" ? "rgba(0,255,136,0.22)" : s === "degraded" ? "rgba(255,184,0,0.3)" : "rgba(255,45,85,0.3)";

  return (
    <div className="apex-card">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[10px] text-[#4a6a8a] uppercase tracking-widest mb-0.5">System Health</div>
          <div className="font-heading text-sm font-semibold text-[#e2f0ff]">SERVICE MATRIX</div>
        </div>
        <div className="flex items-center gap-3">
          {health?.is_mock && <span className="demo-badge">DEMO</span>}
          <div className="text-right">
            <div className="text-lg font-bold font-mono" style={{ color: stopped > 0 ? "#FFB800" : degraded > 0 ? "#FFB800" : "#00FF88" }}>
              {total}/{total}
            </div>
            <div className="text-[9px] text-[#4a6a8a] uppercase tracking-wider">SERVICES</div>
          </div>
        </div>
      </div>

      <div className="flex gap-2 mb-4 flex-wrap">
        <span className="badge-green">
          <span className="w-1.5 h-1.5 rounded-full bg-[#00FF88] inline-block mr-1.5 animate-blink" />{running} running
        </span>
        {degraded > 0 && (
          <span className="badge-amber">
            <span className="w-1.5 h-1.5 rounded-full bg-[#FFB800] inline-block mr-1.5" />{degraded} degraded
          </span>
        )}
        {stopped > 0 && (
          <span className="badge-red">
            <span className="w-1.5 h-1.5 rounded-full bg-[#FF2D55] inline-block mr-1.5" />{stopped} offline
          </span>
        )}
      </div>

      {entries.length === 0 ? (
        <div className="text-center py-6 text-[#4a6a8a] text-xs">
          {health === null ? "LOADING..." : "NO SERVICES REGISTERED"}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {entries.map(([name, raw]) => {
            const svc    = normalize(raw);
            const col    = statusColor(svc.status);
            const bg     = statusBg(svc.status);
            const border = statusBorder(svc.status);
            return (
              <div key={name} className="rounded-md p-2.5" style={{
                background: bg,
                border: `1px solid ${border}`,
                ...(svc.status === "stopped" ? { borderColor: "rgba(74,106,138,0.2)", background: "rgba(74,106,138,0.04)" } : {}),
                ...(svc.status === "degraded" ? { borderLeft: "3px solid rgba(255,184,0,0.6)" } : {}),
              }}>
                <div className="flex items-center gap-1.5 mb-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${svc.status === "running" ? "animate-blink" : ""}`}
                    style={{ backgroundColor: svc.status === "stopped" ? "#4a6a8a" : col }} />
                  <span className="text-[11px] font-semibold font-mono truncate" style={{ color: svc.status === "stopped" ? "#4a6a8a" : col }}>{name}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] uppercase tracking-wider font-semibold" style={{ color: svc.status === "stopped" ? "#4a6a8a" : col }}>
                    {svc.status}
                  </span>
                  {svc.latency_ms != null
                    ? <span className="text-[10px] font-mono text-[#8aadcc]">{svc.latency_ms}ms</span>
                    : <span className="text-[10px] font-mono text-[#2a4a6a]">—</span>
                  }
                </div>
                {svc.status === "stopped" && (
                  <div className="mt-1 text-[8px] font-mono text-[#2a4a6a]">
                    Start: mlflow ui --port 5000
                  </div>
                )}
                {svc.status === "degraded" && (
                  <div className="mt-1 text-[8px] font-mono text-[#FFB800] opacity-70">
                    High latency detected
                  </div>
                )}
                {svc.uptime_pct != null && svc.status !== "stopped" && (
                  <div className="mt-1.5">
                    <div className="progress-track" style={{ height: "2px" }}>
                      <div className="progress-fill" style={{ width: `${svc.uptime_pct}%`, background: col }} />
                    </div>
                    <div className="text-[8px] text-[#4a6a8a] mt-0.5 text-right">{svc.uptime_pct.toFixed(1)}% uptime</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
