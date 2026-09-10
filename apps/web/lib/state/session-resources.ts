import { create } from "zustand";
import type { CenterTab } from "./center-tabs-store";

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
  source: "web" | "usage" | "browser";
  sourceId: string;
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
  title: string;
  current: boolean;
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
};

function readStoredPreferences(): Record<string, PreviewPreference> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(PREF_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, PreviewPreference>;
    return Object.fromEntries(Object.entries(parsed).filter(([, value]) => value && typeof value === "object"));
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
}));

function prefKey(sessionId: string, branchId: string | null): string {
  return `${sessionId}\0${branchId || "unassigned"}`;
}

function persistPreferences(preferences: Record<string, PreviewPreference>): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(PREF_STORAGE, JSON.stringify(preferences));
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

export function resourceIsUnavailable(row: SessionResource): boolean {
  return row.status === "closed";
}

/** Unnamed branch groups show the origin after the last colon, truncated
 *  to 8 characters. The grouping key stays the full branch id. */
function unnamedBranchTitle(branchId: string): string {
  const origin = branchId.includes(":") ? branchId.slice(branchId.lastIndexOf(":") + 1) : branchId;
  return origin.slice(0, 8) || branchId;
}

export function groupSessionResources(rows: readonly SessionResource[], currentBranchId: string | null): ResourceGroup[] {
  const groups = new Map<string, ResourceGroup>();
  for (const row of rows) {
    const key = resourceIsUnavailable(row) ? "unavailable" : (row.branchId || "unassigned");
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        title: key === "unavailable" ? "Closed pages" : key === "unassigned" ? "Unassigned" : (row.branchName || unnamedBranchTitle(key)),
        current: key === currentBranchId,
        rows: [],
      };
      groups.set(key, group);
    } else if (row.branchName && (group.title === key || group.title === unnamedBranchTitle(key))) {
      group.title = row.branchName;
    }
    group.rows.push(row);
  }
  const ordered = [...groups.values()].sort((a, b) => {
    if (a.current !== b.current) return a.current ? -1 : 1;
    if (a.key === "unavailable") return 1;
    if (b.key === "unavailable") return -1;
    if (a.key === "unassigned") return 1;
    if (b.key === "unassigned") return -1;
    return 0;
  });
  return ordered;
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
  const state = useBrowserResourceStore.getState();
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
    });
    return;
  }
  // Switching the viewed conversation must not drop retained rows.
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

export async function recoverSessionResources(
  sessionId: string,
  get?: (url: string, init?: RequestInit) => Promise<unknown>,
): Promise<void> {
  const begunClock = beginResourceSnapshotClock();
  try {
    const send = get ?? (await import("../net/fetch-client.ts")).jsonFetch;
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

export function followCurrentBranch(sessionId: string, branchId: string | null): PreviewPreference {
  return writePref(sessionId, branchId, {
    mode: "follow",
    targetId: latestFollowTarget(sessionId, branchId),
    hidden: false,
  });
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
  const send = post ?? (await import("../net/fetch-client.ts")).jsonFetch;
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
  return row.controlState === "active" && row.source === "browser";
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
  const tabId = existingResourceTabId(row, tabs);
  const owner = tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
  if (!tabId || !owner) return null;
  return { tabId, ownerTabId: owner.id };
}

export function associationsForResource(resourceId: string): SessionResource[] {
  return listedBrowserResources().filter(row => row.resourceId === resourceId);
}
