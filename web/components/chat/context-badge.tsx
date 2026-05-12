"use client";

/**
 * Per-conversation token / context-window indicator.
 *
 * Driven off ``messageIds.length`` rather than a timer so idle sessions
 * don't poll. Falls back to streaming-side store data only if the fetch
 * never returns (network glitch). Hidden when no usage yet.
 */
import { useQuery } from "@tanstack/react-query";
import { useMessageIds } from "@/lib/session-store";
import { type BranchTokenStats, fmtTokens } from "./tokens";

export function ContextBadge({ sessionId }: { sessionId: string | null }) {
  const messageIds = useMessageIds(sessionId);
  const { data } = useQuery<BranchTokenStats | null>({
    queryKey: ["session-tokens", sessionId, messageIds.length],
    enabled: !!sessionId,
    queryFn: async () => {
      if (!sessionId) return null;
      const r = await fetch(
        `/api/sessions/${encodeURIComponent(sessionId)}/tokens`,
      );
      if (!r.ok) return null;
      return r.json();
    },
    staleTime: 2_000,
  });

  if (!data || (!data.current_tokens && !data.naive_sum)) return null;

  const current = data.current_tokens || data.naive_sum;
  const window = data.context_window;
  const pct = window ? Math.round((current / window) * 100) : null;
  // Opencode/Claude-Code threshold scheme: dim below 65, yellow 65–85,
  // red above 85. Red signals imminent compaction risk.
  const color =
    pct === null
      ? "var(--text-muted)"
      : pct > 85
        ? "var(--accent-red)"
        : pct > 65
          ? "var(--accent-yellow)"
          : "var(--text-muted)";
  const cachePct = Math.round(data.cache_hit_rate * 100);
  const sourceMix = Object.entries(data.source_mix || {})
    .map(([k, v]) => `${k}: ${v}`)
    .join(", ");
  const tooltip = [
    window
      ? `Context: ${current.toLocaleString()} / ${window.toLocaleString()} (${pct}%)`
      : `Context: ${current.toLocaleString()} tokens`,
    data.cache_read_total
      ? `Cache: ${data.cache_read_total.toLocaleString()} read (${cachePct}% hit rate)`
      : null,
    data.model ? `Model: ${data.model}` : null,
    sourceMix ? `Sources: ${sourceMix}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px]"
      style={{ background: "var(--bg-tertiary)", color }}
      title={tooltip}
    >
      <span>
        {fmtTokens(current)}
        {window ? `/${fmtTokens(window)}` : ""}
        {pct !== null ? ` (${pct}%)` : ""}
      </span>
      {data.cache_read_total > 0 && (
        <span style={{ color: "var(--accent-green)" }}>
          · cache {cachePct}%
        </span>
      )}
    </span>
  );
}
