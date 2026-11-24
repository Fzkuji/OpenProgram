/**
 * The Memory page's four reads, kept across visits (keyed by address, so
 * each project's memory is kept apart) and started ahead of the page.
 */
import { prefetchKept } from "@/lib/abilities/kept-read";
import type { RecentEvent, TimelineDay, TopicPage } from "./types";

export interface CoreState {
  injected: string;
  meta: {
    size: number;
    mtime: number;
    renderedSize: number;
    renderedMtime: number;
    renderedTokens: number;
    injectedTokens: number;
    injectionEnabled: boolean;
    budgetTokens: number;
  };
}

/** What `/api/memory/core` answers; every field may be missing. */
interface CoreReply {
  injected_content?: unknown;
  size?: number;
  mtime?: number;
  rendered_size?: number;
  rendered_mtime?: number;
  rendered_tokens?: number;
  injected_tokens?: number;
  injection_enabled?: boolean;
  budget_tokens?: number;
}

async function readJson(url: string): Promise<{ data: unknown; response: Response }> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return { data: await response.json(), response };
}

export async function readTopics(url: string): Promise<TopicPage[]> {
  const { data } = await readJson(url);
  return Array.isArray(data) ? data as TopicPage[] : [];
}

export async function readTimeline(url: string): Promise<TimelineDay[]> {
  const { data } = await readJson(url);
  return Array.isArray(data) ? data as TimelineDay[] : [];
}

export async function readRecent(url: string): Promise<{ events: RecentEvent[]; limit: number | null }> {
  const { data, response } = await readJson(url);
  const limit = Number(response.headers.get("X-Memory-Recent-Limit"));
  return {
    events: Array.isArray(data) ? data as RecentEvent[] : [],
    limit: Number.isInteger(limit) && limit > 0 ? limit : null,
  };
}

export async function readCore(url: string): Promise<CoreState> {
  const { data } = await readJson(url);
  const d = (data ?? {}) as CoreReply;
  return {
    injected: typeof d.injected_content === "string" ? d.injected_content : "",
    meta: {
      size: d.size ?? 0,
      mtime: d.mtime ?? 0,
      renderedSize: d.rendered_size ?? 0,
      renderedMtime: d.rendered_mtime ?? 0,
      renderedTokens: d.rendered_tokens ?? 0,
      injectedTokens: d.injected_tokens ?? 0,
      injectionEnabled: d.injection_enabled ?? true,
      budgetTokens: d.budget_tokens ?? 2000,
    },
  };
}

/** One timeline day's record. */
export async function readTimelineDay(url: string): Promise<string> {
  const { data } = await readJson(url);
  const content = (data as { content?: unknown } | null)?.content;
  return typeof content === "string" ? content : "";
}

/** The personal memory's four reads, side by side (hover, idle). */
export async function prefetchMemory(): Promise<void> {
  await Promise.all([
    prefetchKept("/api/memory/topics", () => readTopics("/api/memory/topics")),
    prefetchKept("/api/memory/timeline", () => readTimeline("/api/memory/timeline")),
    prefetchKept("/api/memory/recent", () => readRecent("/api/memory/recent")),
    prefetchKept("/api/memory/core", () => readCore("/api/memory/core")),
  ]);
}
