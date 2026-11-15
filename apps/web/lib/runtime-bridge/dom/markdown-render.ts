import renderMathInElement from "katex/contrib/auto-render";
import { marked as npmMarked, type MarkedExtension } from "marked";
import markedFootnote from "marked-footnote";
import { cjkEmphasis } from "../../markdown/cjk-emphasis.ts";

// The shared `marked` singleton (chat, memory, file previews) closes
// `**bold**` in CJK prose the way readers expect; see cjk-emphasis.ts.
// It also renders GFM footnotes (`[^1]`) instead of a dead link.
npmMarked.use(cjkEmphasis);
// Its types name the workspace root's older `marked`; the extension itself
// works with any marked from 7 on.
npmMarked.use(markedFootnote() as unknown as MarkedExtension);

export function escHtml(s: unknown): string {
  if (typeof s !== "string") s = String(s ?? "");
  const div = document.createElement("div");
  div.textContent = s as string;
  return div.innerHTML;
}

/** Tags dropped whole (contents included) when they appear in rendered
 *  markdown. `<script>` inserted via innerHTML never executes, but the
 *  rest of these do their damage without a parser run. */
const DROP_TAGS = new Set([
  "SCRIPT", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "BASE", "FORM",
  "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "MATH",
]);

/** SVG and MathML have their own executable/linking surfaces, including
 *  SMIL attributes that can mutate a safe-looking link after sanitization.
 *  Chat markdown does not need either namespace, so drop those subtrees. */
const DROP_NAMESPACES = new Set([
  "http://www.w3.org/2000/svg",
  "http://www.w3.org/1998/Math/MathML",
]);

/** Attribute values that may carry a URL. Anything resolving to a
 *  script-ish scheme is stripped. */
const URL_ATTRS = new Set(["href", "src", "xlink:href", "action", "formaction"]);

const SAFE_URL = /^(?:https?:|mailto:|tel:|data:image\/(?:png|jpe?g|gif|webp|svg\+xml);|#|\/|\.{0,2}\/)/i;

/** Strip anything executable out of parsed markdown.
 *
 *  marked does NOT sanitize: raw HTML in the source passes straight to
 *  the DOM. Tool, file, MCP, and channel content can all reach this path.
 *  Parse into a detached template and inspect actual elements and attributes
 *  before any rendered markup reaches the chat DOM. */
export function sanitizeHtml(html: string): string {
  if (typeof document === "undefined") return html;
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const walk = (root: ParentNode): void => {
    for (const el of Array.from(root.querySelectorAll("*"))) {
      if (DROP_TAGS.has(el.tagName) || DROP_NAMESPACES.has(el.namespaceURI ?? "")) {
        el.remove();
        continue;
      }
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith("on")) {
          el.removeAttribute(attr.name);
          continue;
        }
        if (URL_ATTRS.has(name) && !SAFE_URL.test(attr.value.trim())) {
          el.removeAttribute(attr.name);
        }
      }
    }
  };
  walk(tpl.content);
  const sanitized = document.createElement("div");
  sanitized.append(tpl.content);
  return sanitized.innerHTML;
}

// Bound retained source and sanitized HTML, including large tool/file content.
const MD_CACHE_MAX = 256;
const MD_CACHE_BYTES = 8 * 1024 * 1024;
let mdCacheBytes = 0;
const mdCache = new Map<string, string>();

/** `cache: false` is for one-off sources, such as the open tail of a
 *  streaming reply, which would only evict entries that get reused. */
export function renderMd(s: unknown, { cache = true }: { cache?: boolean } = {}): string {
  if (typeof s !== "string") s = String(s ?? "");
  const src = s as string;
  const hit = cache ? mdCache.get(src) : undefined;
  if (hit !== undefined) {
    mdCache.delete(src);
    mdCache.set(src, hit);
    return hit;
  }
  let str = src;
  const mathBlocks: string[] = [];
  const stash = (m: string): string => {
    mathBlocks.push(m);
    return "%%MATH" + (mathBlocks.length - 1) + "%%";
  };
  str = str.replace(/\$\$([\s\S]*?)\$\$/g, stash);
  str = str.replace(/\\\[([\s\S]*?)\\\]/g, stash);
  str = str.replace(/\\\(([\s\S]*?)\\\)/g, stash);
  str = str.replace(/\$([^$\n]+?)\$/g, stash);
  // Restore the formula delimiters as escaped text after sanitization. KaTeX
  // reads the resulting text nodes; embedded HTML cannot become DOM content.
  let html = sanitizeHtml(npmMarked.parse(str, { breaks: true }) as string);
  for (let i = 0; i < mathBlocks.length; i++) {
    html = html.replace("%%MATH" + i + "%%", () => escHtml(mathBlocks[i]));
  }
  const out = '<span class="md-rendered">' + html + "</span>";
  const cost = 2 * (src.length + out.length);
  if (cache && cost <= MD_CACHE_BYTES) {
    while (mdCache.size && (mdCache.size >= MD_CACHE_MAX || mdCacheBytes + cost > MD_CACHE_BYTES)) {
      const oldest = mdCache.keys().next().value!;
      mdCacheBytes -= 2 * (oldest.length + mdCache.get(oldest)!.length);
      mdCache.delete(oldest);
    }
    mdCache.set(src, out);
    mdCacheBytes += cost;
  }
  return out;
}

export function typesetMath(root: ParentNode): void {
  if (typeof document === "undefined") return;
  // The selector skips typeset blocks itself: this runs on every chat
  // resize, and a pane drag resizes the chat every frame.
  root.querySelectorAll<HTMLElement>(".md-rendered:not([data-math-rendered])").forEach((el) => {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
    el.dataset.mathRendered = "1";
  });
}

export function renderMathInChat(): void {
  typesetMath(document);
}
