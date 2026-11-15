import type { MarkedExtension, Tokens } from "marked";
import { markdownTokenDestination } from "@/lib/files/markdown-paths";

// Obsidian's reading-view syntax for document previews: callouts,
// ==highlights==, #tags and [[wikilinks]] / ![[embeds]]. Every extension
// emits escaped text or parsed child tokens, never source-authored HTML,
// so the document parser's raw-HTML policy still holds.

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** The heading key the document renderer gives a heading's text. */
export function sectionKey(text: string): string {
  return text.normalize("NFC").toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").trim().replace(/\s+/g, "-") || "section";
}

const CALLOUT_ALIASES: Record<string, string> = {
  summary: "abstract", tldr: "abstract", hint: "tip", important: "tip",
  check: "success", done: "success", help: "question", faq: "question",
  caution: "warning", attention: "warning", fail: "failure", missing: "failure",
  error: "danger", cite: "quote",
};
const CALLOUT_TYPES = new Set(["note", "abstract", "info", "todo", "tip", "success", "question", "warning", "failure", "danger", "bug", "example", "quote"]);

/** `> [!type]± title` quotes become callouts (foldable with + or -),
 *  `==text==` a highlight and `#tag` a tag pill. */
const syntax: NonNullable<MarkedExtension["extensions"]> = [{
  name: "obsidianCallout",
  level: "block",
  start(source) { const index = source.search(/^ {0,3}>[ \t]*\[!/m); return index < 0 ? undefined : index; },
  tokenizer(source) {
    const match = /^ {0,3}>[ \t]*\[!([\w-]+)\]([+-]?)[ \t]*([^\n]*)(?:\n|$)((?: {0,3}>[^\n]*(?:\n|$))*)/.exec(source);
    if (!match) return;
    const kind = match[1].toLowerCase();
    const body = match[4].replace(/^ {0,3}>[ \t]?/gm, "");
    const title = match[3].trim() || kind.charAt(0).toUpperCase() + kind.slice(1);
    return {
      type: "obsidianCallout", raw: match[0], kind, fold: match[2],
      titleTokens: this.lexer.inlineTokens(title), tokens: this.lexer.blockTokens(body, []),
    };
  },
  renderer(token) {
    const family = CALLOUT_ALIASES[token.kind] ?? (CALLOUT_TYPES.has(token.kind) ? token.kind : "note");
    const title = this.parser.parseInline(token.titleTokens);
    const body = token.tokens?.length ? `<div class="callout-content">${this.parser.parse(token.tokens)}</div>` : "";
    const attrs = `class="callout" data-callout="${escapeHtml(family)}"`;
    if (!token.fold) return `<div ${attrs}><div class="callout-title">${title}</div>${body}</div>\n`;
    return `<details ${attrs}${token.fold === "+" ? " open" : ""}><summary class="callout-title">${title}</summary>${body}</details>\n`;
  },
}, {
  name: "obsidianHighlight",
  level: "inline",
  start(source) { const index = source.indexOf("=="); return index < 0 ? undefined : index; },
  tokenizer(source) {
    const match = /^==(?=\S)([^\n]*?\S)==/.exec(source);
    if (match) return { type: "obsidianHighlight", raw: match[0], tokens: this.lexer.inlineTokens(match[1]) };
  },
  renderer(token) { return `<mark>${this.parser.parseInline(token.tokens!)}</mark>`; },
}, {
  name: "obsidianTag",
  level: "inline",
  start(source) { const match = /(^|\s)#[\p{L}\p{N}_/-]*[\p{L}_/-]/u.exec(source); return match ? match.index + match[1].length : undefined; },
  tokenizer(source) {
    const match = /^#([\p{L}\p{N}_/-]*[\p{L}_/-][\p{L}\p{N}_/-]*)/u.exec(source);
    if (match) return { type: "obsidianTag", raw: match[0], text: match[1] };
  },
  renderer(token) { return `<span class="tag">#${escapeHtml(token.text)}</span>`; },
}];

/** `[[target#heading|label]]` is a link to a sibling note; `![[file]]`
 *  embeds an image (`|300` sets its width) and links anything else. */
function wikilinks(resolveImage?: (src: string) => string | null): NonNullable<MarkedExtension["extensions"]> {
  return [{
    name: "obsidianWikilink",
    level: "inline",
    start(source) { const index = source.search(/!?\[\[/); return index < 0 ? undefined : index; },
    tokenizer(source) {
      const match = /^(!?)\[\[([^\]\n|#^]*)(?:#([^\]\n|]*))?(?:\|([^\]\n]*))?\]\]/.exec(source);
      if (!match || (!match[2].trim() && !match[3])) return;
      return { type: "obsidianWikilink", raw: match[0], embed: !!match[1], target: match[2].trim(), heading: match[3]?.trim() ?? "", label: match[4]?.trim() ?? "" };
    },
    renderer(token) {
      const { target, heading, label } = token as Tokens.Generic;
      const file = target && !/\.[\w]+$/.test(target) ? `${target}.md` : target;
      const path = file.split("/").map(encodeURIComponent).join("/");
      if (token.embed && /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(file)) {
        const destination = markdownTokenDestination(path, true);
        const src = destination.kind === "local" ? resolveImage?.(destination.href) : null;
        if (!src) return escapeHtml(file);
        const width = /^\d+$/.test(label) ? ` width="${label}"` : "";
        return `<img src="${escapeHtml(src)}" alt="${escapeHtml(file)}"${width}>`;
      }
      const href = `${path}${heading ? `#${encodeURIComponent(sectionKey(heading))}` : ""}`;
      const text = label || [target, heading].filter(Boolean).join(" › ");
      return `<a href="${escapeHtml(href)}" class="internal-link">${escapeHtml(text)}</a>`;
    },
  }];
}

export function obsidianExtensions(resolveImage?: (src: string) => string | null): MarkedExtension["extensions"] {
  return [...syntax, ...wikilinks(resolveImage)];
}

export type Frontmatter = { key: string; values: string[] }[];

/** Split a leading YAML block into Obsidian-style properties: scalars,
 *  `[a, b]` lists and `- item` lists. Anything nested stays as text. */
export function splitFrontmatter(source: string): { properties: Frontmatter; body: string } {
  const match = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(source);
  if (!match) return { properties: [], body: source };
  const properties: Frontmatter = [];
  const unquote = (value: string) => value.trim().replace(/^(["'])(.*)\1$/, "$2");
  for (const line of match[1].split(/\r?\n/)) {
    const item = /^\s+-\s*(.*)$/.exec(line);
    if (item && properties.length) { properties[properties.length - 1].values.push(unquote(item[1])); continue; }
    const pair = /^([^\s:#][^:]*):\s*(.*)$/.exec(line);
    if (!pair) continue;
    const raw = pair[2].trim();
    const values = /^\[.*\]$/.test(raw) ? raw.slice(1, -1).split(",").map(unquote).filter(Boolean) : raw ? [unquote(raw)] : [];
    properties.push({ key: pair[1].trim(), values });
  }
  return { properties, body: source.slice(match[0].length) };
}
