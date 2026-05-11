/**
 * Catch-all proxy for ``/api/*`` requests.
 *
 * Reasons this exists instead of plain ``next.config.mjs:rewrites()``:
 *
 *  - ``rewrites()`` resolves its ``destination`` once at ``next start``
 *    boot time. If the worker shifts ports (TIME_WAIT, manual kill,
 *    SIGKILL with orphaned Next, etc.) the rewrite keeps proxying to
 *    the dead port and every API call silently fails. The user has
 *    no UI clue — the model picker just "doesn't take", the chat
 *    silently runs the agent default, etc.
 *  - A route handler runs in Node and can read the worker's port
 *    file (``~/.agentic/worker.port``) on every request. We cache the
 *    value with a short TTL so the hot path doesn't hammer the disk,
 *    but a port change is picked up within a second.
 *
 * WebSocket (``/ws``) still goes through ``next.config.mjs`` because
 * the App Router doesn't support a clean WS upgrade in route handlers
 * yet. WS surface is exercised less frequently and reconnects on
 * change anyway, so the staleness window is shorter-lived there.
 */

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
// Forwarded responses can stream (SSE / long polls); don't let Next
// try to cache them — every request must hit the live backend.
export const dynamic = "force-dynamic";

let _cachedPort: number | null = null;
let _cachedAt = 0;
const PORT_TTL_MS = 1_000;

function resolveBackendPort(): number {
  const now = Date.now();
  if (_cachedPort !== null && now - _cachedAt < PORT_TTL_MS) {
    return _cachedPort;
  }
  // Order: env (set by worker at spawn time) → worker.port file → 8765.
  const envUrl = process.env.OPENPROGRAM_BACKEND_URL;
  if (envUrl) {
    const m = envUrl.match(/:(\d+)/);
    if (m) {
      _cachedPort = parseInt(m[1], 10);
      _cachedAt = now;
      return _cachedPort;
    }
  }
  try {
    const portFile = path.join(os.homedir(), ".agentic", "worker.port");
    const raw = fs.readFileSync(portFile, "utf-8").trim();
    const p = parseInt(raw, 10);
    if (Number.isFinite(p) && p > 0) {
      _cachedPort = p;
      _cachedAt = now;
      return p;
    }
  } catch {
    /* ignore — fall through to default */
  }
  _cachedPort = 8765;
  _cachedAt = now;
  return 8765;
}

async function proxy(req: NextRequest): Promise<Response> {
  const port = resolveBackendPort();
  // Preserve the full path + query string.
  const url = new URL(req.nextUrl.pathname + req.nextUrl.search, `http://127.0.0.1:${port}`);

  // Forward request headers except hop-by-hop ones that fetch would
  // re-derive (host) or that the upstream doesn't need.
  const headers = new Headers();
  req.headers.forEach((v, k) => {
    const lower = k.toLowerCase();
    if (lower === "host" || lower === "connection" || lower === "content-length") return;
    headers.set(k, v);
  });

  let body: BodyInit | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = req.body ?? undefined;
  }

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body,
      // @ts-expect-error — Node fetch needs duplex for streamed bodies.
      duplex: body ? "half" : undefined,
      redirect: "manual",
    });
  } catch (err) {
    return new Response(
      JSON.stringify({
        error: "backend_unreachable",
        message: `Worker on 127.0.0.1:${port} did not respond: ${(err as Error).message}`,
      }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  // Pass through status + headers + body stream. Strip
  // ``content-encoding`` if upstream double-encodes; the Node fetch
  // we just made already decoded it for us.
  const resHeaders = new Headers();
  upstream.headers.forEach((v, k) => {
    const lower = k.toLowerCase();
    if (lower === "content-encoding" || lower === "transfer-encoding") return;
    resHeaders.set(k, v);
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: resHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
