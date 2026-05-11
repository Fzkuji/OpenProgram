export type Capability = "vision" | "video" | "tools" | "reasoning" | "ctx";

export interface Model {
  id: string;
  name: string;
  provider: string;
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
  masked?: string;
  value?: string;
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
  name: string;
  category: string;
  description: string;
  params: string[];
  params_detail: FunctionParamDetail[];
  filepath?: string;
  mtime?: number;
}

export interface ProgramsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
}

// ---------------------------------------------------------------------------
// Auth v2 — mirrors openprogram/auth/types.py
// ---------------------------------------------------------------------------

export interface AuthProfile {
  name: string;
  display_name: string;
  description: string;
  created_at_ms: number;
  root: string;
}

export type CredentialKind =
  | "api_key"
  | "oauth"
  | "cli_delegated"
  | "device_code"
  | "external_process"
  | "sso";

export type CredentialStatus =
  | "valid"
  | "expiring_soon"
  | "stale"
  | "refreshing"
  | "needs_reauth"
  | "revoked"
  | "rate_limited"
  | "billing_blocked";

export interface CredentialPayloadView {
  type: CredentialKind;
  api_key_preview?: string;
  access_token_preview?: string;
  has_refresh_token?: boolean;
  expires_at_ms?: number;
  client_id?: string;
  scope?: string[];
  store_path?: string;
  access_key_path?: string[];
  command?: string[];
  cache_seconds?: number;
  broker?: string;
}

export interface CredentialView {
  credential_id: string;
  kind: CredentialKind;
  provider_id: string;
  profile_id: string;
  status: CredentialStatus;
  source: string;
  metadata: Record<string, unknown>;
  created_at_ms: number;
  updated_at_ms: number;
  cooldown_until_ms: number;
  last_used_at_ms: number;
  use_count: number;
  last_error: string | null;
  read_only: boolean;
  payload: CredentialPayloadView;
}

export interface PoolView {
  provider_id: string;
  profile_id: string;
  strategy: "fill_first" | "round_robin" | "random" | "least_used";
  fallback_chain: [string, string][];
  credentials: CredentialView[];
}

export interface RemovalStepView {
  description: string;
  executable: boolean;
  kind: string;
  target: string;
}

export interface DiscoveredCredential {
  source_id: string;
  credential?: CredentialView;
  removal_steps?: RemovalStepView[];
  error?: string;
}

export type AuthEventType =
  | "login_started"
  | "login_succeeded"
  | "login_failed"
  | "refresh_started"
  | "refresh_succeeded"
  | "refresh_failed"
  | "needs_reauth"
  | "revoked"
  | "imported_from_external"
  | "pool_member_added"
  | "pool_member_removed"
  | "pool_member_cooldown"
  | "pool_rotated"
  | "pool_exhausted"
  | "profile_created"
  | "profile_deleted"
  | "profile_activated";

export interface AuthEventPayload {
  type: AuthEventType;
  provider_id: string;
  profile_id: string;
  credential_id: string;
  detail: Record<string, unknown>;
  timestamp_ms: number;
}

export interface AddCredentialBody {
  type: "api_key" | "oauth" | "external_process";
  api_key?: string;
  access_token?: string;
  refresh_token?: string;
  expires_at_ms?: number;
  client_id?: string;
  command?: string[];
  metadata?: Record<string, unknown>;
}
