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
   * with `$ ` render as a command row. Used by claude-max-proxy /
   * any other "local daemon" provider whose setup isn't an API key.
   */
  setup_hint?: string;
}

export interface Model {
  id: string;
  name?: string;
  enabled: boolean;
  vision?: boolean;
  video?: boolean;
  tools?: boolean;
  reasoning?: boolean;
  context_window?: number;
}

/** Compact 'context window' formatter — 128_000 → "128K", 1_000_000 → "1M". */
export function formatCtx(n?: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return Math.round(n / 1_000_000) + "M";
  if (n >= 1000) return Math.round(n / 1000) + "K";
  return String(n);
}
