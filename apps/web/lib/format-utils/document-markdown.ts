import { decodeHTMLAttribute } from "entities";
import { Marked } from "marked";
import { proseMarkdown } from "@/lib/markdown/prose";
import { mathMarkdown } from "@/lib/markdown/math";
import { markdownTokenDestination } from "@/lib/files/markdown-paths";
import { obsidianExtensions, sectionKey } from "./obsidian-document";

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** `obsidian` adds Obsidian's reading-view syntax (callouts, highlights,
 *  tags, wikilinks) for file previews. */
export function createDocumentMarkdown(resolveImage?: (src: string) => string | null, obsidian = false): Marked {
  const headings = new Set<string>();
  return new Marked(...proseMarkdown, mathMarkdown, {
    hooks: { preprocess(source) { headings.clear(); return source; } },
    extensions: obsidian ? obsidianExtensions(resolveImage) ?? [] : [],
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
