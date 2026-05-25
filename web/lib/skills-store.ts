import { create } from "zustand";

export interface Skill {
  name: string;
  description: string;
  category: string;
  optional: boolean;
  allowed_tools: string[];
  triggers: Record<string, unknown>;
  version: string;
  source: string;
  path: string;
  enabled: boolean;
  aliases?: string[];
  path_segments?: string[];
  leaf?: string;
}

export interface SkillCompletion {
  name: string;
  leaf: string;
  parent: string;
  description: string;
  source: string;
  match: "prefix" | "alias" | "leaf" | "suffix" | "substring" | "all";
}

export interface SkillDetail extends Skill {
  body: string;
  resources: string[];
}

export interface InvokeTraceEntry {
  ts: number;
  skill: string;
  source: string;
  md_hash: string;
}

export interface DiscoverySuggestion {
  url: string;
  label: string;
  slug?: string;
  description: string;
  added: boolean;
}

export interface CatalogEntry {
  name: string;
  description: string;
  path: string;
  files: string[];
  // Optional rich metadata — populated when the upstream source exposes
  // it (today only ClawHub does). The UI uses these to render a card
  // grid sorted by downloads/stars/etc.
  display_name?: string;
  version?: string;
  stars?: number;
  downloads?: number;
  installs?: number;
  updated_at?: number;  // unix ms
  tags?: string[];
  content_hash?: string;
}

interface SkillsState {
  skills: Skill[];
  selected: string | null;
  detail: SkillDetail | null;
  discoverySources: string[];
  discoverySuggested: DiscoverySuggestion[];
  loading: boolean;
  error: string | null;
  setSelected: (n: string | null) => void;
  fetchSkills: () => Promise<void>;
  fetchDetail: (name: string) => Promise<void>;
  toggleSkill: (name: string, enabled?: boolean) => Promise<void>;
  createSkill: (body: { name: string; description: string; category?: string; body: string }) => Promise<void>;
  deleteSkill: (name: string) => Promise<void>;
  fetchDiscoverySources: () => Promise<void>;
  fetchDiscoverySuggested: () => Promise<void>;
  addDiscoverySource: (url: string) => Promise<void>;
  removeDiscoverySource: (url: string) => Promise<void>;
  pullDiscovery: (url: string, namespace?: string) => Promise<string[]>;
  browseDiscovery: (url: string) => Promise<CatalogEntry[]>;
  installFromDiscovery: (url: string, name: string, namespace?: string) => Promise<string>;
  fetchInvokeTrace: (name: string, limit?: number) => Promise<InvokeTraceEntry[]>;
}

// Encode each path segment but preserve "/" between them so FastAPI's
// {name:path} converter still receives a slash-delimited path.
function encodePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

async function jsonReq<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 300)}`);
  }
  return r.json() as Promise<T>;
}

export const useSkills = create<SkillsState>((set, get) => ({
  skills: [],
  selected: null,
  detail: null,
  discoverySources: [],
  discoverySuggested: [],
  loading: false,
  error: null,

  setSelected: (n) => set({ selected: n }),

  fetchSkills: async () => {
    set({ loading: true, error: null });
    try {
      const skills = await jsonReq<Skill[]>("/api/skills");
      set({ skills, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  fetchDetail: async (name) => {
    try {
      const detail = await jsonReq<SkillDetail>(`/api/skills/${encodePath(name)}`);
      set({ detail });
    } catch (e) {
      set({ error: String(e), detail: null });
    }
  },

  toggleSkill: async (name, enabled) => {
    const body = enabled === undefined ? {} : { enabled };
    await jsonReq(`/api/skills/${encodePath(name)}/toggle`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await get().fetchSkills();
  },

  createSkill: async (body) => {
    await jsonReq("/api/skills", { method: "POST", body: JSON.stringify(body) });
    await get().fetchSkills();
  },

  deleteSkill: async (name) => {
    await jsonReq(`/api/skills/${encodePath(name)}`, { method: "DELETE" });
    if (get().selected === name) set({ selected: null, detail: null });
    await get().fetchSkills();
  },

  fetchDiscoverySources: async () => {
    const sources = await jsonReq<string[]>("/api/skills/discovery/sources");
    set({ discoverySources: sources });
  },

  fetchDiscoverySuggested: async () => {
    const suggested = await jsonReq<DiscoverySuggestion[]>("/api/skills/discovery/suggested");
    set({ discoverySuggested: suggested });
  },

  addDiscoverySource: async (url) => {
    const sources = await jsonReq<string[]>("/api/skills/discovery/sources", {
      method: "POST",
      body: JSON.stringify({ action: "add", url }),
    });
    set({ discoverySources: sources });
    await get().fetchDiscoverySuggested();
  },

  removeDiscoverySource: async (url) => {
    const sources = await jsonReq<string[]>("/api/skills/discovery/sources", {
      method: "POST",
      body: JSON.stringify({ action: "remove", url }),
    });
    set({ discoverySources: sources });
    await get().fetchDiscoverySuggested();
  },

  pullDiscovery: async (url, namespace) => {
    const res = await jsonReq<{ pulled: string[] }>("/api/skills/discovery/pull", {
      method: "POST",
      body: JSON.stringify({ url, ...(namespace ? { namespace } : {}) }),
    });
    await get().fetchSkills();
    return res.pulled;
  },

  browseDiscovery: async (url) => {
    const res = await jsonReq<{ url: string; entries: CatalogEntry[] }>(
      `/api/skills/discovery/browse?url=${encodeURIComponent(url)}`,
    );
    return res.entries;
  },

  installFromDiscovery: async (url, name, namespace) => {
    const res = await jsonReq<{ installed: string }>(
      "/api/skills/discovery/install",
      { method: "POST", body: JSON.stringify({ url, name, ...(namespace ? { namespace } : {}) }) },
    );
    await get().fetchSkills();
    return res.installed;
  },

  fetchInvokeTrace: async (name, limit = 50) => {
    return jsonReq<InvokeTraceEntry[]>(`/api/skills/${encodePath(name)}/invoke-trace`, {
      method: "POST",
      body: JSON.stringify({ limit }),
    });
  },
}));
