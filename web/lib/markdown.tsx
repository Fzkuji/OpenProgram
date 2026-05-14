"use client";

import { useEffect, useRef } from "react";
import { escHtml } from "./format";

// Markdown + KaTeX rendering. Mirrors `renderMd()` and
// `renderMathInChat()` from web/public/js/shared/helpers.js.
//
// `marked` is an installed npm dep, but AppShell ALSO loads it from a
// CDN <script> tag for legacy callers, and `katex` is loaded ONLY from
// CDN. To avoid duplicate parse implementations and to stay consistent
// with what the legacy chat renderer outputs, we read both libs off
// `window.*` (typed in lib/cdn-globals.d.ts). When `window.marked` is
// not yet ready, we fall back to a `<pre>`-wrapped escape so the user
// sees something instead of an empty bubble.
//
// TODO: once every caller of legacy `renderMd()` is migrated, swap the
// CDN `marked` for the npm import and drop the global lookup.

/** Replace LaTeX blocks with placeholder tokens so marked doesn't mangle
 * them, then restore the originals after parsing. */
function markdownToHtml(src: string): string {
  let s = typeof src === "string" ? src : String(src ?? "");
  const marked = typeof window !== "undefined" ? window.marked : undefined;
  if (!marked) {
    return "<pre>" + escHtml(s) + "</pre>";
  }

  const mathBlocks: string[] = [];
  const stash = (m: string) => {
    mathBlocks.push(m);
    return "%%MATH" + (mathBlocks.length - 1) + "%%";
  };
  // $$...$$ (display)
  s = s.replace(/\$\$([\s\S]*?)\$\$/g, (m) => stash(m));
  // \[...\] (display)
  s = s.replace(/\\\[([\s\S]*?)\\\]/g, (m) => stash(m));
  // \(...\) (inline)
  s = s.replace(/\\\(([\s\S]*?)\\\)/g, (m) => stash(m));
  // $...$ (inline, single line only)
  s = s.replace(/\$([^$\n]+?)\$/g, (m) => stash(m));

  let html = marked.parse(s, { breaks: true });
  for (let i = 0; i < mathBlocks.length; i++) {
    html = html.replace("%%MATH" + i + "%%", mathBlocks[i]);
  }
  return '<span class="md-rendered">' + html + "</span>";
}

/** Run KaTeX auto-render against a specific container. Memoizes via a
 * `data-math-rendered` attribute so streaming updates don't re-render
 * already-typeset spans. */
export function renderMathIn(el: HTMLElement): void {
  if (typeof window === "undefined") return;
  const render = window.renderMathInElement;
  if (!render) return;
  el.querySelectorAll<HTMLElement>(".md-rendered").forEach((node) => {
    if (node.dataset.mathRendered) return;
    render(node, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
    node.dataset.mathRendered = "1";
  });
}

/** Render a markdown + LaTeX string. Produces the same DOM shape
 * (`<span class="md-rendered">...</span>`) the legacy CSS targets. */
export function Markdown({ source }: { source: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = markdownToHtml(source);

  // KaTeX auto-render mutates the DOM after marked has produced HTML.
  // Run it after every render of the host node so streaming text gets
  // typeset as it arrives.
  useEffect(() => {
    if (ref.current) renderMathIn(ref.current);
  });

  // `dangerouslySetInnerHTML` is intentional — the source HTML comes
  // from our own marked invocation (the only untrusted bit is the
  // markdown body, which marked sanitizes internally).
  return (
    <div
      ref={ref}
      style={{ display: "contents" }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
