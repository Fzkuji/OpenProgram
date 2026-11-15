import { decodeHTMLAttribute } from "entities";
import { Marked, type TokenizerExtension, type RendererExtension } from "marked";
import { cjkEmphasis } from "@/lib/markdown/cjk-emphasis";
import { markdownTokenDestination } from "@/lib/files/markdown-paths";
import { obsidianExtensions, sectionKey } from "./obsidian-document";

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Extensions run on lexer input, so code tokens remain literal. Math produces
// escaped text nodes for KaTeX, never a post-parse raw HTML replacement.
function mathExtension(block: boolean): TokenizerExtension & RendererExtension {
  const name = block ? "documentBlockMath" : "documentInlineMath";
  const pattern = block ? /^(?:\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\])(?:\n|$)/
    : /^(?:\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+?\$)/;
  return {
    name, level: block ? "block" : "inline",
    ...(!block ? { start(source: string) { const index = source.search(/\$|\\[([]/); return index < 0 ? undefined : index; } } : {}),
    tokenizer(source) { const match = pattern.exec(source); if (match) return { type: name, raw: match[0], text: match[0] }; },
    renderer(token) { const tag = block ? "div" : "span"; return `<${tag}>${escapeHtml(token.text)}</${tag}>`; },
  };
}

/** `obsidian` adds Obsidian's reading-view syntax (callouts, highlights,
 *  tags, wikilinks) for file previews. */
export function createDocumentMarkdown(resolveImage?: (src: string) => string | null, obsidian = false): Marked {
  const headings = new Set<string>();
  return new Marked(cjkEmphasis, {
    hooks: { preprocess(source) { headings.clear(); return source; } },
    extensions: [mathExtension(true), mathExtension(false), ...(obsidian ? obsidianExtensions(resolveImage) ?? [] : [])],
    renderer: {
      heading(token) {
        const label = this.parser.parseInline(token.tokens);
        const text = decodeHTMLAttribute(this.parser.parseInline(token.tokens, this.parser.textRenderer));
        const base = sectionKey(text);
        let key = base;
        for (let suffix = 1; headings.has(key); suffix++) key = `${base}-${suffix}`;
        headings.add(key);
        return `<h${token.depth} data-section-key="${escapeHtml(key)}">${label}</h${token.depth}>\n`;
      },
      html(token) { return escapeHtml(token.text); },
      link(token) {
        const label = this.parser.parseInline(token.tokens);
        const destination = markdownTokenDestination(token.href);
        if (destination.kind === "rejected") return label;
        const title = token.title ? ` title="${escapeHtml(decodeHTMLAttribute(token.title))}"` : "";
        return `<a href="${escapeHtml(destination.href)}"${title}>${label}</a>`;
      },
      image(token) {
        const destination = markdownTokenDestination(token.href, true);
        const src = destination.kind === "external" ? destination.href
          : destination.kind === "local" ? resolveImage?.(destination.href) : null;
        const alt = decodeHTMLAttribute(this.parser.parseInline(token.tokens, this.parser.textRenderer));
        if (!src) return escapeHtml(alt);
        const title = token.title ? ` title="${escapeHtml(decodeHTMLAttribute(token.title))}"` : "";
        return `<img src="${escapeHtml(src)}" alt="${escapeHtml(alt)}"${title}>`;
      },
    },
  });
}
