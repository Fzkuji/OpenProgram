"use client";

import { useEffect, useRef } from "react";
import { marked as npmMarked, Marked } from "marked";

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

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Dedicated marked instance for untrusted sources (repo files): raw HTML
// tokens — block and inline both surface as `html` tokens — are escaped
// instead of passed through. Separate instance so the override can never
// leak into window.marked / npmMarked and change chat rendering.
const escapingMarked = new Marked({
  renderer: {
    html(token) {
      return escapeHtml(token.text);
    },
  },
});

/** Replace LaTeX blocks with placeholder tokens so marked doesn't mangle
 * them, then restore the originals after parsing. */
function markdownToHtml(src: string, escapeRawHtml?: boolean): string {
  let s = typeof src === "string" ? src : String(src ?? "");

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

  let html: string;
  if (escapeRawHtml) {
    html = escapingMarked.parse(s, { breaks: true, async: false });
  } else {
    // Prefer window.marked (legacy chat CSS targets its exact output); fall
    // back to the npm-bundled marked so detail pages render even before the
    // CDN <script> has loaded. Both produce compatible HTML.
    const marked =
      (typeof window !== "undefined" ? window.marked : undefined) ?? npmMarked;
    html = marked.parse(s, { breaks: true });
  }
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
 * (`<span class="md-rendered">...</span>`) the legacy CSS targets.
 *
 * `escapeRawHtml` is for untrusted sources (e.g. arbitrary repo .md files
 * in the files panel): raw HTML in the markdown is escaped and shown as
 * text instead of injected into the DOM. Leave it unset for trusted
 * chat/doc content — that path is byte-identical to the original. */
export function Markdown({
  source,
  escapeRawHtml,
}: {
  source: string;
  escapeRawHtml?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const html = markdownToHtml(source, escapeRawHtml);

  // KaTeX auto-render mutates the DOM after marked has produced HTML.
  // Run it after every render of the host node so streaming text gets
  // typeset as it arrives.
  useEffect(() => {
    if (ref.current) renderMathIn(ref.current);
  });

  // `dangerouslySetInnerHTML` is intentional — the HTML comes from our
  // own marked invocation. Note marked does NOT sanitize: raw HTML in
  // the markdown body passes straight through to the DOM. Callers with
  // untrusted input must set `escapeRawHtml`, which escapes raw HTML
  // tokens at parse time; no sanitization happens anywhere else.
  return (
    <div
      ref={ref}
      style={{ display: "contents" }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
