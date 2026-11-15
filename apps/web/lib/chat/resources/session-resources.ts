import type { TerminalResource } from "../../desktop/terminal-resources";
import type { ManagedProcess } from "../../net/execution/process-client";
import { create } from "zustand";
import type { CenterTab } from "../../tabs/center-tabs-store";

export type BrowserControlState =
  | "active"
  | "yielding"
  | "paused"
  | "waiting"
  | "unknown"
  | "stop_unconfirmed"
  | "idle"
  | "closed";

export type BrowserLastOperation = {
  id: string;
  action: string;
  phase: "dispatched" | "acknowledged" | "failed" | "unknown";
  frame_id?: string;
  geometry_revision?: number;
  point?: { x: number; y: number; width?: number; height?: number };
  timestamp?: number;
  error?: string;
};

export type SessionResource = {
  id: string;
  sessionId: string | null;
  kind: string;
  title: string;
  target: string;
  status: string;
  source: "web" | "usage" | "browser" | "terminal" | "application" | "process";
  sourceId: string;
  terminalGeneration?: string;
  applicationId?: string;
  applicationInstanceId?: string;
  applicationDigest?: string;
  scopeSessionId?: string;
  resourceId?: string;
  conversationSessionId?: string;
  executionId?: string | null;
  branchId?: string | null;
  branchName?: string | null;
  agentName?: string | null;
  tabId?: string | null;
  windowId?: string | null;
  controlState?: BrowserControlState;
  generation?: number;
  sequence?: number;
  lastOperation?: BrowserLastOperation;
  pendingWait?: { id: string; kind?: string; tool?: string };
};

export type BackendResource = {
  application_id?: string;
  application_instance_id?: string;
  application_digest?: string;
  id: string;
  resource_id?: string;
  session_id: string;
  conversation_session_id?: string;
  execution_id?: string | null;
  branch_id?: string | null;
  branch_name?: string | null;
  agent_name?: string | null;
  tab_id?: string | null;
  window_id?: string | null;
  kind: string;
  title: string;
  target: string;
  status: string;
  source: "usage" | "browser" | string;
  control_state?: BrowserControlState;
  generation?: number;
  sequence?: number;
  last_operation?: BrowserLastOperation;
  pending_wait?: { id?: string; kind?: string; tool?: string };
};

export type PreviewPreference = {
  targetId: string | null;
  mode: "follow" | "manual";
  hidden: boolean;
  expanded: boolean;
};

export type ResourceGroup = {
  key: string;
  rows: SessionResource[];
};

export type ResourceControlRequest = {
  conversationSessionId: string;
  resourceId: string;
  action: "pause" | "resume";
  commandId: string;
  generation: number;
};

const PREF_STORAGE = "openprogram.resource-preview";
/** Preview targets naming a native app window or a Desktop Terminal rather
 *  than a web Page. Terminal targets match the terminal rows' ids. */
export const NATIVE_WINDOW_TARGET = "native:";
export const TERMINAL_TARGET = "terminal:";
const isAppTarget = (target: string | null | undefined) =>
  !!target && (target.startsWith(NATIVE_WINDOW_TARGET) || target.startsWith(TERMINAL_TARGET));

const defaultPref = (): PreviewPreference => ({
  targetId: null, mode: "follow", hidden: false, expanded: false,
});

export type IngestOrigin = "event" | "snapshot";

type BrowserResourceState = {
  rows: Record<string, SessionResource>;
  ingestClock: number;
  rowClock: Record<string, number>;
  viewedBranch: Record<string, string | null>;
  latestFollow: Record<string, string>;
  seenOperations: Record<string, string>;
  preferences: Record<string, PreviewPreference>;
  connected: boolean;
  followEpoch: number;
  snapshotComplete: Record<string, true>;
  snapshotFailed: Record<string, true>;
  optimisticClosed: Record<string, { generation: number; expiresAt: number; rows: SessionResource[] }>;
};

function readStoredPreferences(): Record<string, PreviewPreference> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(PREF_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, PreviewPreference>;
    return Object.fromEntries(Object.entries(parsed)
      .filter(([, value]) => value && typeof value === "object")
      .map(([key, value]) => [key, value.mode === "manual" ? { ...value, mode: "follow" as const } : value]));
  } catch {
    return {};
  }
}

export const useBrowserResourceStore = create<BrowserResourceState>(() => ({
  rows: {},
  ingestClock: 0,
  rowClock: {},
  viewedBranch: {},
  latestFollow: {},
  seenOperations: {},
  preferences: readStoredPreferences(),
  connected: true,
  followEpoch: 0,
  snapshotComplete: {},
  snapshotFailed: {},
  optimisticClosed: {},
}));

