/**
 * Markdown → HTML for chat bubbles.
 *
 * Delegates to the shared `renderMd` (runtime-bridge/helpers) so React
 * bubbles render byte-for-byte identically to every other renderer —
 * no second markdown engine. Falls back to escaped plain text on SSR,
 * where `renderMd` would touch `window`.
 */
import { copyText } from "@/lib/clipboard";
import { highlightEscapedCode } from "@/lib/chat/markdown/code-highlight";
import { Marked } from "marked";
import { renderMd, sanitizeHtml } from "@/lib/runtime-bridge/dom/markdown-render";
import { proseMarkdown } from "@/lib/markdown/prose";
import {
  A2UI_LANG,
  A2UI_PARTIAL_LANG,
  a2uiPending,
  markOpenA2uiFence,
  renderA2ui,
  unescapeHtml,
} from "@/lib/chat/markdown/a2ui";
import { markOpenVisualFence, withVisuals } from "@/lib/chat/markdown/visual";
import { localPathHref } from "@/lib/files/prose-file-paths";

export function renderMarkdown(src: string, { cache = true }: { cache?: boolean } = {}): string {
  if (typeof window === "undefined") return escapeHtml(src);
  try {
    return withTableScroll(withCodeChrome(withVisuals(withA2ui(renderMd(markOpenVisualFence(markOpenA2uiFence(src)), { cache }))), cache));
  } catch {
    return escapeHtml(src);
  }
}

/** Text inside an A2UI surface is model-written markdown with no raw
 *  HTML: tags show as text, and the result is sanitized like any bubble. */
const a2uiMarked = new Marked(...proseMarkdown, {
  breaks: true,
  renderer: {
    html(token) {
      return escapeHtml(token.text);
    },
  },
});

function a2uiText(markdown: string, inline: boolean): string {
  const html = inline
    ? a2uiMarked.parseInline(markdown, { async: false }) as string
    : a2uiMarked.parse(markdown, { async: false }) as string;
  return sanitizeHtml(html);
}

const A2UI_BLOCK = new RegExp(
  `<pre><code class="language-(${A2UI_LANG}|${A2UI_PARTIAL_LANG})">([\\s\\S]*?)<\\/code><\\/pre>`,
  "g",
);

/** Swap ```a2ui fences for the surface they describe (lib/chat/markdown/a2ui.ts).
 *  A finished block that does not parse stays a code block; one still
 *  streaming shows a skeleton until its JSON is complete. */
function withA2ui(html: string): string {
  if (!html.includes(`language-${A2UI_LANG}`)) return html;
  return html.replace(A2UI_BLOCK, (block, lang: string, body: string) => {
    const surface = renderA2ui(unescapeHtml(body), a2uiText);
    if (surface) return surface;
    if (lang === A2UI_PARTIAL_LANG) return a2uiPending();
    return block;
  });
}

/** User-authored text can arrive from external channels, so raw HTML in
 *  it is shown as text (not parsed) and the result is sanitized like any
 *  other rendered markdown. Formulas stay literal; this path is for prose,
 *  lists and code. */
const userMarked = new Marked(...proseMarkdown, {
  breaks: true,
  renderer: {
    html(token) {
      return escapeHtml(token.text);
    },
  },
});
const userCache = new Map<string, string>();

export function renderUserMarkdown(src: string): string {
  if (typeof window === "undefined") return escapeHtml(src);
  const hit = userCache.get(src);
  if (hit !== undefined) return hit;
  let out: string;
  try {
    out = '<span class="md-rendered">'
      + withTableScroll(withCodeChrome(sanitizeHtml(userMarked.parse(src, { async: false }) as string)))
      + "</span>";
  } catch {
    out = escapeHtml(src);
  }
  if (userCache.size >= 500) userCache.delete(userCache.keys().next().value!);
  userCache.set(src, out);
  return out;
}

/** Wrap each table in its scroll container, so the table can span the
 *  column (marked emits a bare <table>, and a table that scrolls itself
 *  has to be display:block, which shrinks it to its content). */
function withTableScroll(html: string): string {
  if (!html.includes("<table")) return html;
  return html.replace(/<table>/g, '<div class="md-table"><table>').replace(/<\/table>/g, "</table></div>");
}

const COPY_ICON =
  '<svg class="md-code-icon-copy" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
  + '<svg class="md-code-icon-done" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

