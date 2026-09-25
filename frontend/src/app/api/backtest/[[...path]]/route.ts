import { type NextRequest } from "next/server";

/**
 * The Next rewrite proxy times out on a full backtest report (~1MB).
 * This route fetches FastAPI itself so the desk can load /backtest.
 */

const API = process.env.ALGOE_API_URL ?? "http://127.0.0.1:8001";

async function proxy(req: NextRequest, path: string[]) {
  const suffix = path.length ? `/${path.join("/")}` : "";
  const target = `${API}/api/backtest${suffix}${req.nextUrl.search}`;
  const init: RequestInit = {
    method: req.method,
    cache: "no-store",
    headers: {
      "content-type": req.headers.get("content-type") ?? "application/json",
    },
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }
  const res = await fetch(target, init);
  return new Response(await res.arrayBuffer(), {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") ?? "application/json",
    },
  });
}

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await ctx.params;
  return proxy(req, path ?? []);
}

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await ctx.params;
  return proxy(req, path ?? []);
}
