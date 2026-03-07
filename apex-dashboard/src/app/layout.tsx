import type { Metadata } from "next";
import "./globals.css";
import AutoTradingBanner from "@/components/AutoTradingBanner";
import ClientShell from "@/components/ClientShell";

export const metadata: Metadata = {
  title: "APEX v3.0 — Professional Quant Terminal",
  description: "APEX Algorithmic Trading Platform — Live Dashboard",
};

const IS_DEMO = process.env.USE_MOCK_DATA === "true";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body
        className="flex h-screen overflow-hidden"
        style={{ backgroundColor: "#050810", color: "#e2f0ff" }}
      >
        {/* Scanline CRT overlay */}
        <div className="scanline-overlay" aria-hidden="true" />

        {/* Demo mode amber banner */}
        {IS_DEMO && (
          <div className="demo-banner" aria-label="Demo mode active">
            <span>◈</span>
            <span>DEMO MODE — SIMULATED DATA — NOT CONNECTED TO LIVE MARKETS</span>
            <span>◈</span>
          </div>
        )}

        <AutoTradingBanner />
        <ClientShell isDemo={IS_DEMO}>
          {children}
        </ClientShell>

        {/* Bottom status bar */}
        <div className="status-bar">
          <span>
            <span className="status-bar-dot" style={{ backgroundColor: IS_DEMO ? "#FFB800" : "#00FF88" }} />
            {IS_DEMO ? "DEMO" : "LIVE"}
          </span>
          <span>
            <span className="status-bar-dot" style={{ backgroundColor: "#00D4FF" }} />
            API :8000
          </span>
          <span>
            <span className="status-bar-dot" style={{ backgroundColor: "#00FF88" }} />
            UI :3001
          </span>
          <span style={{ marginLeft: "auto", color: "#00D4FF" }}>
            <span className="status-bar-dot" style={{ backgroundColor: "#00D4FF", display: "inline-block" }} />
            PAPER TRADING &nbsp;|&nbsp; APEX v3.0
          </span>
        </div>
      </body>
    </html>
  );
}
