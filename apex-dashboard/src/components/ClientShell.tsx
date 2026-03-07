"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import Sidebar from "@/components/Sidebar";
import LivePriceBar from "@/components/LivePriceBar";

const CommandPalette = dynamic(() => import("@/components/CommandPalette"), { ssr: false });
const ChartModal     = dynamic(() => import("@/components/ChartModal"),     { ssr: false });

const NAV_SHORTCUTS: Record<string, string> = {
  "1": "/dashboard",
  "2": "/charts",
  "3": "/trading",
  "4": "/signals",
  "5": "/risk",
  "6": "/backtest",
  "7": "/models",
  "8": "/wallet",
};

interface Props {
  children:  React.ReactNode;
  isDemo:    boolean;
}

export default function ClientShell({ children, isDemo }: Props) {
  const router = useRouter();
  const [paletteOpen,  setPaletteOpen]  = useState(false);
  const [modalSymbol,  setModalSymbol]  = useState<string | null>(null);

  // Global keyboard shortcuts
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    const meta = e.metaKey || e.ctrlKey;
    if (!meta) return;

    // ⌘K — command palette
    if (e.key === "k" || e.key === "K") {
      e.preventDefault();
      setPaletteOpen(p => !p);
      return;
    }

    // ⌘1–7 — navigation
    const navPath = NAV_SHORTCUTS[e.key];
    if (navPath) {
      e.preventDefault();
      router.push(navPath);
      return;
    }

    // ⌘. — toggle kill switch shortcut hint
    if (e.key === ".") {
      e.preventDefault();
      // Show hint — actual kill switch is in the command palette
      setPaletteOpen(true);
    }
  }, [router]);

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return (
    <>
      <div className={`flex flex-1 overflow-hidden flex-col ${isDemo ? "pt-7" : ""}`}>
        {/* Live price ticker bar */}
        <LivePriceBar onSymbolClick={sym => setModalSymbol(sym)} />

        {/* Main layout */}
        <div className="flex flex-1 overflow-hidden">
          <Sidebar />
          <main className="flex-1 overflow-y-auto p-5 pb-8">
            {children}
          </main>
        </div>
      </div>

      {/* Command palette overlay */}
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onSymbolSelect={sym => {
          setPaletteOpen(false);
          setModalSymbol(sym);
        }}
      />

      {/* Chart modal triggered by price bar symbol clicks */}
      {modalSymbol && (
        <ChartModal symbol={modalSymbol} onClose={() => setModalSymbol(null)} />
      )}
    </>
  );
}
