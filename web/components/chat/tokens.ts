/**
 * Shared types + formatter for the per-branch / per-message token API.
 *
 * Both `<ContextBadge>` (full-branch summary) and `<MessageBubble>`'s
 * inline `<MessageTokenBadge>` (per-row counts) consume the same shape,
 * so the interface lives here to avoid duplicate type declarations.
 */
export interface BranchTokenStats {
  current_tokens: number;
  context_window: number;
  pct_used: number;
  cache_read_total: number;
  cache_hit_rate: number;
  model: string | null;
  source_mix: Record<string, number>;
  naive_sum: number;
  last_assistant_usage: number;
  branch: Array<{
    message_id: string;
    role: string;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    total: number;
    token_source: string;
    token_model: string | null;
    timestamp: number;
  }>;
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}
