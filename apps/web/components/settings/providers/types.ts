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
  /** User-added (tier-3) provider — a config-only OpenAI-compatible endpoint.
   *  Drives the "custom" badge + delete action + manual model-add input. */
  custom?: boolean;
  local?: boolean;
  /**
   * Provider-specific setup instructions surfaced in the detail
   * panel. Backticked spans render as inline <code>; lines starting
   * with `$ ` render as a command row. Used for provider-specific setup instructions.
   */
  setup_hint?: string;
  /**
   * Native login methods (OAuth / device-code / import-from-CLI) the web can
   * drive for this provider, excluding plain api_key (the ApiKey field handles
   * that). Present only for providers with a real native flow; absent/empty for
   * key-only providers. Drives the <ProviderLogin/> choice list; the first is
   * the recommended way. Source of truth:
   * openprogram/models/auth/login/login_method_registry.py.
   */
  login_methods?: LoginMethod[];
}

/** One way to sign in: its title and one-line description in English and
 *  Chinese, and for an import, the credential file it reads. */
export interface LoginMethod {
  id: string;
  label: string;
  label_zh: string;
  description: string;
  description_zh: string;
  path?: string;
}

/** A credential's state (openprogram/models/auth/selection.py ``state_of``);
 *  ``not_set_up`` for a key of a provider that has no address here, ``no_file``
 *  for an imported sign-in whose file is not there (manage.credential_row). */
export type CredentialStatus = "ready" | "cooling" | "quota" | "invalid_key" | "needs_login" | "not_set_up" | "no_file";

/** How a provider's credentials take turns. */
export type CredentialStrategy = "in_order" | "evenly" | "least_used";

/** One credential of a provider as GET /api/providers/{p}/credentials lists
 *  it (openprogram/models/auth/manage.py): never its secret, only the last
 *  four characters of a key. */
export interface CredentialRow {
  id: string;
  label: string;
  /** The server's name (English): the label, else the email, else "Key ····tail".
   *  The page names a row with `credentialName`, in the reader's language. */
  display_label: string;
  /** What a sign-in of the provider is called ("ChatGPT"). */
  signin_name?: string;
  kind: string;
  /** web | cli | import | env */
  source: string;
  identity: { email?: string; plan?: string; org?: string; name?: string };
  key_tail: string;
  /** Set for a key read from the environment, which is shown, never stored. */
  env_var: string | null;
  read_only: boolean;
  enabled: boolean;
  status: CredentialStatus;
  /** When a cooling or spent credential is used again (epoch seconds). */
  cooldown_until: number | null;
  blocked_models: Record<string, number>;
  last_error: { at?: number; code?: string; message?: string } | null;
  last_used_at: number | null;
  use_count: number;
  created_at: number | null;
  /** Calls and cost since local midnight. */
  today?: { calls: number; cost_usd: number };
  /** The quota limits this credential last reported. */
  limits?: import("@/lib/settings/provider-status.generated").QuotaLimitWire[];
}

/** A provider's ordered credential list and how to add to it. */
export interface CredentialList {
  provider: string;
  strategy: CredentialStrategy;
  strategies: CredentialStrategy[];
  /** The credential a new conversation would use now. */
  active: string | null;
  auth: {
    methods: LoginMethod[];
    env_vars: string[];
    check: string | null;
    keys_url: string | null;
    billing_url: string | null;
    signin_name?: string;
  };
  /** False when the provider has no address here (a custom provider removed from the config). */
  set_up?: boolean;
  credentials: CredentialRow[];
}

/** A conversation's credential per provider: the one it is bound to (kept
 *  while it works) and the one it is pinned to by hand. */
export interface SessionCredentials {
  binding: Record<string, string>;
  pin: Record<string, string>;
}

export interface Model {
  id: string;
  name?: string;
  enabled: boolean;

  // Capabilities -----------------------------------------------------
  vision?: boolean;
  video?: boolean;
  audio?: boolean;
  tools?: boolean | null;
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
  /** Wire format / endpoint family the model expects
   *  (``"anthropic-messages-2023-06-01"``, ``"openai-chat-completions"``, …).
   *  Surfaced in the detail panel so it's clear at a glance which
   *  SDK / wire schema a model speaks. */
  api?: string;
}

/** Compact 'context window' formatter — 128_000 → "128K", 1_000_000 → "1M". */
export function formatCtx(n?: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return Math.round(n / 1_000_000) + "M";
  if (n >= 1000) return Math.round(n / 1000) + "K";
  return String(n);
}

/** Refetch agentSettings so the top-bar agent chip drops a model that was
 *  just disabled. Dynamic import: runtime-bridge/providers touches `window`
 *  at module scope, so a static import breaks Next prerendering. */
export function refreshAgentChip(): void {
  void import("@/lib/runtime-bridge/providers").then((m) => m.loadAgentSettings({ written: true }));
}