const chromeCache = new Map<string, string>();
const CHROME_CACHE_MAX = 200;

/** Wrap fenced code blocks in a header with the language and a copy
 *  button, and
 *  syntax-highlight bodies in known languages (highlight.js only emits
 *  escaped text plus `hljs-*` spans). Runs on already-sanitized HTML; code bodies are escaped by
 *  marked, so `</code></pre>` only ever closes a real block. */
function withCodeChrome(html: string, cache = true): string {
  if (!html.includes("<pre><code")) return html;
  // Cache the highlighted result per rendered HTML string; a streaming
  // reply's open tail changes every delta and is not cached.
  const hit = cache ? chromeCache.get(html) : undefined;
  if (hit !== undefined) return hit;
  const out = html
    .replace(
      /<pre><code class="language-([^"]*)">([\s\S]*?)<\/code><\/pre>/g,
      (_m, lang: string, body: string) =>
        `<pre><code class="language-${lang}">${highlightEscapedCode(body, lang)}</code></pre>`,
    )
    .replace(/<pre><code(?: class="language-([^"]*)")?>/g, (_m, lang?: string) => {
      const label = escapeHtml((lang || "").trim());
      return '<div class="md-code"><div class="md-code-head">'
        + `<span class="md-code-lang">${label || "text"}</span>`
        + `<button type="button" class="md-code-copy" aria-label="Copy code" title="Copy code">${COPY_ICON}</button>`
        + "</div><pre><code" + (lang ? ` class="language-${label}"` : "") + ">";
    })
    .replace(/<\/code><\/pre>/g, "</code></pre></div>");
  if (!cache) return out;
  if (chromeCache.size >= CHROME_CACHE_MAX) chromeCache.delete(chromeCache.keys().next().value!);
  chromeCache.set(html, out);
  return out;
}

if (typeof document !== "undefined") {
  // A link in rendered prose to a local file is not a page of the app:
  // stop it before the shell's router takes the path as a route. The
  // reply's own handler opens the file (prose-file-links.tsx).
  document.addEventListener("click", (event) => {
    const target = event.target as Element | null;
    const anchor = typeof target?.closest === "function" ? target.closest<HTMLAnchorElement>(".md-rendered a[href]") : null;
    if (anchor && localPathHref(anchor.getAttribute("href") ?? "")) event.preventDefault();
  }, true);
  document.addEventListener("click", (event) => {
    const target = event.target as Element | null;
    const tab = typeof target?.closest === "function" ? target.closest<HTMLElement>(".a2ui-tab") : null;
    if (tab) {
      selectA2uiTab(tab);
      return;
    }
    // A footnote link and its way back move within their own reply: every
    // reply numbers its notes from 1, so a page-wide #footnote-1 could
    // land in another one, and the hash would change the App's URL.
    const note = typeof target?.closest === "function"
      ? target.closest<HTMLAnchorElement>("a[data-footnote-ref], a[data-footnote-backref]")
      : null;
    if (note) {
      event.preventDefault();
      const id = note.getAttribute("href")?.slice(1) ?? "";
      const dest = Array.from(note.closest(".md-rendered")?.querySelectorAll<HTMLElement>("[id]") ?? []).find(el => el.id === id);
      dest?.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const btn = typeof target?.closest === "function" ? target.closest(".md-code-copy") : null;
    if (!btn) return;
    const code = btn.closest(".md-code")?.querySelector("pre")?.textContent ?? "";
    void copyText(code).then((ok) => {
      if (!ok) return;
      btn.classList.add("copied");
      window.setTimeout(() => btn.classList.remove("copied"), 1500);
    });
  });
}

/** A2UI Tabs render as static markup; the bar's buttons only toggle which
 *  panel of their own Tabs component is shown. */
function selectA2uiTab(tab: HTMLElement): void {
  const tabs = tab.closest(".a2ui-tabs");
  if (!tabs) return;
  const index = Number(tab.dataset.a2uiTab);
  const bar = Array.from(tabs.querySelectorAll<HTMLElement>(":scope > .a2ui-tab-bar > .a2ui-tab"));
  const panels = Array.from(tabs.querySelectorAll<HTMLElement>(":scope > .a2ui-tab-panel"));
  bar.forEach((b, i) => b.setAttribute("aria-selected", String(i === index)));
  panels.forEach((p, i) => { p.hidden = i !== index; });
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
