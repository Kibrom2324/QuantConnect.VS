import { NextResponse } from "next/server";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";

export async function GET() {
  try {
    const res = await fetch(`${APEX}/ensemble/weights`, { next: { revalidate: 0 } });
    return NextResponse.json(
      res.ok
        ? await res.json()
        : { weights: { TFT: 0.40, XGB: 0.35, Factor: 0.25 } }
    );
  } catch {
    return NextResponse.json({ weights: { TFT: 0.40, XGB: 0.35, Factor: 0.25 } });
  }
}
