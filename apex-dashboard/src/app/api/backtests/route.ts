import { NextResponse } from "next/server";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";

export async function GET() {
  try {
    const res = await fetch(`${APEX}/backtests/files`, { next: { revalidate: 0 } });
    return NextResponse.json(res.ok ? await res.json() : { files: [] });
  } catch {
    return NextResponse.json({ files: [] });
  }
}
