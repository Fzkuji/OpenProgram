"use client";

import { useEffect, useMemo, useRef } from "react";
import { marked as npmMarked, Marked, type Tokens } from "marked";
import { typesetMath } from "@/lib/runtime-bridge/dom/markdown-render";
import { renderMermaidLazily } from "@/lib/chat/markdown/mermaid-lazy";
import { createDocumentMarkdown } from "./document-markdown";
import { cjkEmphasis } from "@/lib/markdown/cjk-emphasis";

// Markdown + KaTeX rendering. Mirrors `renderMd()` and
// `renderMathInChat()` from apps/web/public/js/shared/helpers.js.
// Both libraries are build-time imports so offline and strict-CSP behavior
// matches the normal online path.

/** Maps a local document image to its authorized URL. Null rejects an
 * untrusted document image; the legacy trusted path retains its fallback. */
export type MarkdownImageResolver = (src: string) => string | null;

function imageResolvingMarked(resolveImage: MarkdownImageResolver): Marked {
  return new Marked(cjkEmphasis, {
    walkTokens(token) {
      if (token.type !== "image") return;
      const image = token as Tokens.Image;
      image.href = resolveImage(image.href) ?? image.href;
    },
  });
}

/** Replace LaTeX blocks with placeholder tokens so marked doesn't mangle
 * them, then restore the originals after parsing. */
function markdownToHtml(src: string, escapeRawHtml?: boolean, parser?: Marked): string {
  let s = typeof src === "string" ? src : String(src ?? "");
  if (escapeRawHtml) {
    const documentParser = parser ?? createDocumentMarkdown();
    return '<span class="md-rendered">' + documentParser.parse(s, { breaks: true, async: false }) + "</span>";
  }

  const mathBlocks: string[] = [];
  const stash = (m: string) => {
    mathBlocks.push(m);
    return "%%MATH" + (mathBlocks.length - 1) + "%%";
  };
  s = s.replace(/\$\$([\s\S]*?)\$\$/g, (m) => stash(m));
  s = s.replace(/\\\[([\s\S]*?)\\\]/g, (m) => stash(m));
  s = s.replace(/\\\(([\s\S]*?)\\\)/g, (m) => stash(m));
  s = s.replace(/\$([^$\n]+?)\$/g, (m) => stash(m));

  let html: string;
  if (parser) {
    html = parser.parse(s, { breaks: true, async: false });
  } else {
    html = npmMarked.parse(s, { breaks: true }) as string;
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
  typesetMath(el);
  renderMermaidLazily(el);
}

/** Render a markdown + LaTeX string. Produces the same DOM shape
 * (`<span class="md-rendered">...</span>`) the legacy CSS targets.
 *
 * `escapeRawHtml` is for untrusted sources (e.g. arbitrary repo .md files
 * in the files panel): HTML/math remain escaped and destinations follow
 * the document URL policy. Callers rendering user, model or file-authored text
 * must opt into this policy. The unset legacy mode preserves raw HTML;
 * use it only with an explicit trusted-markup contract. */
export function Markdown({
  source,
  escapeRawHtml,
  resolveImage,
  obsidian,
}: {
  source: string;
  escapeRawHtml?: boolean;
  resolveImage?: MarkdownImageResolver;
  /** With escapeRawHtml: Obsidian's callouts, highlights, tags, wikilinks. */
  obsidian?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const parser = useMemo(() => escapeRawHtml ? createDocumentMarkdown(resolveImage, obsidian)
    : resolveImage ? imageResolvingMarked(resolveImage) : undefined,
    [resolveImage, escapeRawHtml, obsidian]);
  const html = markdownToHtml(source, escapeRawHtml, parser);

  // KaTeX auto-render mutates the DOM after marked has produced HTML.
  // Run it after every render of the host node so streaming text gets
  // typeset as it arrives.
  useEffect(() => {
    if (ref.current) renderMathIn(ref.current);
  });

  // `dangerouslySetInnerHTML` is intentional — the HTML comes from our
  // own marked invocation. Note marked does NOT sanitize: raw HTML in
  // the markdown body passes straight through to the DOM. Callers with
  // untrusted input must set `escapeRawHtml`, which uses the separate
  // document parser for raw HTML, math and URL policy enforcement.
  return (
    <div
      ref={ref}
      style={{ display: "contents" }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
