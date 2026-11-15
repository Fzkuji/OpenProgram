/**
 * How full a conversation's context is: the one reading the composer's
 * ring, its tip and the /context card all show, so they never disagree.
 *
 * A session's total and window are its last context_stats; before the
 * first one arrives, the card's cached snapshot (the session's GET
 * /context). A chat with no session shows the draft estimate. A window
 * that is not known is 0: the count alone, no limit and no percentage.
 */
import type { ContextBreakdown } from "./context-breakdown-cache";

/** A session's context_stats as the store keeps them. */
export interface ContextStats {
  total_used?: number;
  context?: number;
  input?: number;
  cache_read?: number;
  basis?: string | null;
}

export interface ContextOccupancy {
  used: number;
  /** 0 when the model's window is not known. */
  window: number;
  /** "estimated" when the count is an estimate, not a measured request. */
  basis?: string | null;
}

/** A snapshot made from context_stats alone, for a session with none yet. */
export function statsSnapshot(
  stats: ContextStats | undefined,
  window: number | undefined,
): ContextBreakdown | null {
  if (stats == null && !window) return null;
  // An older record (a replayed context_stats) has no total_used: the last
  // request's prompt size stands in for it.
  const used = stats?.total_used
    ?? (stats ? stats.context || (stats.input || 0) + (stats.cache_read || 0) : 0);
  return {
    total_used: used,
    window: window || null,
    context_window: window || null,
    basis: stats?.basis ?? undefined,
  };
}

/** The snapshot a session is shown from. Its total and window are the
 *  last context_stats (the server pushes one whenever occupancy moves);
 *  the cached card snapshot adds the categories, and stands alone until
 *  the first context_stats arrives. */
export function occupancySnapshot(
  cached: ContextBreakdown | null,
  stats: ContextStats | undefined,
  window: number | undefined,
): ContextBreakdown | null {
  if (stats == null) return cached ?? statsSnapshot(stats, window);
  const fromStats = statsSnapshot(stats, window)!;
  if (!cached) return fromStats;
  const used = fromStats.total_used ?? 0;
  return {
    ...cached,
    ...fromStats,
    free_space: fromStats.window ? Math.max(0, fromStats.window - used) : null,
  };
}

export function occupancyOf(data: ContextBreakdown | null | undefined): ContextOccupancy {
  const window = data?.window || data?.context_window || 0;
  // A snapshot without a total gives the free space instead; with neither,
  // nothing is known to be used (never "the whole window").
  const used = data?.total_used
    ?? (window > 0 && typeof data?.free_space === "number" ? window - data.free_space : 0);
  return { used, window: window > 0 ? window : 0, basis: data?.basis ?? null };
}

export function formatTokens(n: number): string {
  return n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
}

/** The ring's tip: "Context used / window (pct)", or the count alone when
 *  the window is unknown; " · est." for an estimate; then provider · model. */
export function contextTip(occupancy: ContextOccupancy, metaLine = ""): string {
  const { used, window } = occupancy;
  const head = window > 0
    ? `Context ${formatTokens(used)} / ${formatTokens(window)} (${(Math.max(0, Math.min(1, used / window)) * 100).toFixed(0)}%)`
    : `Context ${formatTokens(used)}`;
  return head + (occupancy.basis === "estimated" ? " · est." : "") + (metaLine ? ` · ${metaLine}` : "");
}
