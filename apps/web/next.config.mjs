import { fileURLToPath } from "node:url";

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
  webpack(config) {
    // Webpack must invalidate cached CSS when our local PostCSS plugin changes.
    if (config.cache && typeof config.cache === "object") {
      const dependencies = config.cache.buildDependencies ?? {};
      config.cache.buildDependencies = {
        ...dependencies,
        config: [...(dependencies.config ?? []),
          fileURLToPath(new URL("./postcss.config.mjs", import.meta.url)),
          fileURLToPath(new URL("./scripts/postcss-katex-fonts.cjs", import.meta.url))],
      };
    }
    return config;
  },
};

export default nextConfig;