function prefKey(sessionId: string, branchId: string | null): string {
  return `${sessionId}\0${branchId || "unassigned"}`;
}

function persistPreferences(preferences: Record<string, PreviewPreference>): void {
  if (typeof localStorage === "undefined") return;
  try {
    // A pin lasts for this page load. Stored, it outlived the reason for it
    // and the preview never followed the Agent again.
    // An app window's frames live in the worker's memory only.
    const stored = Object.fromEntries(Object.entries(preferences).map(([key, pref]) => [key, {
      ...pref,
      mode: "follow" as const,
      targetId: isAppTarget(pref.targetId) ? null : pref.targetId,
    }]));
    localStorage.setItem(PREF_STORAGE, JSON.stringify(stored));
  } catch {
    /* quota / private mode */
  }
}

function writePref(sessionId: string, branchId: string | null, patch: Partial<PreviewPreference>): PreviewPreference {
  const key = prefKey(sessionId, branchId);
  const current = useBrowserResourceStore.getState();
  const next = { ...defaultPref(), ...current.preferences[key], ...patch };
  const preferences = { ...current.preferences, [key]: next };
  useBrowserResourceStore.setState({ preferences });
  persistPreferences(preferences);
  return next;
}

export function unwrapResourcePayload(data: unknown): BackendResource | null {
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  const item = record.item;
  if (item && typeof item === "object" && typeof (item as BackendResource).id === "string") {
    return item as BackendResource;
  }
  if (typeof record.id === "string") return record as unknown as BackendResource;
  return null;
}

export function resourceSessionId(tab: CenterTab | undefined): string | null {
  if (tab?.kind === "session") return tab.draft ? null : tab.sessionId || null;
  if (tab?.kind === "web") return tab.agentSessionId || null;
  if (tab?.kind === "file") return tab.diffSessionId || null;
  return null;
}

function asControlState(value: unknown): BrowserControlState | undefined {
  if (
    value === "active" || value === "yielding" || value === "paused" || value === "waiting"
    || value === "unknown" || value === "stop_unconfirmed" || value === "idle" || value === "closed"
  ) return value;
  return undefined;
}

function asPendingWait(value: unknown): SessionResource["pendingWait"] {
  if (!value || typeof value !== "object") return undefined;
  const item = value as { id?: unknown; kind?: unknown; tool?: unknown };
  if (typeof item.id !== "string" || !item.id) return undefined;
  return {
    id: item.id,
    kind: typeof item.kind === "string" ? item.kind : undefined,
    tool: typeof item.tool === "string" ? item.tool : undefined,
  };
}

function asLastOperation(value: unknown): BrowserLastOperation | undefined {
  if (!value || typeof value !== "object") return undefined;
  const item = value as Record<string, unknown>;
  if (typeof item.id !== "string" || typeof item.action !== "string") return undefined;
  const phase = item.phase === "dispatched" || item.phase === "acknowledged"
    || item.phase === "failed" || item.phase === "unknown" ? item.phase : "unknown";
  const point = item.point && typeof item.point === "object" ? item.point as BrowserLastOperation["point"] : undefined;
  return {
    id: item.id,
    action: item.action,
    phase,
    frame_id: typeof item.frame_id === "string" ? item.frame_id : undefined,
    geometry_revision: typeof item.geometry_revision === "number" ? item.geometry_revision : undefined,
    point: point && typeof point.x === "number" && typeof point.y === "number" ? point : undefined,
    timestamp: typeof item.timestamp === "number" ? item.timestamp : undefined,
    error: typeof item.error === "string" ? item.error : undefined,
  };
}

function admittedOperation(operation: BrowserLastOperation | undefined): boolean {
  return !!operation && (operation.phase === "dispatched" || operation.phase === "acknowledged");
}

export function normalizeBackendResource(item: BackendResource, scopeSessionId: string): SessionResource | null {
  if (item.source === "browser") {
    return {
      id: item.id,
      sourceId: item.resource_id || item.id,
      source: "browser",
      sessionId: item.session_id,
      scopeSessionId,
      resourceId: item.resource_id || item.id,
      conversationSessionId: item.conversation_session_id || scopeSessionId,
      executionId: item.execution_id,
      branchId: item.branch_id ?? null,
      branchName: item.branch_name ?? null,
      agentName: item.agent_name ?? null,
      tabId: item.tab_id ?? null,
      windowId: item.window_id ?? null,
      kind: item.kind || "web",
      title: item.title,
      target: item.target,
      status: item.status,
      controlState: asControlState(item.control_state),
      generation: item.generation,
      sequence: item.sequence,
      lastOperation: asLastOperation(item.last_operation),
      pendingWait: asPendingWait(item.pending_wait),
    };
  }
  if (item.source === "application") return {
    id: item.id, sourceId: item.id, source: "application", kind: "application",
    sessionId: item.session_id, scopeSessionId, conversationSessionId: scopeSessionId,
    title: item.title, target: item.target, status: item.status,
    applicationId: item.application_id, applicationInstanceId: item.application_instance_id,
    applicationDigest: item.application_digest,
  };
  if (item.source !== "usage") return null;
  return {
    id: `${item.source}:${item.id}`,
    sourceId: item.id,
    source: "usage",
    sessionId: item.session_id,
    scopeSessionId,
    conversationSessionId: item.conversation_session_id || scopeSessionId,
    executionId: item.execution_id,
    kind: item.kind,
    title: item.title,
    target: item.target,
    status: item.status,
  };
}

