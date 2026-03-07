import { NextResponse } from "next/server";
import { unlinkSync, existsSync } from "fs";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";
const KILL_FLAG = "/tmp/apex_kill.flag";

export async function POST() {
  // Remove file flag immediately
  let fileOk = false;
  try {
    if (existsSync(KILL_FLAG)) unlinkSync(KILL_FLAG);
    fileOk = true;
  } catch { /* ignore */ }

  // Try API as well
  let apiOk = false;
  try {
    const res = await fetch(`${APEX}/kill-switch/disable`, {
      method: "POST",
      signal: AbortSignal.timeout(2000),
    });
    apiOk = res.ok;
  } catch { /* ignore */ }

  const method = apiOk && fileOk ? "both" : fileOk ? "file" : apiOk ? "api" : "unknown";
  return NextResponse.json({ activated: false, method, deactivated_at: new Date().toISOString() });
}
