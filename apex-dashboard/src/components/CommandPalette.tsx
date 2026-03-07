"use client";

import { useEffect, useState, useCallback } from "react";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";

// ── Types ─────────────────────────────────────────────────────────────────
interface Signal {
  symbol:     string;
  direction:  string;
  confidence: number;
  timestamp:  string;
}

// ── WATCH symbols ─────────────────────────────────────────────────────────
const SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META", "GOOGL", "AMD", "SPY", "QQQ"];

const NAV_ITEMS = [
  { label: "Dashboard",   href: "/dashboard", shortcut: "⌘1" },
  { label: "Charts",      href: "/charts",    shortcut: "⌘2" },
  { label: "Trading",     href: "/trading",   shortcut: "⌘3" },
  { label: "Signals",     href: "/signals",   shortcut: "⌘4" },
  { label: "Risk",        href: "/risk",      shortcut: "⌘5" },
  { label: "Backtest",    href: "/backtest",  shortcut: "⌘6" },
  { label: "Models",      href: "/models",    shortcut: "⌘7" },
];

const SYSTEM_COMMANDS = [
  { label: "🛡  Activate Kill Switch",   id: "kill",   danger: true  },
  { label: "▶  Resume Auto-Trading",     id: "resume", danger: false },
  { label: "📋  Switch to Paper Mode",   id: "paper",  danger: false },
  { label: "🤖  Enable Auto Mode",       id: "auto",   danger: false },
];

interface Props {
  open:    boolean;
  onClose: () => void;
  onSymbolSelect?: (symbol: string) => void;
}

export default function CommandPalette({ open, onClose, onSymbolSelect }: Props) {
  const router  = useRouter();
  const [signals, setSignals] = useState<Signal[]>([]);

  // Fetch recent signals for suggestions
  useEffect(() => {
    if (!open) return;
    fetch("/api/signals?limit=5")
      .then(r => r.json())
      .then(d => setSignals(d.signals ?? d ?? []))
      .catch(() => {});
  }, [open]);

  const execSystem = useCallback(async (id: string) => {
    onClose();
    if (id === "kill") {
      await fetch("/api/kill-switch", { method: "POST", body: JSON.stringify({ activate: true }), headers: { "Content-Type": "application/json" } });
    } else if (id === "resume") {
      await fetch("/api/kill-switch", { method: "POST", body: JSON.stringify({ activate: false }), headers: { "Content-Type": "application/json" } });
    } else if (id === "paper") {
      await fetch("/api/trading-mode", { method: "POST", body: JSON.stringify({ mode: "paper" }), headers: { "Content-Type": "application/json" } });
    } else if (id === "auto") {
      await fetch("/api/trading-mode", { method: "POST", body: JSON.stringify({ mode: "auto" }), headers: { "Content-Type": "application/json" } });
    }
  }, [onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/60 backdrop-blur-sm animate-palette-in"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-cyan-500/30 shadow-2xl overflow-hidden animate-modal-in"
        style={{ background: "rgba(5,8,16,0.97)" }}
      >
        <Command
          className="[&_[cmdk-input-wrapper]]:border-b [&_[cmdk-input-wrapper]]:border-[#1e2d40]"
          onKeyDown={e => { if (e.key === "Escape") onClose(); }}
        >
          {/* Input */}
          <div className="flex items-center px-4 py-3 gap-2">
            <span className="text-gray-500 text-sm">⌘</span>
            <Command.Input
              autoFocus
              placeholder="Type a command or symbol…"
              className="flex-1 bg-transparent text-white text-sm outline-none placeholder:text-gray-600"
            />
            <kbd
              onClick={onClose}
              className="text-[10px] text-gray-600 border border-[#1e2d40] rounded px-1.5 py-0.5 cursor-pointer hover:text-gray-400"
            >
              ESC
            </kbd>
          </div>

          <Command.List className="max-h-80 overflow-y-auto py-2">
            <Command.Empty className="text-center text-gray-500 text-sm py-6">
              No results found.
            </Command.Empty>

            {/* SYMBOLS */}
            <Command.Group
              heading="Symbols"
              className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:text-gray-600 [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
            >
              {SYMBOLS.map(sym => (
                <Command.Item
                  key={sym}
                  value={`symbol ${sym}`}
                  onSelect={() => { onSymbolSelect?.(sym); onClose(); }}
                  className="flex items-center justify-between px-4 py-2 cursor-pointer text-sm text-gray-300
                             aria-selected:bg-cyan-500/10 aria-selected:text-cyan-300 rounded mx-2"
                >
                  <span className="font-mono font-bold">{sym}</span>
                  <span className="text-[10px] text-gray-600">Open chart →</span>
                </Command.Item>
              ))}
            </Command.Group>

            {/* NAVIGATION */}
            <Command.Group
              heading="Navigation"
              className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:text-gray-600 [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
            >
              {NAV_ITEMS.map(item => (
                <Command.Item
                  key={item.href}
                  value={`nav ${item.label}`}
                  onSelect={() => { router.push(item.href); onClose(); }}
                  className="flex items-center justify-between px-4 py-2 cursor-pointer text-sm text-gray-300
                             aria-selected:bg-cyan-500/10 aria-selected:text-cyan-300 rounded mx-2"
                >
                  <span>Go to {item.label}</span>
                  <kbd className="text-[10px] text-gray-600 border border-[#1e2d40] rounded px-1.5 py-0.5">
                    {item.shortcut}
                  </kbd>
                </Command.Item>
              ))}
            </Command.Group>

            {/* RECENT SIGNALS */}
            {signals.length > 0 && (
              <Command.Group
                heading="Recent Signals"
                className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:text-gray-600 [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
              >
                {signals.slice(0, 5).map((sig, i) => (
                  <Command.Item
                    key={i}
                    value={`signal ${sig.symbol} ${sig.direction}`}
                    onSelect={() => { onSymbolSelect?.(sig.symbol); onClose(); }}
                    className="flex items-center justify-between px-4 py-2 cursor-pointer text-sm text-gray-300
                               aria-selected:bg-cyan-500/10 aria-selected:text-cyan-300 rounded mx-2"
                  >
                    <span>
                      <span className="font-mono font-bold">{sig.symbol}</span>
                      <span className={`ml-2 text-xs ${sig.direction === "UP" ? "text-green-400" : "text-red-400"}`}>
                        {sig.direction}
                      </span>
                    </span>
                    <span className="text-[10px] text-gray-500">{sig.confidence ?? 0}%</span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            {/* SYSTEM COMMANDS */}
            <Command.Group
              heading="System Commands"
              className="[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:text-gray-600 [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5"
            >
              {SYSTEM_COMMANDS.map(cmd => (
                <Command.Item
                  key={cmd.id}
                  value={`cmd ${cmd.label}`}
                  onSelect={() => execSystem(cmd.id)}
                  className={`flex items-center px-4 py-2 cursor-pointer text-sm rounded mx-2
                              aria-selected:bg-opacity-20
                              ${cmd.danger
                                ? "text-red-400 aria-selected:bg-red-500/10"
                                : "text-gray-300 aria-selected:bg-cyan-500/10 aria-selected:text-cyan-300"
                              }`}
                >
                  {cmd.label}
                </Command.Item>
              ))}
            </Command.Group>

          </Command.List>

          {/* Footer */}
          <div className="flex items-center justify-between px-4 py-2 border-t border-[#1e2d40] text-[10px] text-gray-600">
            <span>↑↓ navigate</span>
            <span>↵ select</span>
            <span>ESC close</span>
          </div>
        </Command>
      </div>
    </div>
  );
}
