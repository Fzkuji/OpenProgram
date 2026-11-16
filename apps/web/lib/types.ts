export type Capability = "vision" | "video" | "tools" | "reasoning" | "ctx";

export interface Model {
  id: string;
  name: string;
  provider: string;
  /** The provider's display name, as Settings shows it (enabled list). */
  providerLabel?: string;
  enabled: boolean;
  capabilities: Capability[];
  context?: number;
  custom?: boolean;
}

export interface Provider {
  id: string;
  label: string;
  kind: "api" | "cli";
  enabled: boolean;
  configured: boolean;
  api_key_env: string | null;
  default_base_url: string;
  base_url: string;
  use_responses_api?: boolean;
  supports_fetch?: boolean;
  cli_binary?: string;
  model_count: number;
  enabled_model_count: number;
}

export interface ProviderConfig {
  base_url: string | null;
  use_responses_api: boolean;
}

export interface TestResult {
  ok: boolean;
  latency_ms?: number;
  error?: string;
}

export interface KeyPreview {
  has_value: boolean;
  masked: string;
}

export interface FunctionParamDetail {
  name: string;
  type: string;
  default: string | null;
  required: boolean;
  description?: string;
  placeholder?: string;
  multiline?: boolean;
  hidden?: boolean;
  choices?: string[];
}

export interface AgenticFunction {
  continuation?: { supported: boolean; reason?: string; boundary?: string; state?: string };
  name: string;
  category: string;
  description: string;
  params: string[];
  params_detail: FunctionParamDetail[];
  filepath?: string;
  mtime?: number;
}

export interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons?: Record<string, string>;
}

