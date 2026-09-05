/**
 * Markdown → HTML for chat bubbles.
 *
 * Delegates to the shared `renderMd` (runtime-bridge/helpers) so React
 * bubbles render byte-for-byte identically to every other renderer —
 * no second markdown engine. Falls back to escaped plain text on SSR,
 * where `renderMd` would touch `window`.
 */
import { renderMd } from "@/lib/runtime-bridge/helpers";

export function renderMarkdown(src: string): string {
  if (typeof window === "undefined") return escapeHtml(src);
  try {
    return renderMd(src);
  } catch {
    return escapeHtml(src);
  }
}

/** Compatibility hook retained for callers that used to wait for CDN marked. */
export function useMarkdownReady(): boolean {
  return true;
}

/** SSR/no-`marked` fallback escape. Deliberately NOT
 *  `runtime-bridge/helpers.escHtml` — that one escapes via
 *  `document.createElement`, and the only callers here are the two
 *  branches where the DOM is unavailable or `renderMd` just threw. */
function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c] as string,
  );
}
