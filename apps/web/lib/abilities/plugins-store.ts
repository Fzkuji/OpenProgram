import { create } from "zustand";

import { jsonFetch } from "@/lib/net/fetch-client";
import { answered, readKept, retrySoon } from "./kept-read";

const PLUGINS_KEY = "/api/plugins";
const readPlugins = () => jsonFetch<{ plugins: PluginRow[]; errors: Record<string, string> }>(PLUGINS_KEY);

export interface PluginRow {
  name: string;
  version: string;
  description: string;
  source: string;
  deprecated?: boolean;
  compatibility?: string;
  trust: string;
  enabled: boolean;
  loaded: boolean;
  error?: string;
  sidebar?: Array<Record<string, unknown>>;
  options_schema?: Record<string, unknown>;
  entrypoints?: Record<string, unknown>;
  /** What the running plugin registered, by kind (tools, context, …). */
  contributions?: Record<string, string[]>;
  manifest_form?: string;
  root?: string;
}

export interface MarketplaceEntry {
  id: string;
  name: string;
  url: string;
}

interface PluginsState {
  plugins: PluginRow[];
  errors: Record<string, string>;
  marketplaces: MarketplaceEntry[];
  tab: "installed" | "marketplace" | "errors";
  /** The list has come from the server at least once. */
  ready: boolean;
  setTab: (t: PluginsState["tab"]) => void;
  refresh: () => Promise<void>;
  refreshMarketplaces: () => Promise<void>;
  install: (source: string, spec: string, ref?: string) => Promise<{ success: boolean; log: string }>;
  uninstall: (name: string) => Promise<{ success: boolean; log: string }>;
  toggle: (name: string, enabled: boolean) => Promise<PluginRow | { error: string; code?: string }>;
  reload: (name: string) => Promise<PluginRow | { error: string }>;
  validate: (name: string) => Promise<{ checks: Array<{ name: string; ok: boolean; detail: string }>; all_ok: boolean }>;
  getOptions: (name: string) => Promise<Record<string, unknown>>;
  setOptions: (name: string, options: Record<string, unknown>) => Promise<void>;
  setTrust: (name: string, level: string) => Promise<void>;
  addMarketplace: (url: string, name?: string) => Promise<MarketplaceEntry>;
  removeMarketplace: (id: string) => Promise<void>;
  fetchMarketplaceIndex: (id: string) => Promise<Array<Record<string, unknown>>>;
  fetchBuiltinPlugins: () => Promise<Array<Record<string, unknown>>>;
}


export const usePluginsStore = create<PluginsState>((set, get) => ({
  plugins: [],
  errors: {},
  marketplaces: [],
  tab: "installed",
  ready: false,

  setTab: (tab) => set({ tab }),

  // A failed read keeps the last list and asks again shortly.
  refresh: async () => {
    try {
      const d = await readKept(PLUGINS_KEY, readPlugins);
      answered(PLUGINS_KEY);
      set({ plugins: d.plugins || [], errors: d.errors || {}, ready: true });
    } catch (e) {
      retrySoon(PLUGINS_KEY, () => { void get().refresh(); }, e);
    }
  },

  refreshMarketplaces: async () => {
    const d = await jsonFetch<{ marketplaces: MarketplaceEntry[] }>("/api/plugins/marketplaces");
    set({ marketplaces: d.marketplaces || [] });
  },

  install: async (source, spec, ref) => {
    const r = await jsonFetch<{ success: boolean; log: string }>("/api/plugins/install", {
      method: "POST",
      body: JSON.stringify({ source, spec, ref }),
    });
    await get().refresh();
    return r;
  },

  uninstall: async (name) => {
    const r = await jsonFetch<{ success: boolean; log: string }>(
      `/api/plugins/${encodeURIComponent(name)}/uninstall`,
      { method: "POST" },
    );
    await get().refresh();
    return r;
  },

  toggle: async (name, enabled) => {
    try {
      const r = await jsonFetch<PluginRow>(
        `/api/plugins/${encodeURIComponent(name)}/toggle`,
        { method: "POST", body: JSON.stringify({ enabled }) },
      );
      await get().refresh();
      return r;
    } catch (e) {
      const err = e as Error & { code?: string };
      return { error: err.message, code: err.code };
    }
  },

  reload: async (name) => {
    try {
      const r = await jsonFetch<PluginRow>(
        `/api/plugins/${encodeURIComponent(name)}/reload`,
        { method: "POST" },
      );
      await get().refresh();
      return r;
    } catch (e) {
      return { error: (e as Error).message };
    }
  },

  validate: (name) =>
    jsonFetch(`/api/plugins/${encodeURIComponent(name)}/validate`, { method: "POST" }),

  getOptions: async (name) => {
    const d = await jsonFetch<{ options: Record<string, unknown> }>(
      `/api/plugins/${encodeURIComponent(name)}/options`,
    );
    return d.options || {};
  },

  setOptions: async (name, options) => {
    await jsonFetch(`/api/plugins/${encodeURIComponent(name)}/options`, {
      method: "POST",
      body: JSON.stringify({ options }),
    });
  },

  setTrust: async (name, level) => {
    await jsonFetch(`/api/plugins/${encodeURIComponent(name)}/trust`, {
      method: "POST",
      body: JSON.stringify({ level }),
    });
    await get().refresh();
  },

  addMarketplace: async (url, name) => {
    const r = await jsonFetch<MarketplaceEntry>("/api/plugins/marketplaces", {
      method: "POST",
      body: JSON.stringify({ url, name }),
    });
    await get().refreshMarketplaces();
    return r;
  },

  removeMarketplace: async (id) => {
    await jsonFetch(`/api/plugins/marketplaces/${encodeURIComponent(id)}`, { method: "DELETE" });
    await get().refreshMarketplaces();
  },

  fetchMarketplaceIndex: async (id) => {
    const d = await jsonFetch<{ items: Array<Record<string, unknown>> }>(
      `/api/plugins/marketplace/${encodeURIComponent(id)}/index`,
    );
    return d.items || [];
  },

  fetchBuiltinPlugins: async () => {
    const d = await jsonFetch<{ items: Array<Record<string, unknown>> }>(
      "/api/plugins/builtin",
    );
    return d.items || [];
  },
}));

/** The Plugins page's first read, started before the page opens. It is
 *  asked once: a failure only warns, and retrying belongs to a page that
 *  shows the list. */
export function prefetchPlugins(): Promise<void> {
  if (usePluginsStore.getState().ready) return Promise.resolve();
  return readKept(PLUGINS_KEY, readPlugins).then(
    (d) => { answered(PLUGINS_KEY); usePluginsStore.setState({ plugins: d.plugins || [], errors: d.errors || {}, ready: true }); },
    (cause) => { console.warn("[prefetch] plugins", cause); },
  );
}
