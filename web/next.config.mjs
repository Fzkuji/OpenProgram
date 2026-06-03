/** @type {import('next').NextConfig} */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

/**
 * Resolve the backend URL once at config load.
 *
 * Order: explicit env var → <state-dir>/worker.port → default 18109.
 *
 * The state dir mirrors ``openprogram.paths.get_state_dir()``: the
 * canonical ``~/.openprogram`` (or ``~/.openprogram-<profile>`` when
 * ``OPENPROGRAM_PROFILE`` is set), with the legacy ``~/.agentic`` dir
 * checked only as a back-compat fallback. (Before the state-dir
 * unification this read ``~/.agentic`` and defaulted to 8765, which
 * left ``/ws`` proxying to a dead port once the worker moved to
 * ``~/.openprogram`` + the 18109 default.)
 */
function resolveBackend() {
  if (process.env.OPENPROGRAM_BACKEND_URL) {
    return process.env.OPENPROGRAM_BACKEND_URL;
  }
  const profile = (process.env.OPENPROGRAM_PROFILE || "").trim();
  const suffix = profile ? `-${profile}` : "";
  const candidates = [
    path.join(os.homedir(), `.openprogram${suffix}`, "worker.port"),
    path.join(os.homedir(), `.agentic${suffix}`, "worker.port"),
  ];
  for (const portFile of candidates) {
    try {
      const raw = fs.readFileSync(portFile, "utf-8").trim();
      const port = parseInt(raw, 10);
      if (Number.isFinite(port) && port > 0) {
        return `http://127.0.0.1:${port}`;
      }
    } catch {
      /* not present — try next candidate */
    }
  }
  // Worker's own default port (openprogram.webui defaults to 18109).
  return "http://127.0.0.1:18109";
}

const BACKEND = resolveBackend();

const nextConfig = {
  reactStrictMode: false,
  // Lint is a dev-time gate (`next lint` / editor), not a build blocker.
  // A stray unused-var or `<img>` warning must not fail the production
  // build the worker depends on (it was, silently breaking the build →
  // the frontend never came up while `next dev` masked it).
  eslint: { ignoreDuringBuilds: true },
  async rewrites() {
    return [
      // ``/api/*`` is handled by the dynamic route at
      // ``app/api/[...path]/route.ts`` which re-reads
      // ``<state-dir>/worker.port`` on every request, so it stays
      // live when the worker shifts ports. Don't add a static
      // rewrite here — it would compete with the route handler and
      // bake in a port at startup.
      { source: "/ws", destination: `${BACKEND}/ws` },
      { source: "/ws/:path*", destination: `${BACKEND}/ws/:path*` },
    ];
  },
};

export default nextConfig;
