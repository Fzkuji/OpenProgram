import { create } from "zustand";
import {
  associationsForResource,
  browserConnectionOpen,
  listedBrowserResources,
  previewTabId,
  requestResourceControl,
  useBrowserResourceStore,
  type BrowserControlState,
  type BrowserLastOperation,
  type SessionResource,
} from "../chat/session-resources.ts";

export type HumanYieldEvent = {
  type: string;
  key?: string;
};

export type BrowserControlResource = {
  id: string;
  resourceId: string;
  tabId?: string | null;
  conversationSessionId: string;
  generation: number;
  controlState?: BrowserControlState;
};

export type PendingYield = {
  since: number;
  commandId: string;
  generation: number;
  posted: boolean;
  failed?: boolean;
};

export type PendingClose = {
  tabId: string;
  resourceId: string;
  generation: number;
  associationIds: string[];
  executionIds: string[];
  error?: string;
};

type MarkerRecord = {
  operation: BrowserLastOperation;
  generation: number;
  expiresAt: number;
};

type BrowserControlStateStore = {
  pending: Record<string, PendingYield>;
  showActions: boolean;
  history: Record<string, BrowserLastOperation[]>;
  markers: Record<string, MarkerRecord>;
  resumeError: Record<string, string>;
  pendingCloses: PendingClose[];
  inflight: Record<string, true>;
};

const STOP_UNCONFIRMED_MS = 5000;
const HISTORY_LIMIT = 20;
const MARKER_TTL_MS = 1800;
const PASSIVE_KEYS = new Set(["Tab", "Shift", "Control", "Alt", "Meta"]);

export const useBrowserControlStore = create<BrowserControlStateStore>(() => ({
  pending: {},
  showActions: true,
  history: {},
  markers: {},
  resumeError: {},
  pendingCloses: [],
  inflight: {},
}));

export function resetBrowserControl(): void {
  useBrowserControlStore.setState({
    pending: {},
    showActions: true,
    history: {},
    markers: {},
    resumeError: {},
    pendingCloses: [],
    inflight: {},
  });
}

export function isHumanYieldEvent(_event: HumanYieldEvent): boolean {
  return false;
}

function pendingKey(resourceId: string, generation: number): string {
  return `${resourceId}:${generation}`;
}

function clearPendingLease(resourceId: string, generation: number): void {
  const key = pendingKey(resourceId, generation);
  const current = useBrowserControlStore.getState();
  if (!current.pending[key] && !current.inflight[key]) return;
  const pending = { ...current.pending };
  delete pending[key];
  const inflight = { ...current.inflight };
  delete inflight[key];
  useBrowserControlStore.setState({ pending, inflight });
}

function acknowledgeIngestedControl(): void {
  const current = new Map<string, {
    resourceId: string;
    generation: number;
    sequence: number;
    states: Set<string>;
  }>();
  for (const row of listedBrowserResources()) {
    const resourceId = row.resourceId || row.id;
    const generation = row.generation || 0;
    const sequence = row.sequence ?? 0;
    const key = pendingKey(resourceId, generation);
    const state = row.controlState || "unknown";
    const seen = current.get(key);
    if (!seen || sequence > seen.sequence) {
      current.set(key, { resourceId, generation, sequence, states: new Set([state]) });
    } else if (sequence === seen.sequence) {
      seen.states.add(state);
    }
  }
  for (const group of current.values()) {
    if (![...group.states].every(state => state === "paused" || state === "waiting" || state === "closed" || state === "idle")) continue;
    clearPendingLease(group.resourceId, group.generation);
  }
}

useBrowserResourceStore.subscribe((state, prev) => {
  if (state.rows === prev.rows) return;
  acknowledgeIngestedControl();
});

export function displayedControlState(
  resource: BrowserControlResource,
  opts: { now?: number } = {},
): BrowserControlState {
  const backend = resource.controlState || "unknown";
  if (!browserConnectionOpen() && backend !== "closed") return "unknown";
  if (backend === "paused" || backend === "waiting" || backend === "closed" || backend === "idle") return backend;
  const lease = useBrowserControlStore.getState().pending[pendingKey(resource.resourceId, resource.generation)];
  if (lease && !lease.failed) {
    const now = opts.now ?? Date.now();
    if (now - lease.since >= STOP_UNCONFIRMED_MS) return "stop_unconfirmed";
    return "yielding";
  }
  if (backend === "stop_unconfirmed" || lease?.failed) return "stop_unconfirmed";
  return backend;
}

