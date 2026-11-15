// Find in conversation. The server searches the whole branch snapshot the
// history pages come from, so matches outside the loaded window are found
// too; the chat then loads the window around a match, scrolls to it and
// paints the occurrences with the CSS Custom Highlight API (the transcript
// DOM is never rewritten).

export interface FindMatch {
  /** Display group id: what `history_around` accepts. */
  id: string;
  message_id: string;
  role: string;
  count: number;
  snippet: string;
}

export const FIND_ALL = "op-find";
export const FIND_CURRENT = "op-find-current";

export function normalizeMatches(raw: unknown): FindMatch[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((row) => {
    if (!row || typeof row !== "object") return [];
    const m = row as Record<string, unknown>;
    if (typeof m.id !== "string" || typeof m.message_id !== "string") return [];
    const count = typeof m.count === "number" && m.count > 0 ? Math.floor(m.count) : 1;
    return [{
      id: m.id,
      message_id: m.message_id,
      role: typeof m.role === "string" ? m.role : "",
      count,
      snippet: typeof m.snippet === "string" ? m.snippet : "",
    }];
  });
}

export function totalOccurrences(matches: readonly FindMatch[]): number {
  return matches.reduce((sum, m) => sum + m.count, 0);
}

/** Which message, and which occurrence inside it, the n-th match is. */
export function locate(matches: readonly FindMatch[], index: number): { match: FindMatch; occurrence: number } | null {
  if (index < 0) return null;
  let rest = index;
  for (const match of matches) {
    if (rest < match.count) return { match, occurrence: rest };
    rest -= match.count;
  }
  return null;
}

/** Step forward or back through `total` matches, wrapping at the ends. */
export function step(index: number, total: number, forward: boolean): number {
  if (total <= 0) return -1;
  if (index < 0) return forward ? 0 : total - 1;
  return (index + (forward ? 1 : total - 1)) % total;
}

function visible(el: Element, cache: Map<Element, boolean>): boolean {
  let seen = cache.get(el);
  if (seen === undefined) {
    const check = (el as Element & { checkVisibility?: () => boolean }).checkVisibility;
    seen = typeof check === "function" ? check.call(el) : !el.closest("[hidden]");
    cache.set(el, seen);
  }
  return seen;
}

/** Ranges of `query` in the visible text under `root`, case-insensitive.
 *  A match split across two elements (half bold, say) is not found. */
export function textRanges(root: Node, query: string): Range[] {
  const needle = query.toLowerCase();
  if (!needle) return [];
  const doc = root.ownerDocument ?? (root as Document);
  const walker = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  const cache = new Map<Element, boolean>();
  const ranges: Range[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue ?? "";
    if (text.length < needle.length) continue;
    const folded = text.toLowerCase();
    // Lower-casing changed the length (rare scripts): offsets would drift.
    if (folded.length !== text.length) continue;
    let at = folded.indexOf(needle);
    if (at < 0) continue;
    const parent = node.parentElement;
    if (!parent || !visible(parent, cache)) continue;
    while (at >= 0) {
      const range = doc.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + needle.length);
      ranges.push(range);
      at = folded.indexOf(needle, at + needle.length);
    }
  }
  return ranges;
}

type HighlightRegistry = { set(name: string, value: unknown): void; delete(name: string): void };
type HighlightCtor = new (...ranges: Range[]) => unknown;

function registry(): { highlights: HighlightRegistry; Highlight: HighlightCtor } | null {
  const css = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS;
  const Highlight = (globalThis as { Highlight?: HighlightCtor }).Highlight;
  return css?.highlights && Highlight ? { highlights: css.highlights, Highlight } : null;
}

export function paintHighlights(all: readonly Range[], current: Range | null): void {
  const api = registry();
  if (!api) return;
  api.highlights.set(FIND_ALL, new api.Highlight(...all));
  if (current) api.highlights.set(FIND_CURRENT, new api.Highlight(current));
  else api.highlights.delete(FIND_CURRENT);
}

export function clearHighlights(): void {
  const api = registry();
  api?.highlights.delete(FIND_ALL);
  api?.highlights.delete(FIND_CURRENT);
}

/** The rendered row for a message, outermost first in document order. */
export function messageRow(container: ParentNode, messageId: string): HTMLElement | null {
  const esc = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(messageId) : messageId.replace(/["\\]/g, "\\$&");
  return container.querySelector<HTMLElement>(`[data-msg-id="${esc}"]`);
}

const openers = new Map<string, Set<() => void>>();

/** A row that folds its text away registers how to open it, so a match
 *  inside can be shown. Returns the function that takes it back. */
export function onFindReveal(messageId: string, open: () => void): () => void {
  const set = openers.get(messageId) ?? new Set();
  openers.set(messageId, set);
  set.add(open);
  return () => {
    set.delete(open);
    if (!set.size && openers.get(messageId) === set) openers.delete(messageId);
  };
}

/** Opens the folded rows of a message; false when none is folded. */
export function revealForFind(messageId: string): boolean {
  const set = openers.get(messageId);
  if (!set?.size) return false;
  for (const open of [...set]) open();
  return true;
}

/** Cmd/Ctrl+F belongs to the chat only while the chat is showing and focus
 *  is not in something with its own find (web page, terminal, editor). */
export function chatOwnsFind(target: EventTarget | null, chatArea: Element | null): boolean {
  if (!chatArea) return false;
  const check = (chatArea as Element & { checkVisibility?: () => boolean }).checkVisibility;
  if (typeof check === "function" && !check.call(chatArea)) return false;
  const el = target && typeof (target as Element).closest === "function" ? target as Element : null;
  if (!el) return true;
  const owner = el.closest(".xterm, .cm-editor, .monaco-editor, .peer-session-pane");
  // The composer is a CodeMirror editor without a find of its own.
  return !owner || (owner.matches(".cm-editor") && !!owner.closest("#chatView"));
}
