/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: `next build` emits plain HTML/JS/CSS into apps/web/out/,
  // served by the Python worker (single-port architecture — see
  // docs/reference/design/cli/single-port.md). No rewrites, no baked
  // backend port: the app talks to its own origin (/api, /ws) at runtime.
  output: "export",
  reactStrictMode: false,
  // Lint is a dev-time gate (`next lint` / editor), not a build blocker.
  // A stray unused-var or `<img>` warning must not fail the production
  // build the worker depends on (it was, silently breaking the build →
  // the frontend never came up while `next dev` masked it).
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
};

export default nextConfig;
