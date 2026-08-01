/**
 * Markdown → HTML for chat bubbles.
 *
 * Delegates to the shared `renderMd` (runtime-bridge/helpers) so React
 * bubbles render byte-for-byte identically to every other renderer —
 * no second markdown engine. Falls back to escaped plain text on SSR,
 * where `renderMd` would touch `window`.
 */
import { useEffect, useState } from "react";

import { renderMd } from "@/lib/runtime-bridge/helpers";

export function renderMarkdown(src: string): string {
  if (typeof window === "undefined") return escapeHtml(src);
  try {
    return renderMd(src);
  } catch {
    return escapeHtml(src);
  }
}

/**
 * Re-render gate for markdown.
 *
 * `renderMd` needs the `marked` CDN lib on `window` — an async load
 * (app-shell's `__sharedScriptsReady`) that usually finishes *after*
 * the first bubble paints, and until then `renderMd` emits a `<pre>`
 * escaped-text fallback. Without this hook a bubble rendered early
 * would keep that fallback forever. The hook flips to `true` once
 * `marked` lands, forcing the consuming bubble to re-render and pick
 * up real markdown.
 */
export function useMarkdownReady(): boolean {
  const has = () => typeof window !== "undefined" && !!window.marked;
  const [ready, setReady] = useState<boolean>(has);

  useEffect(() => {
    if (ready) return;
    let cancelled = false;
    const w = window as unknown as {
      __sharedScriptsReady?: Promise<void>;
    };
    if (has()) {
      setReady(true);
      return;
    }
    const done = () => {
      if (!cancelled && has()) setReady(true);
    };
    w.__sharedScriptsReady?.then(done);
    // Poll as a backstop — the promise resolves before `marked` is
    // actually assigned in some load orderings.
    const t = setInterval(() => {
      if (has()) {
        done();
        clearInterval(t);
      }
    }, 120);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [ready]);

  return ready;
}

export function escapeHtml(s: string): string {
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