function toControlResource(resource: BrowserControlResource | SessionResource): BrowserControlResource {
  const conversationSessionId = "conversationSessionId" in resource
    ? resource.conversationSessionId || ("scopeSessionId" in resource ? resource.scopeSessionId : undefined) || ("sessionId" in resource ? resource.sessionId : undefined)
    : "";
  return {
    id: resource.id,
    resourceId: resource.resourceId || resource.id,
    tabId: resource.tabId ?? null,
    conversationSessionId: conversationSessionId || "",
    generation: resource.generation || 0,
    controlState: resource.controlState,
  };
}

function notify(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("op:browser-resource-ingested", { detail: {} }));
}

export function markScopeYielding(
  resource: BrowserControlResource | SessionResource,
  deps: { now?: () => number; commandId?: string } = {},
): BrowserControlState {
  const row = toControlResource(resource);
  if (!row.resourceId) return displayedControlState(row);
  const key = pendingKey(row.resourceId, row.generation);
  if (row.controlState === "idle" || row.controlState === "closed") {
    const current = useBrowserControlStore.getState();
    if (current.pending[key] || current.inflight[key]) {
      clearPendingLease(row.resourceId, row.generation);
      notify();
    }
    return displayedControlState(row);
  }
  const current = useBrowserControlStore.getState();
  if (!current.pending[key]) {
    useBrowserControlStore.setState({
      pending: {
        ...current.pending,
        [key]: {
          since: (deps.now || Date.now)(),
          commandId: deps.commandId || crypto.randomUUID(),
          generation: row.generation,
          posted: false,
        },
      },
      markers: Object.fromEntries(
        Object.entries(current.markers).filter(([id]) => id !== row.resourceId),
      ),
    });
  } else {
    useBrowserControlStore.setState({
      markers: Object.fromEntries(
        Object.entries(current.markers).filter(([id]) => id !== row.resourceId),
      ),
    });
  }
  notify();
  return displayedControlState(row);
}

async function postPauseOnce(
  row: BrowserControlResource,
  deps: { postControl?: typeof requestResourceControl } = {},
): Promise<BrowserControlState> {
  const key = pendingKey(row.resourceId, row.generation);
  const current = useBrowserControlStore.getState();
  const lease = current.pending[key];
  if (!lease) return displayedControlState(row);
  if (current.inflight[key]) return displayedControlState(row);
  const shown = displayedControlState(row);
  const expiredOrFailed = shown === "stop_unconfirmed" || !!lease.failed;
  if (lease.posted && !expiredOrFailed) return shown;
  const retryPosted = lease.posted && expiredOrFailed;
  const commandId = retryPosted ? crypto.randomUUID() : lease.commandId;
  useBrowserControlStore.setState({
    pending: {
      ...current.pending,
      [key]: {
        ...lease,
        posted: true,
        failed: false,
        commandId,
        since: expiredOrFailed ? Date.now() : lease.since,
      },
    },
    inflight: { ...current.inflight, [key]: true },
  });
  notify();
  try {
    const posted = await (deps.postControl || requestResourceControl)({
      conversationSessionId: row.conversationSessionId,
      resourceId: row.resourceId,
      action: "pause",
      commandId,
      generation: row.generation,
    });
    const state = posted.control_state
      || ("controlState" in posted ? (posted as { controlState?: BrowserControlState }).controlState : undefined);
    if (state === "paused" || state === "idle" || state === "closed") {
      clearPendingLease(row.resourceId, row.generation);
      notify();
      return state;
    }
    const latest = useBrowserControlStore.getState();
    const inflight = { ...latest.inflight };
    delete inflight[key];
    const currentLease = latest.pending[key];
    if (state === "stop_unconfirmed") {
      useBrowserControlStore.setState({
        inflight,
        pending: currentLease
          ? { ...latest.pending, [key]: { ...currentLease, failed: true } }
          : latest.pending,
      });
      notify();
      return "stop_unconfirmed";
    }
    useBrowserControlStore.setState({ inflight });
  } catch {
    const latest = useBrowserControlStore.getState();
    const inflight = { ...latest.inflight };
    delete inflight[key];
    const currentLease = latest.pending[key];
    useBrowserControlStore.setState({
      inflight,
      pending: currentLease
        ? { ...latest.pending, [key]: { ...currentLease, failed: true } }
        : latest.pending,
    });
  }
  notify();
  return displayedControlState(row);
}

