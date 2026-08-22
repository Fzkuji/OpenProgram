/**
 * The per-session store instances and the registry that owns them.
 *
 * Split out of `session-scope.tsx` so the global store can push into a scope
 * (WS frames and legacy bridges have no React context) without importing the
 * React provider — and so the import cycle stays one-directional at module
 * load: the global store imports this file, this file only reaches back into
 * the global store from inside function bodies, never at module init.
 */
import { createStore, type StoreApi } from "zustand";

import type { ComposerSettings, RunningTask } from "./types";

export interface SessionScopeState {
  /** The session (or provisional `local_*` chat key) this store speaks for. */
  sid: string;
  /** Unsent composer text. */
  draft: string;
  setDraft: (value: string) => void;
  /** Tool toggles + thinking effort for this session. */
  settings: ComposerSettings;
  patchSettings: (patch: Partial<ComposerSettings>) => void;
  /** Non-null while a turn is running — drives this composer's send/stop. */
  running: RunningTask | null;
  setRunning: (task: RunningTask | null) => void;
  /** `/context` popover open for this session. */
  contextPanelOpen: boolean;
  setContextPanelOpen: (open: boolean) => void;
}

/** Mirrors the global store's defaults. A session with no persisted entry
 *  must still send tools — an empty tools array reads to the model as "I
 *  can't access your files". */
export const DEFAULT_SCOPE_SETTINGS: ComposerSettings = {
  thinking: "",
  tools: true,
  webSearch: false,
  fast: false,
  unattended: false,
  permission_mode: "",
  effective_permission: "",
  sandbox: true,
};

/**
 * Where a scope write goes to be persisted. The global store installs these
 * at its own module init (`installScopeWriteThrough`) rather than being
 * imported here — this file must not reach into `./index` at load time, since
 * that module imports this one.
 */
export interface ScopeWriteThrough {
  draft: (sid: string, value: string) => void;
  settings: (sid: string, patch: Partial<ComposerSettings>) => void;
  running: (sid: string, task: RunningTask | null) => void;
}

let writeThrough: ScopeWriteThrough | null = null;

export function installScopeWriteThrough(hooks: ScopeWriteThrough): void {
  writeThrough = hooks;
}

/**
 * Seed a fresh instance from the global keyed maps, then write back to them.
 * Reading the seed matters on remount (tab revisit, reload after localStorage
 * hydration): the instance is new, the draft is not.
 */
function createSessionStore(
  sid: string,
  seed: { draft: string; settings: ComposerSettings; running: RunningTask | null },
): StoreApi<SessionScopeState> {
  return createStore<SessionScopeState>((set) => ({
    sid,
    draft: seed.draft,
    setDraft: (value) => {
      set({ draft: value });
      // The global map owns persistence (localStorage + transfer journal).
      writeThrough?.draft(sid, value);
    },
    settings: seed.settings,
    patchSettings: (patch) => {
      set((s) => ({ settings: { ...s.settings, ...patch } }));
      writeThrough?.settings(sid, patch);
    },
    running: seed.running,
    setRunning: (task) => {
      set({ running: task });
      writeThrough?.running(sid, task);
    },
    contextPanelOpen: false,
    setContextPanelOpen: (open) => set({ contextPanelOpen: open }),
  }));
}

const stores = new Map<string, StoreApi<SessionScopeState>>();

/**
 * The store for `sid`, created on first ask and cached forever after —
 * unmounting a pane must not throw away the draft being typed in it.
 * `seed` supplies the initial values (the caller reads them off the global
 * store; this module stays free of that import at load time).
 */
export function getSessionStore(
  sid: string,
  seed: { draft: string; settings: ComposerSettings; running: RunningTask | null },
): StoreApi<SessionScopeState> {
  let store = stores.get(sid);
  if (!store) {
    store = createSessionStore(sid, seed);
    stores.set(sid, store);
  }
  return store;
}

/** Forget a session's store — call when the session itself is deleted. */
export function dropSessionStore(sid: string): void {
  stores.delete(sid);
}

/**
 * Push values that arrived from outside React (a WS frame, a legacy bridge,
 * a window-to-window tab transfer) into a session's store. No-ops when
 * nothing has rendered that session yet: its instance would seed from the
 * global maps on first render anyway.
 */
export function pushToSessionStore(
  sid: string,
  patch: Partial<Omit<SessionScopeState, "sid">>,
): void {
  stores.get(sid)?.setState(patch as Partial<SessionScopeState>);
}