export function backendResourceRows(items: readonly BackendResource[], scopeSessionId: string): SessionResource[] {
  return items.flatMap(item => {
    const row = normalizeBackendResource(item, scopeSessionId);
    return row ? [row] : [];
  });
}

export function sessionResourceRows(
  tabs: readonly CenterTab[],
  backend: readonly SessionResource[],
  sessionId: string | null,
) {
  if (!sessionId) return [];
  const covered = new Set(backend.flatMap(row => row.tabId ? [row.tabId] : []));
  const views: SessionResource[] = tabs.flatMap(tab => {
    if (tab.kind !== "web" || covered.has(tab.id)) return [];
    return [{
      id: `tab:${tab.id}`, sessionId: tab.agentSessionId || null,
      kind: "web", title: tab.title || tab.url || tab.id,
      target: tab.url || "", status: "open", source: "web", sourceId: tab.id,
      tabId: tab.id, conversationSessionId: tab.agentSessionId || undefined,
    }];
  });
  const unique = new Map([...views, ...backend].map(row => [row.id, row]));
  return [...unique.values()].filter(row => {
    if (row.source === "browser") {
      return row.conversationSessionId === sessionId || row.scopeSessionId === sessionId;
    }
    return row.sessionId === sessionId;
  });
}

/** Project exact native instances into the shared session list, never tab guesses. */
export function terminalResourceRows(resources: readonly TerminalResource[], sessionId: string | null): SessionResource[] {
  if (!sessionId) return [];
  return resources.filter(row => row.session_ids.includes(sessionId)).map(row => ({
    id: `terminal:${row.terminal_id}`, sourceId: row.terminal_id, source: "terminal",
    terminalGeneration: row.generation,
    sessionId, conversationSessionId: sessionId, kind: "terminal",
    title: row.preset === "claude" ? "Claude Code" : "Terminal",
    target: row.start_cwd, status: row.status,
    controlState: row.in_use ? "active" : "idle",
  }));
}

/** Commands a bash call left running, shown as background terminals. An
 *  ended one stays listed only while it is the one being viewed. */
export function backgroundTerminalRows(
  processes: readonly ManagedProcess[], sessionId: string | null, viewingId?: string | null,
): SessionResource[] {
  if (!sessionId) return [];
  const active = (item: ManagedProcess) => ["starting", "running", "stopping", "unknown"].includes(item.status);
  return processes.filter(item => item.background && (active(item) || item.id === viewingId)).map(item => ({
    id: `process:${item.id}`, sourceId: item.id, source: "process" as const,
    sessionId, conversationSessionId: sessionId, kind: "terminal",
    title: item.display?.name || item.command.slice(0, 60), target: item.command,
    status: item.status, executionId: item.execution_id,
  }));
}

