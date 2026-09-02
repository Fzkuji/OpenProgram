import { jsonFetch } from "./fetch-client";
import type {
  Provider,
  Model,
  Capability,
  ProviderConfig,
  TestResult,
  KeyPreview,
  AgenticFunction,
  FunctionsMeta,
  AddCredentialBody,
  AuthAccount,
  CredentialView,
  DiscoveredCredential,
  PoolView,
} from "@/lib/types";

interface RawModel {
  id: string;
  name: string;
  vision?: boolean;
  video?: boolean;
  tools?: boolean;
  reasoning?: boolean;
  context_window?: number;
  enabled?: boolean;
  custom?: boolean;
  provider?: string;
}

function mapModel(m: RawModel, provider: string): Model {
  const caps: Capability[] = [];
  if (m.vision) caps.push("vision");
  if (m.video) caps.push("video");
  if (m.tools) caps.push("tools");
  if (m.reasoning) caps.push("reasoning");
  if (m.context_window) caps.push("ctx");
  return {
    id: m.id,
    name: m.name || m.id,
    provider: m.provider || provider,
    enabled: m.enabled ?? false,
    capabilities: caps,
    context: m.context_window,
    custom: m.custom,
  };
}

export const api = {
  listProviders: () =>
    jsonFetch<{ providers: Provider[] }>("/api/providers/list").then((d) => d.providers),

  listModels: (provider: string) =>
    jsonFetch<{ models: RawModel[] }>(`/api/providers/${provider}/models`).then((d) =>
      d.models.map((m) => mapModel(m, provider))
    ),

  listEnabledModels: () =>
    jsonFetch<{ models: RawModel[] }>("/api/models/enabled").then((d) =>
      d.models.map((m) => mapModel(m, m.provider ?? ""))
    ),

  toggleProvider: (provider: string, enabled: boolean) =>
    jsonFetch<{ ok: true }>(`/api/providers/${provider}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  toggleModel: (provider: string, model: string, enabled: boolean) =>
    jsonFetch<{ ok: true }>(`/api/providers/${provider}/models/${encodeURIComponent(model)}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  getProviderConfig: (provider: string) =>
    jsonFetch<ProviderConfig>(`/api/providers/${provider}/config`),

  setProviderConfig: (provider: string, patch: Partial<ProviderConfig>) =>
    jsonFetch<ProviderConfig>(`/api/providers/${provider}/config`, {
      method: "POST",
      body: JSON.stringify(patch),
    }),

  fetchRemoteModels: (provider: string) =>
    jsonFetch<{ fetched: number; added: number; total_custom: number }>(
      `/api/providers/${provider}/fetch-models`,
      { method: "POST" }
    ),

  testProvider: (provider: string, model?: string) =>
    jsonFetch<TestResult>(`/api/providers/${provider}/test`, {
      method: "POST",
      body: JSON.stringify({ model }),
    }),

  deleteModel: (provider: string, model: string) =>
    jsonFetch<{ ok: true }>(`/api/providers/${provider}/models/${encodeURIComponent(model)}`, {
      method: "DELETE",
    }),

  getKey: (envVar: string) =>
    jsonFetch<KeyPreview>(`/api/config/key/${envVar}`),

  listFunctions: () => jsonFetch<AgenticFunction[]>("/api/programs"),

  getProgramsMeta: () => jsonFetch<FunctionsMeta>("/api/programs/meta"),

  setProgramsMeta: (meta: FunctionsMeta) =>
    jsonFetch<{ ok: true }>("/api/programs/meta", {
      method: "POST",
      body: JSON.stringify(meta),
    }),

  getFunctionSource: (name: string) =>
    jsonFetch<{ name: string; source: string; filepath: string }>(
      `/api/function/${encodeURIComponent(name)}/source`
    ),

  runFunction: (name: string, params: Record<string, unknown>) => {
    const { _session_id, session_id, ...kwargs } = params;
    const body: Record<string, unknown> = { kwargs };
    const sid = session_id ?? _session_id;
    if (typeof sid === "string" && sid.trim()) body.session_id = sid;
    return jsonFetch<{ result?: unknown; error?: string; session_id?: string; msg_id?: string }>(
      `/api/function/${encodeURIComponent(name)}`,
      { method: "POST", body: JSON.stringify(body) }
    );
  },

  listHistory: () =>
    jsonFetch<{ id: string; title: string; created_at?: number }[]>("/api/history"),

  // Pin a (provider, model) for this conversation. ``session_id`` is
  // required — without it the backend only updates the global default
  // and the active conversation keeps using its old runtime (the bug
  // where "switching to Opus" silently kept routing to Sonnet).
  switchModel: (provider: string, model: string, session_id?: string) =>
    jsonFetch<{ ok: true }>("/api/model", {
      method: "POST",
      body: JSON.stringify({ provider, model, session_id }),
    }),

  getAgentSettings: () => jsonFetch<Record<string, unknown>>("/api/agent_settings"),

  setAgentSettings: (patch: Record<string, unknown>) =>
    jsonFetch<{ ok: true }>("/api/agent_settings", {
      method: "POST",
      body: JSON.stringify(patch),
    }),

  // ----- Auth v2 -----------------------------------------------------------

  listProviderAccounts: () =>
    jsonFetch<{ accounts: AuthAccount[]; default: string }>("/api/providers/accounts"),

  createProviderAccount: (name: string, display_name = "", description = "") =>
    jsonFetch<AuthAccount>("/api/providers/accounts", {
      method: "POST",
      body: JSON.stringify({ name, display_name, description }),
    }),

  deleteProviderAccount: (name: string) =>
    jsonFetch<{ deleted: string }>(`/api/providers/accounts/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  listProviderPools: (account?: string) => {
    const qs = account ? `?account=${encodeURIComponent(account)}` : "";
    return jsonFetch<{ pools: PoolView[] }>(`/api/providers/pools${qs}`);
  },

  getProviderPool: (provider: string, account: string) =>
    jsonFetch<PoolView>(
      `/api/providers/pools/${encodeURIComponent(provider)}/${encodeURIComponent(account)}`,
    ),

  addProviderCredential: (provider: string, account: string, body: AddCredentialBody) =>
    jsonFetch<CredentialView>(
      `/api/providers/pools/${encodeURIComponent(provider)}/${encodeURIComponent(account)}/credentials`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  removeProviderCredential: (provider: string, account: string, credentialId: string) =>
    jsonFetch<{ removed: string }>(
      `/api/providers/pools/${encodeURIComponent(provider)}/${encodeURIComponent(account)}/credentials/${encodeURIComponent(credentialId)}`,
      { method: "DELETE" },
    ),

  discoverProviderCredentials: () =>
    jsonFetch<{ discovered: DiscoveredCredential[] }>("/api/providers/discover", {
      method: "POST",
    }),

  runProvidersDoctor: () =>
    jsonFetch<DoctorReport>("/api/providers/doctor", { method: "POST" }),

  adoptAllProviderCredentials: (account?: string) => {
    const qs = account ? `?account=${encodeURIComponent(account)}` : "";
    return jsonFetch<AdoptAllReport>(`/api/providers/adopt_all${qs}`, {
      method: "POST",
    });
  },

  listProviderAliases: () =>
    jsonFetch<Record<string, string>>("/api/providers/aliases"),

  retryChat: (sessionId: string, msgId: string) =>
    jsonFetch<{ session_id: string; msg_id: string; truncated_from: string }>(
      "/api/chat/retry",
      { method: "POST", body: JSON.stringify({ session_id: sessionId, msg_id: msgId }) },
    ),

  branchChat: (sessionId: string, msgId: string) =>
    jsonFetch<{ session_id: string; title: string; branched_from: string }>(
      "/api/chat/branch",
      { method: "POST", body: JSON.stringify({ session_id: sessionId, msg_id: msgId }) },
    ),

  /** Read the canvas file. Returns ``content: ""`` + ``exists: false``
   *  when the file hasn't been created yet — so the panel renders an
   *  empty state instead of throwing. */
  getCanvas: (path?: string) =>
    jsonFetch<{
      path: string;
      content: string;
      mtime: number;
      blocks: { id: string; length: number }[];
      exists: boolean;
    }>(`/api/canvas${path ? "?path=" + encodeURIComponent(path) : ""}`),
};

export interface DoctorFinding {
  level: "ERROR" | "WARN" | "INFO";
  code: string;
  message: string;
  provider?: string;
  profile?: string;
  credential_id?: string;
}

export interface DoctorReport {
  pools_checked: number;
  accounts_checked: number;
  findings: DoctorFinding[];
}

export interface AdoptEvent {
  level: "adopted" | "error";
  source_id?: string;
  provider_id?: string;
  preview?: string;
  error?: string;
}

export interface AdoptAllReport {
  adopted: number;
  skipped: number;
  errored: number;
  events: AdoptEvent[];
  profile: string;
}
