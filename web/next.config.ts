import type { NextConfig } from "next";

/**
 * Backend (FastAPI + WebSocket) runs on a separate port.
 * Set OPENPROGRAM_BACKEND_URL to point at it. Defaults to the
 * conventional dev port if unset.
 */
const BACKEND = process.env.OPENPROGRAM_BACKEND_URL || "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      { source: "/ws", destination: `${BACKEND}/ws` },
      { source: "/ws/:path*", destination: `${BACKEND}/ws/:path*` },
      { source: "/healthz", destination: `${BACKEND}/healthz` },
    ];
  },
};

export default nextConfig;