const URL_LIKE_TITLE = /^(?:[a-z][a-z0-9+.-]*:\/\/|[^\s/]+\.[a-z]{2,}(?:[/:?#]|$))/i;
// Pages can title themselves with only direction marks or zero-width spaces.
const INVISIBLE_TITLE = /^[\s\u200B-\u200F\u202A-\u202E\u2060-\u2064\uFEFF]*$/;

/** A title that only stands in while a Page loads: blank, invisible, or its URL. */
export function isPlaceholderPageTitle(value: string | null | undefined): boolean {
  return !value || INVISIBLE_TITLE.test(value) || URL_LIKE_TITLE.test(value);
}

/** A web row's title is captured when its Page binds and can be blank, the
 *  URL, or a loading placeholder; a tab title reported by the live native
 *  Page is newer, otherwise prefer a real title from the row or its tab. */
export function resourceDisplayTitle(
  row: SessionResource,
  tab?: { title?: string; url?: string; urlNativeAt?: number } | null,
): string {
  const ordered = Number(tab?.urlNativeAt) > 0 ? [tab?.title, row.title] : [row.title, tab?.title];
  const titles = ordered.filter((value): value is string => (
    !!value && !INVISIBLE_TITLE.test(value)
  ));
  return titles.find(value => !URL_LIKE_TITLE.test(value))
    || titles[0] || row.target || tab?.url || "";
}

export function resourceIsUnavailable(row: SessionResource): boolean {
  return row.status === "closed" || row.status === "exited";
}

const RESOURCE_KIND_ORDER = ["web", "vm", "desktop", "terminal", "application", "docker", "remote", "other"];

/** The backend projects one Page once per branch that used it; the list shows
 *  the Page once, preferring the viewed branch's row, then the newest. */
function onePerResource(rows: readonly SessionResource[], viewedBranchId: string | null): SessionResource[] {
  const kept = new Map<string, SessionResource>();
  for (const row of rows) {
    const key = row.resourceId ? `resource:${row.resourceId}` : `row:${row.id}`;
    const previous = kept.get(key);
    if (!previous) {
      kept.set(key, row);
      continue;
    }
    const viewed = (row.branchId || null) === viewedBranchId;
    const previousViewed = (previous.branchId || null) === viewedBranchId;
    if (viewed !== previousViewed ? viewed : (row.sequence ?? 0) > (previous.sequence ?? 0)) {
      kept.set(key, row);
    }
  }
  return [...kept.values()];
}

/** Classify display groups from resource descriptors, never names or branches. */
/** Groups every resource by kind, in panel order. `always` names kinds
 *  listed even with no rows, so the panel's default groups never vanish. */
export function groupSessionResources(
  rows: readonly SessionResource[],
  viewedBranchId: string | null = null,
  always: readonly string[] = [],
): ResourceGroup[] {
  const groups = new Map<string, ResourceGroup>();
  for (const row of onePerResource(rows, viewedBranchId)) {
    if (resourceIsUnavailable(row)) continue;
    const kind = row.source === "browser" || row.source === "web" ? "web" : row.kind;
    const key = RESOURCE_KIND_ORDER.includes(kind) ? kind : "other";
    let group = groups.get(key);
    if (!group) {
      group = { key, rows: [] };
      groups.set(key, group);
    }
    group.rows.push(row);
  }
  for (const key of always) if (!groups.has(key)) groups.set(key, { key, rows: [] });
  return RESOURCE_KIND_ORDER.flatMap(key => groups.get(key) ? [groups.get(key)!] : []);
}

function inCurrentScope(row: SessionResource, scopeSessionId: string): boolean {
  return row.conversationSessionId === scopeSessionId || row.sessionId === scopeSessionId || row.scopeSessionId === scopeSessionId;
}

function rememberFollow(row: SessionResource, previous: SessionResource | undefined, origin: IngestOrigin): void {
  if (!admittedOperation(row.lastOperation) || !row.lastOperation) return;
  if (origin === "snapshot") return;
  if (previous?.lastOperation?.id === row.lastOperation.id) return;
  const sessionId = row.conversationSessionId || row.scopeSessionId || row.sessionId;
  if (!sessionId) return;
  const key = prefKey(sessionId, row.branchId || null);
  const state = useBrowserResourceStore.getState();
  if (state.seenOperations[row.id] === row.lastOperation.id) return;
  const pref = { ...defaultPref(), ...state.preferences[key] };
  const patch: Partial<BrowserResourceState> = {
    seenOperations: { ...state.seenOperations, [row.id]: row.lastOperation.id },
    latestFollow: { ...state.latestFollow, [key]: row.id },
    followEpoch: state.followEpoch + 1,
  };
  useBrowserResourceStore.setState(patch);
  if (pref.mode === "follow") writePref(sessionId, row.branchId || null, { targetId: row.id });
}

export function ingestBrowserResource(
  raw: BackendResource | Record<string, unknown>,
  scopeSessionId: string,
  opts: { origin?: IngestOrigin } = {},
): SessionResource | null {
  if (!raw || typeof raw !== "object" || typeof (raw as BackendResource).id !== "string") return null;
  const rawItem = raw as BackendResource;
  const item = { ...rawItem, source: rawItem.source || "browser" };
  const row = normalizeBackendResource(item, scopeSessionId);
  if (!row) return null;
  if (item.session_id && item.session_id !== row.sessionId) return null;
  if (scopeSessionId && row.conversationSessionId && row.conversationSessionId !== scopeSessionId && row.sessionId !== scopeSessionId) {
    return null;
  }
  let state = useBrowserResourceStore.getState();
  const resourceId = row.resourceId || row.sourceId;
  const pending = resourceId ? state.optimisticClosed[resourceId] : undefined;
  const origin = opts.origin || "event";
  const generation = row.generation ?? 0;
  const live = row.status !== "closed" && row.controlState !== "closed";
  if (pending) {
    const expired = Date.now() >= pending.expiresAt;
    const staleClosed = !live && (
      generation < pending.generation
      || (generation === pending.generation && pending.rows.some(item => (
        (item.generation ?? 0) === generation && (item.sequence ?? 0) >= (row.sequence ?? 0)
      )))
    );
    if (staleClosed) return null;
    if (!live || origin === "snapshot" || generation > pending.generation || expired) {
      const restoreRows = (!live || (origin === "snapshot" && generation <= pending.generation) || expired)
        ? pending.rows : [];
      if (restoreRows.length > 0) {
        const rows = { ...state.rows };
        const rowClock = { ...state.rowClock };
        let clock = state.ingestClock;
        for (const item of restoreRows) {
          if (rows[item.id]) continue;
          clock += 1;
          rows[item.id] = item;
          rowClock[item.id] = clock;
        }
        state = { ...state, rows, rowClock, ingestClock: clock };
        useBrowserResourceStore.setState({ rows, rowClock, ingestClock: clock });
      }
      clearOptimisticClose(resourceId);
      const optimisticClosed = { ...state.optimisticClosed };
      delete optimisticClosed[resourceId];
      useBrowserResourceStore.setState({ optimisticClosed });
      state = useBrowserResourceStore.getState();
    } else if (generation <= pending.generation) {
      return null;
    }
  }
  if (!live && resourceId) {
    const rows = { ...state.rows };
    let changed = false;
    let matched = false;
    for (const [id, existing] of Object.entries(rows)) {
      if (existing.resourceId !== resourceId) continue;
      matched = true;
      const existingGeneration = existing.generation ?? 0;
      const existingSequence = existing.sequence ?? 0;
      if (existingGeneration > generation
        || (existingGeneration === generation && existingSequence >= (row.sequence ?? 0))) continue;
      rows[id] = { ...existing, status: row.status, controlState: row.controlState,
        sequence: Math.max(existing.sequence ?? 0, row.sequence ?? 0) };
      changed = true;
    }
    if (changed) {
      // A close is a change the panes show (the page's floating Agent button
      // leaves with it), so it advances the clock they subscribe to; no row
      // is stamped, so a snapshot begun earlier still sees nothing new.
      useBrowserResourceStore.setState({ rows, ingestClock: state.ingestClock + 1 });
      return row;
    }
    if (matched) {
      return Object.values(state.rows).find(item => item.resourceId === resourceId) || null;
    }
  }
  const existing = state.rows[row.id];
  if (existing) {
    const nextGen = row.generation ?? 0;
    const prevGen = existing.generation ?? 0;
    if (nextGen < prevGen) return existing;
    if (nextGen === prevGen && (row.sequence ?? 0) < (existing.sequence ?? 0)) return existing;
  }
  const ingestClock = state.ingestClock + 1;
  useBrowserResourceStore.setState({
    ingestClock,
    rows: { ...state.rows, [row.id]: row },
    rowClock: { ...state.rowClock, [row.id]: ingestClock },
  });
  rememberFollow(row, existing, opts.origin || "event");
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("op:browser-resource-ingested", { detail: { id: row.id } }));
  }
  return useBrowserResourceStore.getState().rows[row.id] || row;
}

export function resetBrowserResources(sessionId?: string): void {
  if (sessionId === undefined) {
    for (const resourceId of optimisticCloseTimers.keys()) clearOptimisticClose(resourceId);
    useBrowserResourceStore.setState({
      rows: {},
      ingestClock: 0,
      rowClock: {},
      viewedBranch: {},
      latestFollow: {},
      seenOperations: {},
      preferences: {},
      connected: true,
      followEpoch: 0,
      snapshotComplete: {},
      snapshotFailed: {},
      optimisticClosed: {},
    });
    return;
  }
  // Switching the viewed conversation must not drop retained rows.
}

const OPTIMISTIC_CLOSE_TIMEOUT_MS = 15000;
const optimisticCloseTimers = new Map<string, ReturnType<typeof setTimeout>>();

function clearOptimisticClose(resourceId: string): void {
  const timer = optimisticCloseTimers.get(resourceId);
  if (timer !== undefined) clearTimeout(timer);
  optimisticCloseTimers.delete(resourceId);
}

function restoreOptimisticClose(resourceId: string): void {
  const state = useBrowserResourceStore.getState();
  const pending = state.optimisticClosed[resourceId];
  if (!pending) return;
  const rows = { ...state.rows };
  const rowClock = { ...state.rowClock };
  let clock = state.ingestClock;
  for (const row of pending.rows) {
    if (rows[row.id]) continue;
    clock += 1;
    rows[row.id] = row;
    rowClock[row.id] = clock;
  }
  const optimisticClosed = { ...state.optimisticClosed };
  delete optimisticClosed[resourceId];
  clearOptimisticClose(resourceId);
  useBrowserResourceStore.setState({ rows, rowClock, ingestClock: clock, optimisticClosed });
}

function dropOptimisticClose(resourceId: string): void {
  const state = useBrowserResourceStore.getState();
  if (!state.optimisticClosed[resourceId]) return;
  const optimisticClosed = { ...state.optimisticClosed };
  delete optimisticClosed[resourceId];
  clearOptimisticClose(resourceId);
  useBrowserResourceStore.setState({ optimisticClosed });
}

/** Hide one Page until a close event, authoritative snapshot, or timeout. */
export function optimisticallyCloseBrowserResource(resourceId: string, generation = 0): void {
  if (!resourceId) return;
  const state = useBrowserResourceStore.getState();
  const rows = Object.fromEntries(Object.entries(state.rows).filter(([, row]) => row.resourceId !== resourceId));
  const prior = state.optimisticClosed[resourceId];
  clearOptimisticClose(resourceId);
  const hiddenRows = Object.values(state.rows).filter(row => row.resourceId === resourceId);
  const pending = {
    generation: Math.max(generation, prior?.generation ?? 0),
    expiresAt: Date.now() + OPTIMISTIC_CLOSE_TIMEOUT_MS,
    rows: prior?.rows?.length ? prior.rows : hiddenRows,
  };
  useBrowserResourceStore.setState({
    rows,
    optimisticClosed: {
      ...state.optimisticClosed,
      [resourceId]: pending,
    },
  });
  optimisticCloseTimers.set(resourceId, setTimeout(() => restoreOptimisticClose(resourceId), OPTIMISTIC_CLOSE_TIMEOUT_MS));
}

/**
 * Close one Page by resource id. The server record is authoritative even
 * when the row's tab is already gone, so a row can always be closed; a
 * failed request brings the hidden row back at once.
 */
export async function requestCloseBrowserResource(
  input: { conversationSessionId: string; resourceId: string },
  post?: (url: string, init: RequestInit) => Promise<unknown>,
): Promise<boolean> {
  try {
    const send = post ?? (await import("../../net/fetch-client.ts")).jsonFetch;
    const data = await send(
      `/api/session/${encodeURIComponent(input.conversationSessionId)}/resources/${encodeURIComponent(input.resourceId)}/close`,
      { method: "POST", body: "{}" },
    ) as { items?: unknown };
    const items = Array.isArray(data?.items) ? data.items as BackendResource[] : [];
    for (const item of items) ingestBrowserResource(item, input.conversationSessionId, { origin: "event" });
    return true;
  } catch (error) {
    // 404: the Page is already gone for this conversation; keep it hidden.
    if ((error as { status?: number })?.status === 404) {
      dropOptimisticClose(input.resourceId);
      return true;
    }
    restoreOptimisticClose(input.resourceId);
    return false;
  }
}

export function completeResourceSnapshot(sessionId: string, result: { ok: boolean }): void {
  if (!sessionId) return;
  const state = useBrowserResourceStore.getState();
  const snapshotFailed = { ...state.snapshotFailed };
  if (result.ok) delete snapshotFailed[sessionId];
  else snapshotFailed[sessionId] = true;
  useBrowserResourceStore.setState({
    snapshotComplete: { ...state.snapshotComplete, [sessionId]: true },
    snapshotFailed,
  });
}

export function sessionResourceView(sessionId: string | null): {
  rows: SessionResource[];
  loaded: boolean;
  unavailable: boolean;
  currentBranchId: string | null;
} {
  const state = useBrowserResourceStore.getState();
  if (!sessionId) {
    return {
      rows: [],
      loaded: true,
      unavailable: !state.connected,
      currentBranchId: null,
    };
  }
  return {
    rows: Object.values(state.rows).filter(row => (
      row.sessionId === sessionId || row.conversationSessionId === sessionId || row.scopeSessionId === sessionId
    )),
    loaded: !!state.snapshotComplete[sessionId],
    unavailable: !state.connected || !!state.snapshotFailed[sessionId],
    currentBranchId: state.viewedBranch[sessionId] ?? null,
  };
}

const recovering = new Map<string, Promise<void>>();

/** Read a conversation's resources. The read is costly on the server, and
 *  restoration, the poll and branch changes can each ask at once: callers
 *  arriving while one is out share it. */
export function recoverSessionResources(
  sessionId: string,
  get?: (url: string, init?: RequestInit) => Promise<unknown>,
): Promise<void> {
  const pending = recovering.get(sessionId);
  if (pending) return pending;
  const request = readSessionResources(sessionId, get).finally(() => recovering.delete(sessionId));
  recovering.set(sessionId, request);
  return request;
}

async function readSessionResources(
  sessionId: string,
  get?: (url: string, init?: RequestInit) => Promise<unknown>,
): Promise<void> {
  const begunClock = beginResourceSnapshotClock();
  try {
    const send = get ?? (await import("../../net/fetch-client.ts")).jsonFetch;
    const data = await send(
      `/api/session/${encodeURIComponent(sessionId)}/resources`,
      { cache: "no-store" },
    ) as {
      items?: BackendResource[];
      item?: BackendResource;
      current_branch_id?: string | null;
    };
    const items = Array.isArray(data.items) ? data.items : data.item ? [data.item] : [];
    applyResourceSnapshot(items, sessionId, { begunClock });
    if (data.current_branch_id !== undefined) setViewedBranch(sessionId, data.current_branch_id || null);
  } catch (error) {
    completeResourceSnapshot(sessionId, { ok: false });
    throw error;
  }
}

export function beginResourceSnapshotClock(): number {
  return useBrowserResourceStore.getState().ingestClock;
}

export function listedBrowserResources(): SessionResource[] {
  return Object.values(useBrowserResourceStore.getState().rows);
}

export function setViewedBranch(sessionId: string, branchId: string | null): void {
  useBrowserResourceStore.setState(state => ({
    viewedBranch: { ...state.viewedBranch, [sessionId]: branchId },
  }));
}

export function viewedBranchFor(sessionId: string): string | null {
  return useBrowserResourceStore.getState().viewedBranch[sessionId] ?? null;
}

export function setBrowserConnection(connected: boolean): void {
  const state = useBrowserResourceStore.getState();
  if (connected) {
    useBrowserResourceStore.setState({ connected: true });
    return;
  }
  const rows = Object.fromEntries(Object.entries(state.rows).map(([id, row]) => [
    id,
    row.controlState === "closed" || row.status === "closed"
      ? row
      : { ...row, controlState: "unknown" as const },
  ]));
  useBrowserResourceStore.setState({ connected: false, rows });
}

export function browserConnectionOpen(): boolean {
  return useBrowserResourceStore.getState().connected;
}

export function applyResourceSnapshot(
  items: readonly BackendResource[],
  scopeSessionId: string,
  opts: { begunClock?: number } = {},
): SessionResource[] {
  const begunClock = opts.begunClock ?? useBrowserResourceStore.getState().ingestClock;
  const snapshotIds = new Set(backendResourceRows(items, scopeSessionId).map(row => row.id));
  for (const item of items) ingestBrowserResource(item, scopeSessionId, { origin: "snapshot" });
  const state = useBrowserResourceStore.getState();
  const rows = { ...state.rows };
  const rowClock = { ...state.rowClock };
  for (const [id, row] of Object.entries(rows)) {
    if (!inCurrentScope(row, scopeSessionId) || snapshotIds.has(id)) continue;
    if ((rowClock[id] || 0) <= begunClock) {
      delete rows[id];
      delete rowClock[id];
    }
  }
  useBrowserResourceStore.setState({ rows, rowClock });
  completeResourceSnapshot(scopeSessionId, { ok: true });
  return Object.values(useBrowserResourceStore.getState().rows).filter(row => inCurrentScope(row, scopeSessionId));
}

export function getPreviewPreference(sessionId: string, branchId: string | null): PreviewPreference {
  return { ...defaultPref(), ...useBrowserResourceStore.getState().preferences[prefKey(sessionId, branchId)] };
}

export function latestFollowTarget(sessionId: string, branchId: string | null): string | null {
  return useBrowserResourceStore.getState().latestFollow[prefKey(sessionId, branchId)] ?? null;
}

export function selectResourcePreview(sessionId: string, branchId: string | null, resourceId: string): PreviewPreference {
  return writePref(sessionId, branchId, {
    targetId: resourceId, mode: "manual", hidden: false,
  });
}

/** Show a Page now without pinning: the next Agent operation moves on. */
export function previewResource(sessionId: string, branchId: string | null, resourceId: string): PreviewPreference {
  const pref = getPreviewPreference(sessionId, branchId);
  if (pref.mode === "follow") {
    const key = prefKey(sessionId, branchId);
    useBrowserResourceStore.setState(state => ({
      latestFollow: { ...state.latestFollow, [key]: resourceId },
    }));
  }
  return writePref(sessionId, branchId, { targetId: resourceId, hidden: false });
}

export function followCurrentBranch(sessionId: string, branchId: string | null): PreviewPreference {
  return writePref(sessionId, branchId, {
    mode: "follow",
    targetId: latestFollowTarget(sessionId, branchId) ?? getPreviewPreference(sessionId, branchId).targetId,
    hidden: false,
  });
}

/** The Agent operated a native app window or a terminal: the preview
 *  follows it, the way it follows Pages, unless a pin holds it elsewhere. */
export function followAppTarget(sessionId: string, target: string): void {
  const branchId = viewedBranchFor(sessionId);
  const key = prefKey(sessionId, branchId);
  const state = useBrowserResourceStore.getState();
  const pref = { ...defaultPref(), ...state.preferences[key] };
  useBrowserResourceStore.setState({
    latestFollow: { ...state.latestFollow, [key]: target },
    followEpoch: state.followEpoch + 1,
  });
  if (pref.mode === "follow") writePref(sessionId, branchId, { targetId: target });
}

export function followNativeWindow(sessionId: string, windowId: number): void {
  followAppTarget(sessionId, `${NATIVE_WINDOW_TARGET}${windowId}`);
}

/** The app window or terminal the preview shows for this branch, if any. */
export function appPreviewTarget(sessionId: string, branchId: string | null): string | null {
  const pref = getPreviewPreference(sessionId, branchId);
  if (pref.hidden) return null;
  const target = pref.mode === "follow"
    ? latestFollowTarget(sessionId, branchId) ?? pref.targetId
    : pref.targetId;
  return isAppTarget(target) ? target : null;
}

/** The native window the preview shows for this branch, if it shows one. */
export function nativeWindowPreview(sessionId: string, branchId: string | null): number | null {
  const target = appPreviewTarget(sessionId, branchId);
  if (!target?.startsWith(NATIVE_WINDOW_TARGET)) return null;
  const windowId = Number(target.slice(NATIVE_WINDOW_TARGET.length));
  return Number.isInteger(windowId) ? windowId : null;
}

export function hideResourcePreview(sessionId: string, branchId: string | null): PreviewPreference {
  return writePref(sessionId, branchId, { hidden: true });
}

export function showResourcePreview(sessionId: string, branchId: string | null): PreviewPreference {
  const pref = getPreviewPreference(sessionId, branchId);
  const targetId = pref.mode === "follow"
    ? latestFollowTarget(sessionId, branchId) ?? pref.targetId
    : pref.targetId;
  return writePref(sessionId, branchId, { hidden: false, targetId });
}

export function togglePreviewExpanded(sessionId: string, branchId: string | null): PreviewPreference {
  const pref = getPreviewPreference(sessionId, branchId);
  return writePref(sessionId, branchId, { expanded: !pref.expanded });
}

export async function requestResourceControl(
  input: ResourceControlRequest,
  post?: (url: string, init: RequestInit) => Promise<unknown>,
): Promise<BackendResource> {
  const send = post ?? (await import("../../net/fetch-client.ts")).jsonFetch;
  const body = {
    action: input.action,
    command_id: input.commandId,
    generation: input.generation,
  };
  const data = await send(
    `/api/session/${encodeURIComponent(input.conversationSessionId)}/resources/${encodeURIComponent(input.resourceId)}/control`,
    { method: "POST", body: JSON.stringify(body) },
  );
  const row = unwrapResourcePayload(data);
  if (row) ingestBrowserResource(row, input.conversationSessionId, { origin: "event" });
  return row || (data as BackendResource);
}

export function previewTabId(row: SessionResource | undefined): string | null {
  if (!row) return null;
  return row.tabId || (row.source === "web" ? row.sourceId : null);
}

export function resourceIsOperating(row: SessionResource): boolean {
  if (row.lastOperation?.phase === "dispatched") return true;
  return row.controlState === "active" && (row.source === "browser" || row.source === "terminal");
}

export function existingResourceTabId(
  row: SessionResource | undefined,
  tabs: readonly { id: string }[],
): string | null {
  const tabId = previewTabId(row);
  if (!tabId || !tabs.some(tab => tab.id === tabId)) return null;
  return tabId;
}

export function followPreviewBinding(
  sessionId: string,
  branchId: string | null,
  tabs: readonly { id: string; kind: string; sessionId?: string }[],
): { tabId: string; ownerTabId: string } | null {
  const pref = getPreviewPreference(sessionId, branchId);
  if (pref.hidden) return null;
  const targetId = pref.mode === "follow"
    ? latestFollowTarget(sessionId, branchId) ?? pref.targetId
    : pref.targetId;
  if (!targetId) return null;
  const row = useBrowserResourceStore.getState().rows[targetId];
  // A Page the server has no record for (a site-opened popup, say) is listed
  // from its tab as ``tab:<id>``; preview that tab directly.
  const tabId = row ? existingResourceTabId(row, tabs)
    : targetId.startsWith("tab:") && tabs.some(tab => tab.id === targetId.slice(4))
      ? targetId.slice(4) : null;
  const owner = tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
  if (!tabId || !owner) return null;
  return { tabId, ownerTabId: owner.id };
}

export function associationsForResource(resourceId: string): SessionResource[] {
  return listedBrowserResources().filter(row => row.resourceId === resourceId);
}
