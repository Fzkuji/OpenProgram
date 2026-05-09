/** @type {import('next').NextConfig} */
const BACKEND = process.env.OPENPROGRAM_BACKEND_URL || "http://localhost:8765";

const nextConfig = {
  reactStrictMode: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      { source: "/ws", destination: `${BACKEND}/ws` },
      { source: "/ws/:path*", destination: `${BACKEND}/ws/:path*` },
    ];
  },
};

export default nextConfig;
