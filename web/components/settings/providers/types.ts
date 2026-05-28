/**
 * Shared types for the LLM-Providers settings pane.
 * Pulled out of providers-section.tsx so each sub-component file can
 * import what it needs without dragging the whole pane.
 */

export interface Provider {
  id: string;
  label: string;
  enabled: boolean;
  configured?: boolean;
  kind?: "api" | "cli";
  api_key_env?: string;
  default_base_url?: string;
  base_url?: string;
  supports_fetch?: boolean;
  cli_binary?: string;
  /**
   * Provider-specific setup instructions surfaced in the detail
   * panel. Backticked spans render as inline <code>; lines starting
   * with `$ ` render as a command row. Used by claude-code (meridian /
   * claude-max-api-proxy) and any other "local daemon" provider whose
   * setup isn't an API key.
   */
  setup_hint?: string;
}

export interface Model {
  id: string;
  name?: string;
  enabled: boolean;

  // Capabilities -----------------------------------------------------
  vision?: boolean;
  video?: boolean;
  audio?: boolean;
  tools?: boolean;
  reasoning?: boolean;
  /** JSON-schema strict output mode */
  structured_output?: boolean;
  /** File / PDF attachments (separate from raw image input) */
  attachment?: boolean;
  /** ``temperature`` parameter has any effect on this model */
  temperature_param?: boolean;

  // Modality lists (verbatim from models.dev) ------------------------
  input_modalities?: string[];
  output_modalities?: string[];

  // Limits -----------------------------------------------------------
  context_window?: number;
  /** Some models cap the *single-call* input lower than total context
   *  (e.g. GPT-5.5: 1.05M context, 922K input cap). */
  input_limit?: number;
  /** Output cap per completion. */
  max_tokens?: number;

  // Pricing (USD per 1M tokens) --------------------------------------
  input_cost?: number;
  output_cost?: number;
  cache_read_cost?: number;
  cache_write_cost?: number;
  /** Tiered or service-tier pricing pass-throughs. UI renders these
   *  as a sub-list when present; structure varies per provider so we
   *  keep them loosely typed. */
  cost_tiers?: unknown[];
  cost_context_over_200k?: Record<string, unknown>;

  // Identity / metadata ----------------------------------------------
  family?: string;
  /** Training data cutoff date as the upstream catalogue reports it
   *  (usually ``YYYY-MM`` or ``YYYY-MM-DD``). */
  knowledge_cutoff?: string;
  release_date?: string;
  last_updated?: string;
  open_weights?: boolean;
}

/** Compact 'context window' formatter — 128_000 → "128K", 1_000_000 → "1M". */
export function formatCtx(n?: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return Math.round(n / 1_000_000) + "M";
  if (n >= 1000) return Math.round(n / 1000) + "K";
  return String(n);
}
