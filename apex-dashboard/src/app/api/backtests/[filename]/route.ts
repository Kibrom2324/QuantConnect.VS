import { NextRequest, NextResponse } from "next/server";

const APEX = process.env.APEX_API_URL ?? "http://localhost:8000";

export async function GET(
  _req: NextRequest,
  { params }: { params: { filename: string } }
) {
  const filename = params.filename;
  // Basic sanitization — no slashes or path traversal
  if (!filename || /[/\\.]/.test(filename.replace(/\.json$/, ""))) {
    return NextResponse.json({ error: "Invalid filename" }, { status: 400 });
  }
  try {
    const res = await fetch(
      `${APEX}/backtests/files/${encodeURIComponent(filename)}`,
      { next: { revalidate: 0 } }
    );
    if (!res.ok) return NextResponse.json({ error: `Not found: ${filename}` }, { status: 404 });
    return NextResponse.json(await res.json());
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
