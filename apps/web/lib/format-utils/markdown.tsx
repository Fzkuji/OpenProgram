"use client";

import { useEffect, useMemo, useRef } from "react";
import { marked as npmMarked, Marked, type Tokens } from "marked";
import { typesetMath } from "@/lib/runtime-bridge/dom/markdown-render";
import { renderMermaidLazily } from "@/lib/chat/markdown/mermaid-lazy";
import { createDocumentMarkdown } from "./document-markdown";
import { proseMarkdown } from "@/lib/markdown/prose";
import { mathMarkdown } from "@/lib/markdown/math";

// Markdown + KaTeX rendering. Mirrors `renderMd()` and
// `renderMathInChat()` from apps/web/public/js/shared/helpers.js.
// Both libraries are build-time imports so offline and strict-CSP behavior
// matches the normal online path.

/** Maps a local document image to its authorized URL. Null rejects an
 * untrusted document image; the legacy trusted path retains its fallback. */
export type MarkdownImageResolver = (src: string) => string | null;

function imageResolvingMarked(resolveImage: MarkdownImageResolver): Marked {
  return new Marked(...proseMarkdown, mathMarkdown, {
    walkTokens(token) {
      if (token.type !== "image") return;
      const image = token as Tokens.Image;
      image.href = resolveImage(image.href) ?? image.href;
    },
  });
}

/** Markdown to HTML. Formulas are found by the lexer and come out as
 * `.md-math` elements holding their escaped source (lib/markdown/math.ts). */
function markdownToHtml(src: string, escapeRawHtml?: boolean, parser?: Marked): string {
  let s = typeof src === "string" ? src : String(src ?? "");
  if (escapeRawHtml) {
    const documentParser = parser ?? createDocumentMarkdown();
    return '<span class="md-rendered">' + documentParser.parse(s, { breaks: true, async: false }) + "</span>";
  }

  // Formulas are tokens of the shared marked (lib/markdown/math.ts).
  let html: string;
  if (parser) {
    html = parser.parse(s, { breaks: true, async: false });
  } else {
    html = npmMarked.parse(s, { breaks: true }) as string;
  }
  return '<span class="md-rendered">' + html + "</span>";
}

/** Typeset the formulas in a container. Each `.md-math` element is marked
 * `data-math-rendered`, so streaming updates don't re-render it. */
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

  // KaTeX draws the formulas into the DOM after marked has produced HTML.
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
