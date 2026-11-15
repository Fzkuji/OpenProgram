/**
 * Web sources a provider reports: the result of a provider-executed web
 * search (one `title — url` line per source, or `completed`) and the
 * citations a text block carries. Pure, so stream handlers, the history
 * loader and the transcript rows share one reading.
 */
import type { AssistantBlock, Citation } from "../../session-store/types.ts";

export interface WebSource {
  url: string;
  /** What the row shows: the title, or the url's domain when there is none. */
  label: string;
  title?: string;
}

/** The url's host without `www.`, or the url itself when it does not parse. */
export function sourceDomain(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "") || url;
  } catch {
    return url;
  }
}

/** True for a tool call the model's provider ran itself. */
export function isProviderSearch(block: Pick<AssistantBlock, "tool" | "provider_executed">): boolean {
  return block.provider_executed === true && block.tool === "web_search";
}

/** The query of a web_search call: `query` from the JSON input, else the raw input. */
export function searchQuery(input: string | undefined): string {
  const raw = (input ?? "").trim();
  if (!raw) return "";
  try {
    const value = JSON.parse(raw);
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const query = (value as Record<string, unknown>).query ?? (value as Record<string, unknown>).q;
      if (typeof query === "string" && query.trim()) return query.trim();
      return "";
    }
    if (typeof value === "string") return value.trim();
  } catch {
    /* not JSON: the raw input is the query */
  }
  return raw;
}

const SEPARATOR = /\s+[—–-]\s+(?=https?:\/\/)/;

/** Sources from a provider search result: one `title — url` (or bare url)
 *  per line; `completed` and other non-url lines carry none. */
export function parseSearchSources(result: string | undefined | null): WebSource[] {
  if (!result) return [];
  const out: WebSource[] = [];
  const seen = new Set<string>();
  for (const line of String(result).split("\n")) {
    const text = line.trim();
    if (!text) continue;
    let title: string | undefined;
    let url: string;
    const split = SEPARATOR.exec(text);
    if (split) {
      title = text.slice(0, split.index).trim() || undefined;
      url = text.slice(split.index + split[0].length).trim();
    } else if (/^https?:\/\//.test(text)) {
      url = text;
    } else {
      continue;
    }
    if (seen.has(url)) continue;
    seen.add(url);
    out.push({ url, title, label: title || sourceDomain(url) });
  }
  return out;
}

/** Citations as the wire sends them, kept only when they name a url. */
export function normalizeCitations(raw: unknown): Citation[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: Citation[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const c = item as Record<string, unknown>;
    if (typeof c.url !== "string" || !c.url.trim()) continue;
    out.push({
      url: c.url.trim(),
      title: typeof c.title === "string" && c.title.trim() ? c.title.trim() : null,
      start: typeof c.start === "number" ? c.start : null,
      end: typeof c.end === "number" ? c.end : null,
    });
  }
  return out.length ? out : undefined;
}

/** A text block's citations, one per url in first-cited order. */
export function citationSources(citations: Citation[] | undefined): WebSource[] {
  const out: WebSource[] = [];
  const at = new Map<string, number>();
  for (const c of citations ?? []) {
    if (!c?.url) continue;
    const title = c.title?.trim() || undefined;
    const known = at.get(c.url);
    if (known !== undefined) {
      if (!out[known].title && title) out[known] = { url: c.url, title, label: title };
      continue;
    }
    at.set(c.url, out.length);
    out.push({ url: c.url, title, label: title || sourceDomain(c.url) });
  }
  return out;
}
