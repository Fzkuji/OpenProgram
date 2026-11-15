import { jsonFetch } from "./fetch-client";
import { branchTarget } from "@/lib/agents/branch-target";
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
import type { PickerProvider } from "@/lib/settings/provider-status.generated";

/** ``/api/model``'s answer: the branch written (``branch_id``), the one
 *  selected, that branch's stored settings and the model's own controls. */
export interface ModelPickReply {
  switched?: boolean;
  provider?: string;
  model?: string;
  branch_id?: string | null;
  active_branch_id?: string | null;
  settings?: Record<string, unknown>;
  chat?: Record<string, unknown>;
}

export interface GoalControls {
  can_resume: boolean;
  can_verify: boolean;
  can_end: boolean;
  can_pause: boolean;
  reasons: Partial<Record<"resume" | "verify" | "end" | "pause", string | null>>;
  operations: { effect_id: string; execution_id: string; status: string; tool_name?: string | null; created_at: number; dispatched_at?: number | null }[];
  waits: { wait_id: string; execution_id: string; kind: string; created_at: number }[];
  active_children: string[];
  processes: { id: string; execution_id: string; status: string; started_at: number }[];
}

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
  provider_label?: string;
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
    ...(m.provider_label ? { providerLabel: m.provider_label } : {}),
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

  /** The model picker's read: the enabled models plus, per provider, what
   *  the picker shows beside them (state, fix, quota left, balance). */
  listPickerModels: () =>
    jsonFetch<{ models: RawModel[]; providers?: Record<string, PickerProvider> }>("/api/models/enabled").then((d) => ({
      models: d.models.map((m) => mapModel(m, m.provider ?? "")),
      providers: d.providers ?? {},
    })),

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

  mutateGoal: (
    sessionId: string,
    body: { action: string; prompt?: string; [key: string]: unknown },
  ) =>
    jsonFetch<{ goal: Record<string, unknown>; execution?: Record<string, unknown>; stop_error?: string }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/goal`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  /** Tell the server a queued message waits for this conversation's next
   *  turn, so an automatic Goal continuation yields to it. */
  setGoalQueueWaiting: (sessionId: string, waiting: boolean) =>
    jsonFetch<{ ok: true }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/goal/queue`,
      { method: "POST", body: JSON.stringify({ waiting }) },
    ),

  getGoal: (sessionId: string, signal?: AbortSignal) => jsonFetch<{
    goal: Record<string, unknown>;
    controls?: GoalControls;
    execution?: { execution_id?: string | null; status?: string; finished?: boolean | null; can_start_new_turn?: boolean; provider_response_incomplete?: boolean; recovery_mode?: string | null; recovery_reason?: string | null };
  }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/goal`,
    { signal },
  ),

  listHistory: () =>
    jsonFetch<{ id: string; title: string; created_at?: number }[]>("/api/history"),

  // Pin a (provider, model) for this conversation. ``session_id`` is
  // required — without it the backend only updates the global default
  // and the active conversation keeps using its old runtime (the bug
  // where "switching to Opus" silently kept routing to Sonnet).
  /** Pin a conversation's model on the branch the pane shows (``branchId``),
   *  like every other branch setting; see ``branchTarget``. */
  switchModel: (provider: string, model: string, session_id?: string, branchId?: string | null) =>
    jsonFetch<ModelPickReply>("/api/model", {
      method: "POST",
      body: JSON.stringify({ provider, model, session_id, ...branchTarget(branchId) }),
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
