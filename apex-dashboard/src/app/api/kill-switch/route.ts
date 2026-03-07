import { NextRequest, NextResponse } from "next/server";
import { existsSync, writeFileSync, unlinkSync } from "fs";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";
const USE_MOCK = process.env.USE_MOCK_DATA === "true";
const KILL_FLAG = "/tmp/apex_kill.flag";
const KILL_JSON = "/tmp/apex_kill_switch.json";

export async function GET() {
  // Check file flag first (instant, no network)
  const fileActive = existsSync(KILL_FLAG);

  try {
    const res = await fetch(`${APEX}/kill-switch`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // File flag takes precedence — if file says kill, we're killed
    return NextResponse.json({
      active: data.active || fileActive,
      method: fileActive ? "file" : "api",
      is_mock: false,
    });
  } catch {
    if (USE_MOCK) {
      return NextResponse.json({ active: fileActive, method: fileActive ? "file" : "mock", is_mock: true });
    }
    return NextResponse.json({ active: fileActive, method: fileActive ? "file" : "unknown", is_mock: false, error: "offline" });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const active: boolean = !!body.active;
    const reason: string = body.reason ?? (active ? "Manual emergency stop" : "Kill switch deactivated");
    const timestamp = new Date().toISOString();

    if (active) {
      // Write both flag files so both GET and trade-route checks are satisfied
      writeFileSync(KILL_FLAG, "");
      writeFileSync(KILL_JSON, JSON.stringify({ active: true, reason, timestamp }));
    } else {
      if (existsSync(KILL_FLAG)) unlinkSync(KILL_FLAG);
      if (existsSync(KILL_JSON)) unlinkSync(KILL_JSON);
    }

    // Also notify APEX service (best-effort)
    try {
      await fetch(`${APEX}/kill-switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active, reason }),
        signal: AbortSignal.timeout(2000),
      });
    } catch {
      // APEX service offline — file flag is still set, so trading is blocked
    }

    return NextResponse.json({ active, reason, timestamp, method: "file" });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