/** Native path: markScopeYielding. postControl/post:true is explicit HTTP pause. */
export function signalHumanBrowserInput(
  resource: BrowserControlResource | SessionResource,
  deps: {
    postControl?: typeof requestResourceControl;
    now?: () => number;
    commandId?: string;
    post?: boolean;
  } = {},
): Promise<BrowserControlState> {
  return Promise.resolve(displayedControlState(toControlResource(resource)));
}

export async function requestExplicitPause(
  resource: BrowserControlResource | SessionResource,
  deps: { postControl?: typeof requestResourceControl; now?: () => number; commandId?: string } = {},
): Promise<BrowserControlState> {
  const row = toControlResource(resource);
  markScopeYielding(row, deps);
  return postPauseOnce(row, deps);
}

export function setControlNotice(resourceId: string, message: string): void {
  if (!resourceId) return;
  useBrowserControlStore.setState(state => ({
    resumeError: { ...state.resumeError, [resourceId]: message },
  }));
  notify();
}

export function revealPendingApproval(
  resource: BrowserControlResource | SessionResource,
): BrowserControlState {
  const row = toControlResource(resource);
  const shown = displayedControlState(row);
  if (shown !== "waiting") {
    setControlNotice(row.resourceId, "Status changed. Try again");
    return shown;
  }
  const sessionId = row.conversationSessionId;
  if (!sessionId) {
    setControlNotice(row.resourceId, "Status changed. Try again");
    return shown;
  }
  void Promise.all([
    import("../session-store/index.ts"),
    import("../tabs/center-tabs-store.ts"),
  ]).then(([{ useSessionStore }, { useCenterTabs }]) => {
    const title = useSessionStore.getState().conversations[sessionId]?.title || sessionId;
    const tabs = useCenterTabs.getState();
    const existing = tabs.tabs.find(tab => tab.kind === "session" && tab.sessionId === sessionId);
    if (existing) {
      if (tabs.activeId !== existing.id) tabs.setActive(existing.id);
    } else {
      tabs.openSessionTab(sessionId, title);
    }
    useSessionStore.getState().setCurrentConv(sessionId);
    useSessionStore.getState().focusComposer();
    if (typeof document === "undefined") return;
    const reveal = () => {
      const card = document.querySelector("[data-pending-decision-id], [data-composer-input-area]");
      if (card && "scrollIntoView" in card) {
        (card as HTMLElement).scrollIntoView({ block: "nearest" });
      }
      const focusable = card?.querySelector?.("button, [tabindex]:not([tabindex='-1'])") as HTMLElement | null;
      focusable?.focus?.();
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(reveal);
    else reveal();
  });
  return shown;
}

export async function requestResumeAgent(
  resource: BrowserControlResource | SessionResource,
  deps: { postControl?: typeof requestResourceControl; commandId?: string } = {},
): Promise<BrowserControlState | null> {
  const row = toControlResource(resource);
  if (!browserConnectionOpen()) return null;
  const shown = displayedControlState(row);
  if (shown === "waiting") {
    setControlNotice(row.resourceId, "Needs your confirmation");
    return "waiting";
  }
  if (shown !== "paused") {
    setControlNotice(row.resourceId, "Status changed. Try again");
    return null;
  }
  try {
    const posted = await (deps.postControl || requestResourceControl)({
      conversationSessionId: row.conversationSessionId,
      resourceId: row.resourceId,
      action: "resume",
      commandId: deps.commandId || crypto.randomUUID(),
      generation: row.generation,
    });
    const state = posted.control_state
      || ("controlState" in posted ? (posted as { controlState?: BrowserControlState }).controlState : undefined)
      || "unknown";
    if (state === "paused") return "paused";
    clearPendingLease(row.resourceId, row.generation);
    const resumeError = { ...useBrowserControlStore.getState().resumeError };
    delete resumeError[row.resourceId];
    useBrowserControlStore.setState({ resumeError });
    return state;
  } catch (error) {
    useBrowserControlStore.setState(state => ({
      resumeError: {
        ...state.resumeError,
        [row.resourceId]: error instanceof Error ? error.message : "Resume failed",
      },
    }));
    notify();
    return "paused";
  }
}

export function toggleShowActions(): boolean {
  const showActions = !useBrowserControlStore.getState().showActions;
  useBrowserControlStore.setState({
    showActions,
    markers: showActions ? useBrowserControlStore.getState().markers : {},
  });
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("op:browser-actions-changed", {
      detail: { showActions },
    }));
  }
  notify();
  return showActions;
}

