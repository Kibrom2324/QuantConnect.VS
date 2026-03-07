import { NextResponse } from "next/server";
import { writeFileSync } from "fs";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";
const KILL_FLAG = "/tmp/apex_kill.flag";

export async function POST() {
  // Write file flag immediately — always works
  let fileOk = false;
  try {
    writeFileSync(KILL_FLAG, new Date().toISOString(), "utf8");
    fileOk = true;
  } catch { /* ignore */ }

  // Try API as well
  let apiOk = false;
  try {
    const res = await fetch(`${APEX}/kill-switch/enable`, {
      method: "POST",
      signal: AbortSignal.timeout(2000),
    });
    apiOk = res.ok;
  } catch { /* ignore */ }

  const method = apiOk && fileOk ? "both" : fileOk ? "file" : apiOk ? "api" : "unknown";
  return NextResponse.json({ activated: true, method, activated_at: new Date().toISOString() });
}
