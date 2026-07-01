import type { MetadataRoute } from "next";

/**
 * PWA manifest — lets the user "Install app" from Chrome's address bar so
 * OpenProgram opens in its OWN window with no tab strip and no address bar
 * (display: standalone), i.e. looks like a native desktop app.
 *
 * Next serves this at /manifest.webmanifest and auto-injects the
 * <link rel="manifest"> — no edit to layout.tsx needed.
 *
 * Requirements for Chrome to offer install: a valid manifest + at least one
 * PNG icon >=192px (Chrome does NOT accept SVG-only). Icons live in
 * public/icons/, rasterized from app/icon.svg.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "OpenProgram",
    short_name: "OpenProgram",
    description: "Agentic programming runtime",
    start_url: "/",
    scope: "/",
    display: "standalone",
    // window-controls-overlay (if the browser supports it) draws the app into
    // the title-bar area for an even more chromeless look; falls back to
    // standalone, then minimal-ui.
    display_override: ["window-controls-overlay", "standalone", "minimal-ui"],
    background_color: "#1f1f1e",
    theme_color: "#1f1f1e",
    orientation: "any",
    icons: [
      { src: "/icons/icon-192.png", type: "image/png", sizes: "192x192" },
      { src: "/icons/icon-512.png", type: "image/png", sizes: "512x512" },
      {
        src: "/icons/icon-512-maskable.png",
        type: "image/png",
        sizes: "512x512",
        purpose: "maskable",
      },
      { src: "/icon.svg", type: "image/svg+xml", sizes: "any" },
    ],
  };
}