export function showActionsEnabled(): boolean {
  return useBrowserControlStore.getState().showActions;
}

function liveCueAllowed(input: {
  resourceId: string;
  generation: number;
  operation: BrowserLastOperation;
  controlState?: BrowserControlState;
  geometryRevision?: number;
}): boolean {
  const state = useBrowserControlStore.getState();
  const blocked = !!state.pending[pendingKey(input.resourceId, input.generation)]
    || input.controlState === "yielding"
    || input.controlState === "paused"
    || input.controlState === "waiting"
    || input.controlState === "stop_unconfirmed"
    || input.controlState === "unknown";
  const failed = input.operation.phase === "failed" || input.operation.phase === "unknown";
  const point = input.operation.point;
  const viewport = point && typeof point.width === "number" && point.width > 0
    && typeof point.height === "number" && point.height > 0;
  const frame = typeof input.operation.frame_id === "string" && input.operation.frame_id.length > 0;
  const geometryOk = input.geometryRevision === undefined
    || input.operation.geometry_revision === input.geometryRevision;
  return !blocked && !failed && state.showActions && !!point && viewport && frame && geometryOk;
}

export function recordOperationCue(input: {
  resourceId: string;
  generation: number;
  operation: BrowserLastOperation;
  controlState?: BrowserControlState;
  geometryRevision?: number;
  now?: number;
  ttlMs?: number;
}): void {
  const state = useBrowserControlStore.getState();
  const history = [...(state.history[input.resourceId] || [])];
  const existing = history.findIndex(item => item.id === input.operation.id);
  const seenId = existing >= 0 || state.markers[input.resourceId]?.operation.id === input.operation.id;
  if (existing >= 0) history[existing] = input.operation;
  else {
    history.push(input.operation);
    if (history.length > HISTORY_LIMIT) history.shift();
  }
  const now = input.now ?? Date.now();
  const markers = { ...state.markers };
  const current = markers[input.resourceId];
  if (seenId) {
    if (current?.operation.id === input.operation.id) {
      markers[input.resourceId] = { ...current, operation: input.operation };
    }
  } else if (liveCueAllowed(input)) {
    markers[input.resourceId] = {
      operation: input.operation,
      generation: input.generation,
      expiresAt: now + (input.ttlMs ?? MARKER_TTL_MS),
    };
  } else if (current && (
    input.controlState === "yielding" || input.controlState === "paused"
    || input.controlState === "waiting"
    || input.controlState === "stop_unconfirmed" || input.operation.phase === "failed"
  )) {
    delete markers[input.resourceId];
  }
  useBrowserControlStore.setState({
    history: { ...state.history, [input.resourceId]: history },
    markers,
  });
}

export function clearMarkersForTab(tabId: string): void {
  const ids = new Set(
    listedBrowserResources()
      .filter(row => row.tabId === tabId)
      .map(row => row.resourceId)
      .filter((id): id is string => !!id),
  );
  if (ids.size === 0) return;
  const markers = { ...useBrowserControlStore.getState().markers };
  for (const id of ids) delete markers[id];
  useBrowserControlStore.setState({ markers });
}

export function operationHistory(resourceId: string): BrowserLastOperation[] {
  return [...(useBrowserControlStore.getState().history[resourceId] || [])];
}

export function liveOperationMarker(
  resourceId: string,
  opts: { generation?: number; now?: number } = {},
): BrowserLastOperation | null {
  const state = useBrowserControlStore.getState();
  if (!state.showActions) return null;
  const marker = state.markers[resourceId];
  if (!marker) return null;
  if (opts.generation !== undefined && marker.generation !== opts.generation) return null;
  if ((opts.now ?? Date.now()) >= marker.expiresAt) return null;
  return marker.operation;
}

export function controlResourceFromSession(row: SessionResource): BrowserControlResource | null {
  const conversationSessionId = row.conversationSessionId || row.scopeSessionId || row.sessionId;
  if (!row.resourceId || !conversationSessionId) return null;
  return {
    id: row.id,
    resourceId: row.resourceId,
    tabId: row.tabId,
    conversationSessionId,
    generation: row.generation || 0,
    controlState: row.controlState,
  };
}

export function resumeErrorFor(resourceId: string): string | undefined {
  return useBrowserControlStore.getState().resumeError[resourceId];
}

