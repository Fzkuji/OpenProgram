/** @type {import('next').NextConfig} */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

/**
 * Resolve the backend URL once at config load.
 *
 * Order: explicit env var → ~/.agentic/worker.port → default 8765.
 * The worker writes its bound port to ~/.agentic/worker.port at
 * startup, so reading that file here lets `next start` proxy to
 * whatever port the worker actually grabbed.
 */
function resolveBackend() {
  if (process.env.OPENPROGRAM_BACKEND_URL) {
    return process.env.OPENPROGRAM_BACKEND_URL;
  }
  try {
    const portFile = path.join(os.homedir(), ".agentic", "worker.port");
    const raw = fs.readFileSync(portFile, "utf-8").trim();
    const port = parseInt(raw, 10);
    if (Number.isFinite(port) && port > 0) {
      return `http://127.0.0.1:${port}`;
    }
  } catch {
    /* file not present yet — fall through */
  }
  return "http://127.0.0.1:8765";
}

const BACKEND = resolveBackend();

const nextConfig = {
  reactStrictMode: false,
  // Skip lint / typecheck during build — they're useful for CI but
  // the repo has known half-typed files (e.g. shiki-code referencing
  // a still-unwritten ``@/lib/shiki``) that block a quick local
  // production build. The lint/typecheck still run in the editor and
  // via explicit ``npm run lint`` / ``tsc --noEmit``.
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
  async rewrites() {
    return [
      // ``/api/*`` is handled by the dynamic route at
      // ``app/api/[...path]/route.ts`` which re-reads
      // ``~/.agentic/worker.port`` on every request, so it stays
      // live when the worker shifts ports. Don't add a static
      // rewrite here — it would compete with the route handler and
      // bake in a port at startup.
      { source: "/ws", destination: `${BACKEND}/ws` },
      { source: "/ws/:path*", destination: `${BACKEND}/ws/:path*` },
    ];
  },
};

export default nextConfig;