export function browserTakeoverKind(
  state: BrowserControlState | null | undefined,
): "reveal" | "resume" | "yielding" | "retry" | "pause" | null {
  if (!state || state === "idle" || state === "closed") return null;
  if (state === "waiting") return "reveal";
  if (state === "paused") return "resume";
  if (state === "yielding") return "yielding";
  if (state === "stop_unconfirmed") return "retry";
  return "pause";
}

export function pendingCloseRequest(): PendingClose | null {
  return useBrowserControlStore.getState().pendingCloses[0] ?? null;
}

function pendingFromRow(row: SessionResource, tabId: string): PendingClose {
  const related = associationsForResource(row.resourceId || row.id);
  return {
    tabId,
    resourceId: row.resourceId || row.id,
    generation: row.generation || 0,
    associationIds: related.map(item => item.id),
    executionIds: [...new Set(related.map(item => item.executionId).filter((id): id is string => !!id))],
  };
}

function upsertPendingClose(entry: PendingClose): void {
  const current = useBrowserControlStore.getState().pendingCloses;
  const existing = current.find(item => item.resourceId === entry.resourceId && item.tabId === entry.tabId);
  if (existing) {
    useBrowserControlStore.setState({
      pendingCloses: current.map(item => item === existing ? { ...existing, ...entry, generation: existing.generation } : item),
    });
    return;
  }
  useBrowserControlStore.setState({ pendingCloses: [...current, entry] });
}

function shownControlState(row: SessionResource): BrowserControlState {
  const control = controlResourceFromSession(row);
  return (control ? displayedControlState(control) : row.controlState) || "unknown";
}

function associationIsOperating(row: SessionResource): boolean {
  const shown = shownControlState(row);
  return shown === "active" || shown === "yielding"
    || row.controlState === "active" || row.controlState === "yielding"
    || row.lastOperation?.phase === "dispatched";
}

export function requestCloseBrowserPage(
  row: SessionResource,
  tabs: readonly { id: string }[],
): "closed" | "pending" | "unavailable" | "error" {
  const tabId = previewTabId(row);
  return tabId && tabs.some(tab => tab.id === tabId) ? "closed" : "unavailable";
}

/** Human strip/menu/Cmd+W close: only idle/non-browser tabs proceed now. */
export function selectTabsReadyForHumanClose<T extends { id: string; kind: string }>(
  tabsToClose: readonly T[],
  allTabs: readonly { id: string }[] = tabsToClose,
): T[] {
  return [...tabsToClose];
}

export function settlePendingClose(
  closeTab: (id: string) => void,
): boolean {
  const pendingCloses = useBrowserControlStore.getState().pendingCloses;
  if (pendingCloses.length === 0) return false;
  const remaining: PendingClose[] = [];
  const closedIds = new Set<string>();
  let closed = false;
  for (const pending of pendingCloses) {
    if (closedIds.has(pending.tabId)) continue;
    const rows = listedBrowserResources().filter(row => row.resourceId === pending.resourceId);
    const matching = rows.filter(row => (row.generation || 0) === pending.generation);
    const inspect = matching[0] || rows[0];
    if (inspect) {
      const shown = shownControlState(inspect);
      if (shown === "unknown" || shown === "stop_unconfirmed") {
        remaining.push({ ...pending, error: shown === "stop_unconfirmed" ? "Stop unconfirmed" : "Unknown" });
        continue;
      }
    }
    if (matching.length === 0) {
      if (rows.length === 0) {
        closeTab(pending.tabId);
        closedIds.add(pending.tabId);
        closed = true;
      }
      continue;
    }
    const released = matching.every(row => (
      row.controlState === "paused" || row.controlState === "waiting"
      || row.controlState === "idle" || row.controlState === "closed"
      || row.status === "closed" || row.status === "idle"
    ));
    if (!released) {
      remaining.push(pending);
      continue;
    }
    closeTab(pending.tabId);
    closedIds.add(pending.tabId);
    closed = true;
  }
  useBrowserControlStore.setState({
    pendingCloses: remaining.filter(item => !closedIds.has(item.tabId)),
  });
  return closed;
}

export function clearYieldingOnDisconnect(): void {
  useBrowserControlStore.setState({
    pending: {},
    markers: {},
    inflight: {},
  });
}

if (typeof window !== "undefined") {
  window.addEventListener("op:browser-geometry", (event: Event) => {
    const tabId = (event as CustomEvent<{ tabId?: string; windowId?: string; geometryRevision?: number }>).detail?.tabId;
    if (tabId) clearMarkersForTab(tabId);
  });

}
