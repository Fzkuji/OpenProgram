/**
 * Desktop bridge — typed accessor for the Electron preload API
 * (`window.openprogramDesktop`) plus the renderer-side bookkeeping the
 * contract leaves to us:
 *
 *  - which native web views this renderer has created (the bridge has
 *    no list call, so destroying views after their tab closes works
 *    off a local set);
 *  - the app-menu DOM CustomEvents ("op-desktop-new-tab" /
 *    "op-desktop-close-tab") wired into the center-tabs store.
 *
 * Absent bridge (plain browser) ⇒ every helper is a no-op and
 * desktopBridge() returns null; callers keep their web fallbacks.
 */
import {
  insertTransferredTabs,
  rebaseCenterTabsPayload,
  removeTransferredTabs,
  replaceCenterTabsPayload,
  snapshotCenterTabsPayload,
  useCenterTabs,
  validateTransferredTabs,
} from "@/lib/state/center-tabs-store";
import {
  findCenterTabGroup,
  resolveCenterTabPanes,
} from "@/lib/state/center-tab-groups";
import {
  applySessionTransfer,
  snapshotSessionTransfer,
} from "@/lib/session-store";
import {
  applyFileDraftSnapshot,
  snapshotFileDrafts,
} from "@/lib/state/files-shared";
import {
  registerPendingTransfer,
  unregisterPendingTransfer,
} from "@/lib/pending-transfer-projection";
import {
  deleteTransferJournal,
  finalizeTransferJournal,
  readTransferJournal,
  rebaseSessionSnapshot,
  recoverTransferJournalEntry,
  writeTransferJournal,
} from "@/lib/tab-transfer-journal";
import { sessionDraftStorageKey } from "@/lib/session-draft-persistence";
import type {
  ChatTransferState,
  DesktopTransferPayload,
  SerializedWebViewBookkeeping,
  SessionTransferSnapshot,
  TabDropPlacement,
  TransferJournalEntry,
  TransferMainStatus,
  TransferRecoveryHandlers,
} from "@/lib/tab-transfer-journal";
import { dragCoordinator } from "@/lib/tab-drag-coordinator";
import type { TabDragSubject, TabDropIntent } from "@/lib/tab-drag-coordinator";
import { useSessionStore } from "@/lib/session-store";
import { fileDraftKey, fileDrafts } from "@/lib/state/files-shared";
import {
  draftChannelChoiceFor,
  type DraftChannelChoiceHost,
} from "@/lib/runtime-bridge/draft-channel-choice";

export interface DesktopWebTabState {
  id: string;
  url?: string;
  title?: string;
  loading?: boolean;
  canGoBack?: boolean;
  canGoForward?: boolean;
  /** "" means the new page has no favicon — clear the tab's icon. */
  faviconUrl?: string;
}

export interface DesktopWebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DesktopVisibleWebView {
  id: string;
  bounds: DesktopWebTabBounds;
}

export interface DesktopWebTabApi {
  /** Create the WebContentsView for `id` if missing, then loadURL. */
  ensure(id: string, url: string): void;
  /** loadURL on the existing view (created if missing). */
  navigate(id: string, url: string): void;
  /** Ensure/navigate/show this view and return its CDP target id. */
  activate(id: string, url?: string): Promise<string | null>;
  /** DIP rect relative to the window content area. */
  setBounds(id: string, bounds: DesktopWebTabBounds): void;
  show(id: string): void;
  hide(id: string): void;
  /** Atomically replace the native views visible in this renderer window. */
  syncVisible(items: DesktopVisibleWebView[]): void;
  destroy(id: string): void;
  reload(id: string): void;
  goBack(id: string): void;
  goForward(id: string): void;
  /** Navigation/title/loading events pushed from main; returns the
   *  unsubscribe function. */
  onState(cb: (state: DesktopWebTabState) => void): () => void;
}

export interface DesktopTransferReceipt {
  token: string;
  reason?: string;
  duplicateId?: string;
  sourceId?: string;
  destinationId?: string | null;
  payload?: DesktopTransferPayload;
}

export interface DesktopTabTransferApi {
  /** Synchronous — called on pointer/mouse down, never after dragstart. */
  prepare(payload: DesktopTransferPayload): string | null;
  inspect(token: string): Promise<
    | { token: string; status: string; sourceId: string; payload: DesktopTransferPayload }
    | null
  >;
  accept(token: string, placement: TabDropPlacement): Promise<
    | {
        token: string;
        status: string;
        sourceId: string;
        destinationId: string;
        payload: DesktopTransferPayload;
        placement: TabDropPlacement;
        recordIds: string[];
      }
    | null
  >;
  reject(
    token: string,
    reason: "duplicate" | "group-full" | "invalid",
    duplicateId?: string,
  ): Promise<{ reason: string; duplicateId?: string } | null>;
  status(token: string): Promise<
    { status: string; sourceId: string; destinationId: string | null } | null
  >;
  journalOpened(token: string, role: "source" | "destination"): Promise<boolean>;
  journalFinalized(
    token: string,
    role: "source" | "destination",
    ownerWindowId?: string,
  ): Promise<boolean>;
  destinationReady(token: string, ok: boolean): Promise<boolean>;
  sourceRemoved(token: string, ok: boolean, empty: boolean): Promise<boolean>;
  destinationUndone(token: string, ok: boolean): Promise<boolean>;
  cancel(token: string): Promise<boolean>;
  /** Drop-to-place: create the torn-off window at the drop point on release
   *  and reveal it at commit. Returns the new window id, or null if the
   *  transfer did not commit. */
  detach(token: string): Promise<string | null>;
  /** Pointer-drop hit test: id of another OpenProgram window under the
   *  cursor, or null. Read-only — no transfer state changes. */
  windowAtCursor(): Promise<string | null>;
  /** Hand a prepared token to another live window so it stages the
   *  incoming transfer itself (pointer drops have no DOM drop event). */
  deliver(token: string, targetWindowId: string): Promise<boolean>;
  onStageIncoming(cb: (detail: { token: string }) => void): () => void;
  /** Cross-window drop cue: subscribes to hover-enter/leave for a drag
   *  happening in ANOTHER window. cb(true) when this window becomes the
   *  hover target, cb(false) when it stops being it. Mirrors onStageIncoming. */
  onTransferHover(cb: (entering: boolean) => void): () => void;
  claimPending(windowId: string): Promise<string | null>;
  pendingTerminal(windowId: string): Promise<Array<{
    token: string;
    status: "committed" | "rolled-back";
    role: "source" | "destination";
    windowId: string;
    orphaned: boolean;
  }>>;
  onRemoveSource(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onUndoDestination(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onCommitted(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onRejected(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onRolledBack(cb: (detail: DesktopTransferReceipt) => void): () => void;
  onFinalizeOrphaned(cb: (detail: {
    token: string;
    status: string;
    role: "source" | "destination";
    windowId: string;
    orphaned: boolean;
  }) => void): () => void;
}

/** One recorded page visit from the desktop browsing history. */
export interface DesktopHistoryEntry {
  url: string;
  title: string;
  faviconUrl: string;
  visitedAt: number;
}

export interface DesktopHistoryApi {
  list(options?: { limit?: number; query?: string }): Promise<DesktopHistoryEntry[]>;
  remove(url: string, visitedAt: number): Promise<boolean>;
  clear(): Promise<boolean>;
}

export interface DesktopBridge {
  readonly isDesktop: true;
  readonly windowId: string;
  /** shell.openExternal — http/https only. */
  openExternal(url: string): void;
  /** Close this window (last tab closed → close window). */
  closeWindow?(): void;
  /** Move this window by a pixel delta (single-tab drag = move window). */
  moveWindowBy?(dx: number, dy: number): void;
  webTab: DesktopWebTabApi;
  tabTransfer: DesktopTabTransferApi;
  /** Absent in shells older than the browsing-history build. */
  history?: DesktopHistoryApi;
}

/** The preload-exposed bridge, or null outside the desktop shell. */
export function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  return (
    ((window as unknown as { openprogramDesktop?: DesktopBridge })
      .openprogramDesktop as DesktopBridge | undefined) ?? null
  );
}

/** Native views created by THIS renderer. Main-side views orphaned by
 *  a full renderer reload are main's cleanup problem — we can't
 *  enumerate them from here. */
const liveViewIds = new Set<string>();
const readyWebTabIds = new Set<string>();
const webTabReadyWaiters = new Map<string, Set<(ready: boolean) => void>>();
const visibleWebBounds = new Map<string, DesktopWebTabBounds>();
let visibleWebFlushScheduled = false;
let visibleWebFlushBridge: DesktopBridge | null = null;
let desktopSplitLayoutAvailable = false;

function scheduleVisibleWebBoundsFlush(bridge: DesktopBridge): void {
  visibleWebFlushBridge = bridge;
  if (visibleWebFlushScheduled) return;
  visibleWebFlushScheduled = true;
  queueMicrotask(() => {
    visibleWebFlushScheduled = false;
    const targetBridge = visibleWebFlushBridge;
    visibleWebFlushBridge = null;
    if (!targetBridge) return;
    targetBridge.webTab.syncVisible(
      Array.from(visibleWebBounds, ([id, bounds]) => ({
        id,
        bounds: { ...bounds },
      })),
    );
  });
}

export function registerVisibleWebTabBounds(
  bridge: DesktopBridge,
  id: string,
  bounds: DesktopWebTabBounds,
): void {
  visibleWebBounds.set(id, { ...bounds });
  scheduleVisibleWebBoundsFlush(bridge);
}

export function removeVisibleWebTabBounds(
  bridge: DesktopBridge,
  id: string,
): void {
  visibleWebBounds.delete(id);
  scheduleVisibleWebBoundsFlush(bridge);
}

export function setWebTabReady(id: string, ready: boolean): void {
  if (!ready) {
    readyWebTabIds.delete(id);
    return;
  }
  if (readyWebTabIds.has(id)) return;
  readyWebTabIds.add(id);
  const waiters = webTabReadyWaiters.get(id);
  if (!waiters) return;
  webTabReadyWaiters.delete(id);
  for (const resolve of waiters) resolve(true);
}

export function isWebTabReady(id: string): boolean {
  return readyWebTabIds.has(id);
}

export function waitForWebTabReady(id: string, timeoutMs: number): Promise<boolean> {
  if (isWebTabReady(id)) return Promise.resolve(true);
  return new Promise((resolve) => {
    const resolveReady = (ready: boolean) => {
      clearTimeout(timeout);
      resolve(ready);
    };
    const timeout = setTimeout(() => {
      const waiters = webTabReadyWaiters.get(id);
      waiters?.delete(resolveReady);
      if (waiters?.size === 0) webTabReadyWaiters.delete(id);
      resolve(false);
    }, timeoutMs);
    const waiters = webTabReadyWaiters.get(id) ?? new Set();
    waiters.add(resolveReady);
    webTabReadyWaiters.set(id, waiters);
  });
}

export function setDesktopSplitLayoutAvailable(available: boolean): void {
  desktopSplitLayoutAvailable = available;
}

export function isDesktopSplitLayoutAvailable(): boolean {
  return desktopSplitLayoutAvailable;
}

export function ensureWebView(
  bridge: DesktopBridge,
  id: string,
  url: string,
): void {
  bridge.webTab.ensure(id, url);
  liveViewIds.add(id);
}

/** Destroy views whose center tab no longer exists. Runs on every
 *  tabs change (store subscription below) and on each web-pane mount. */
export function destroyStaleWebViews(
  bridge: DesktopBridge,
  tabIds: readonly string[],
): void {
  const alive = new Set(tabIds);
  for (const id of Array.from(liveViewIds)) {
    // A transfer transaction owns its native views until commit/rollback;
    // transient tab-list changes must not close them (main rejects the
    // destroy anyway — the lock keeps local bookkeeping consistent too).
    if (transferLockedIds.has(id)) continue;
    if (!alive.has(id)) {
      removeVisibleWebTabBounds(bridge, id);
      bridge.webTab.destroy(id);
      liveViewIds.delete(id);
    }
  }
}

let installed = false;

function showCenterSurface(): boolean {
  const navigate = (window as Window & { __navigate?: (path: string) => void })
    .__navigate;
  if (!navigate) return false;
  navigate("/chat");
  return true;
}

function showActiveCenterTab(): void {
  const state = useCenterTabs.getState();
  const active = state.tabs.find((tab) => tab.id === state.activeId);
  const path =
    active?.kind === "session" && !active.draft && active.sessionId
      ? `/s/${encodeURIComponent(active.sessionId)}`
      : "/chat";
  (window as Window & { __navigate?: (route: string) => void })
    .__navigate?.(path);
}

export function visibleWebTab() {
  const state = useCenterTabs.getState();
  const group = state.activeId
    ? findCenterTabGroup(state.groups, state.activeId)
    : undefined;
  if (group) {
    const visibleWebTabs = resolveCenterTabPanes(group, state.tabs, state.activeId)
      .flatMap((pane) => pane.kind === "tab" ? [pane.tabId] : [])
      .map((id) => state.tabs.find((tab) => tab.id === id))
      .filter((tab) => tab?.kind === "web" && isWebTabReady(tab.id));
    return visibleWebTabs.find((tab) => tab?.id === group.focusedId)
      ?? visibleWebTabs[0]
      ?? null;
  }
  const active = state.tabs.find((tab) => tab.id === state.activeId);
  if (active?.kind === "web" && isWebTabReady(active.id)) return active;
  return null;
}

export function restorePriorActiveTabAfterFailedWebOpen(
  priorActiveId: string | null,
  openedWebTabId: string | null,
): void {
  const state = useCenterTabs.getState();
  if (priorActiveId && openedWebTabId && state.activeId === openedWebTabId) {
    state.setActive(priorActiveId);
  }
}

function sendWebTabResult(
  ws: WebSocket,
  reqId: string,
  active: { id: string; url?: string },
  targetId: string | null,
): void {
  const activeUrl = active.url || (active.id.startsWith("w:") ? active.id.slice(2) : "");
  const ok = !!activeUrl && !!targetId;
  ws.send(JSON.stringify({
    action: "webtab_result",
    req_id: reqId,
    ok,
    ...(ok ? { url: activeUrl, tab_id: active.id, target_id: targetId } : {}),
    ...(!ok ? { error: "desktop web tab did not expose a CDP target" } : {}),
  }));
}

/**
 * Wire the desktop shell into the tabs store. Idempotent; no-op
 * outside the desktop shell. Currently called on web-pane mount
 * (web-tab-pane.tsx); app-shell should ALSO call it once at startup
 * so the app menu works before the first web tab ever opens.
 *
 *  - "op-desktop-new-tab" / "op-desktop-close-tab" CustomEvents
 *    (dispatched on window by preload for menu accelerators) →
 *    openNewTabPage / closeTab(activeId).
 *  - Store subscription that destroys native views the moment their
 *    tab closes — a closed tab's pane is already unmounted (or never
 *    was, for background tabs), so no component can do this cleanup.
 */
export function installDesktopMenuHandlers(): void {
  if (installed || typeof window === "undefined") return;
  const bridge = desktopBridge();
  if (!bridge) return;
  installed = true;
  window.addEventListener("op-desktop-new-tab", () => {
    useCenterTabs.getState().openNewTabPage();
    showCenterSurface();
  });
  window.addEventListener("op-desktop-close-tab", () => {
    const s = useCenterTabs.getState();
    if (s.activeId) s.closeTab(s.activeId);
    showActiveCenterTab();
  });
  // Agent 控制面：后端广播 webtab.command(op=open) → 在可见 UI 里开
  // web tab，并经同一条 WS 回执 webtab_result(req_id)。非桌面客户端不装
  // 本 handler（上面 bridge 为空即返回），该消息自然被忽略。
  window.addEventListener("op:ws-message", (e) => {
    const detail = (e as CustomEvent).detail as
      | { type?: string; data?: { op?: string; url?: string; req_id?: string } }
      | undefined;
    if (detail?.type !== "webtab.command") return;
    const d = detail.data;
    if (!d?.req_id || (d.op !== "open" && d.op !== "active")) return;
    const ws = (window as unknown as { ws?: WebSocket }).ws;
    if (ws?.readyState !== WebSocket.OPEN) return;

    if (d.op === "open") {
      if (!d.url) return;
      const state = useCenterTabs.getState();
      const active = state.tabs.find((tab) => tab.id === state.activeId);
      const priorActiveId = state.activeId;
      const split = active?.kind === "session" && isDesktopSplitLayoutAvailable();
      const routeVisible =
        window.location.pathname === "/chat" ||
        window.location.pathname.startsWith("/s/");
      let id: string | null;
      if (split) {
        id = state.openWebTabInSplit(d.url);
      } else {
        state.openWebTab(d.url);
        id = useCenterTabs.getState().activeId;
      }
      if (!split && !routeVisible) {
        const routed = showCenterSurface();
        if (!routed) {
          restorePriorActiveTabAfterFailedWebOpen(priorActiveId, id);
          ws.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ok: false,
            error: "center-tab navigation unavailable",
          }));
          return;
        }
      }
      void (async () => {
        const ready = !!id && await waitForWebTabReady(id, 2000);
        const tab = id
          ? useCenterTabs.getState().tabs.find((item) => item.id === id)
          : null;
        let targetId: string | null = null;
        if (ready && tab?.kind === "web") {
          try {
            targetId = await bridge.webTab.activate(tab.id, tab.url);
          } catch {
            targetId = null;
          }
        }
        if (!targetId && !split) {
          restorePriorActiveTabAfterFailedWebOpen(priorActiveId, id);
        }
        sendWebTabResult(
          ws,
          d.req_id!,
          tab?.kind === "web" ? tab : { id: id ?? "", url: d.url },
          targetId,
        );
      })();
      return;
    }

    const active = visibleWebTab();
    const routeVisible =
      window.location.pathname === "/chat" ||
      window.location.pathname.startsWith("/s/");
    if (!routeVisible || !active) {
      ws.send(JSON.stringify({
        action: "webtab_result",
        req_id: d.req_id,
        ok: false,
        error: "no visible active web tab",
      }));
      return;
    }
    void bridge.webTab
      .activate(active.id)
      .then((targetId) => sendWebTabResult(ws, d.req_id!, active, targetId))
      .catch(() => sendWebTabResult(ws, d.req_id!, active, null));
  });
  installTabTransferHandlers(bridge);
  // Fixed startup order (multiwindow plan Task 7): committed storage is
  // already hydrated by store init, listeners are installed above, then
  // journal recovery → terminal/orphan acks → ordinary native-view
  // reconciliation → claimPending for a detached window's pending token.
  void recoverPendingTabTransfers(bridge, () => {
    useCenterTabs.subscribe((s) => {
      destroyStaleWebViews(
        bridge,
        s.tabs.map((t) => t.id),
      );
    });
  });
}

// ------------------------------------------------------------- tab transfer
//
// Renderer side of the cross-window transfer transaction. Order is the
// contract (see the multiwindow plan's non-negotiable invariants):
//   destination: journal write+read-back → journalOpened → {persist:false}
//                mutations → destinationReady;
//   source:      journal → journalOpened → {persist:false} removal →
//                sourceRemoved; commit persists via finalizeTransferJournal;
//   undo:        forget bridge bookkeeping FIRST, then revert transient
//                store/session state, then acknowledge destinationUndone.

export interface AcceptedTransfer {
  token: string;
  insertedIds: string[];
  journal: TransferJournalEntry;
  inMemoryBridgeRecovery: SerializedWebViewBookkeeping;
}

export const acceptedTransfers = new Map<string, AcceptedTransfer>();

/** Native ids locked by an in-flight transfer transaction. */
const transferLockedIds = new Set<string>();

/** In-memory ready-waiter snapshots per source-side token (not journaled —
 *  a reloaded renderer recreates waiters through the ensure path). */
const sourceWaiterRecovery = new Map<
  string,
  Map<string, Set<(ready: boolean) => void> | undefined>
>();

function transferWebIds(payload: DesktopTransferPayload): string[] {
  return payload.tabs.flatMap((tab) => (tab.kind === "web" ? [tab.id] : []));
}

export function serializeWebViewBookkeeping(): SerializedWebViewBookkeeping {
  return {
    liveIds: [...liveViewIds],
    readyIds: [...readyWebTabIds],
    visibleBounds: Array.from(visibleWebBounds, ([id, bounds]) => ({
      id,
      bounds: { ...bounds },
    })),
  };
}

export function applyWebViewBookkeeping(
  snapshot: SerializedWebViewBookkeeping,
  bridge?: DesktopBridge | null,
): void {
  liveViewIds.clear();
  readyWebTabIds.clear();
  visibleWebBounds.clear();
  for (const id of snapshot.liveIds) liveViewIds.add(id);
  for (const id of snapshot.readyIds) readyWebTabIds.add(id);
  for (const { id, bounds } of snapshot.visibleBounds) {
    visibleWebBounds.set(id, { ...bounds });
  }
  if (bridge) scheduleVisibleWebBoundsFlush(bridge);
}

export function forgetTransferredWebView(
  bridge: DesktopBridge | null,
  id: string,
): void {
  liveViewIds.delete(id);
  readyWebTabIds.delete(id);
  if (visibleWebBounds.delete(id) && bridge) {
    scheduleVisibleWebBoundsFlush(bridge);
  }
}

function unlockTransfer(payload: DesktopTransferPayload): void {
  for (const id of transferWebIds(payload)) transferLockedIds.delete(id);
}

function destinationSessionAfter(
  before: SessionTransferSnapshot,
  payload: DesktopTransferPayload,
): SessionTransferSnapshot {
  const after = structuredClone(before);
  for (const chat of payload.chats) {
    if (chat.composerDraft !== undefined) {
      after.composerDrafts[chat.chatKey] = chat.composerDraft;
    }
    if (chat.composerSettings) {
      after.composerSettingsBySession[chat.chatKey] = chat.composerSettings;
    }
    if (chat.pendingProjectId) {
      after.pendingProjectsByChat[chat.chatKey] = chat.pendingProjectId;
    }
    if (chat.draftChannelChoice) {
      after.draftChannelChoices[chat.chatKey] = chat.draftChannelChoice;
    }
    if (chat.wasActive) {
      const tab = payload.tabs.find((item) => item.sessionId === chat.chatKey);
      after.activeChatKey = chat.chatKey;
      after.currentSessionId = tab?.draft ? null : chat.chatKey;
      after.composerInput = chat.activeComposerInput ?? chat.composerDraft ?? "";
      if (chat.activeComposerSettings) {
        after.composerSettings = chat.activeComposerSettings;
      }
    }
  }
  return after;
}

function sourceSessionAfter(
  before: SessionTransferSnapshot,
  payload: DesktopTransferPayload,
  afterCenter: ReturnType<typeof snapshotCenterTabsPayload>,
): SessionTransferSnapshot {
  const after = structuredClone(before);
  const moved = new Set<string>();
  for (const chat of payload.chats) {
    moved.add(chat.chatKey);
    delete after.composerDrafts[chat.chatKey];
    delete after.composerSettingsBySession[chat.chatKey];
    delete after.pendingProjectsByChat[chat.chatKey];
    delete after.draftChannelChoices[chat.chatKey];
  }
  if (before.activeChatKey && moved.has(before.activeChatKey)) {
    const active = afterCenter.tabs.find((tab) => tab.id === afterCenter.activeId);
    const key = active?.kind === "session" ? active.sessionId ?? null : null;
    after.activeChatKey = key;
    after.currentSessionId = active?.kind === "session" && !active.draft
      ? key
      : null;
    after.composerInput = (key && after.composerDrafts[key]) || "";
    if (key && after.composerSettingsBySession[key]) {
      after.composerSettings = after.composerSettingsBySession[key];
    }
  }
  return after;
}

export function transferRecoveryHandlers(
  bridge: DesktopBridge | null,
): TransferRecoveryHandlers {
  return {
    applyCenterTabs: (payload, options) =>
      replaceCenterTabsPayload(payload, options),
    applySession: (snapshot, options) => applySessionTransfer(snapshot, options),
    applyFileDrafts: applyFileDraftSnapshot,
    applyBridge: (snapshot) => applyWebViewBookkeeping(snapshot, bridge),
    rebuildAccepted: (entry) => {
      for (const id of transferWebIds(entry.payload)) transferLockedIds.add(id);
      acceptedTransfers.set(entry.token, {
        token: entry.token,
        insertedIds: entry.payload.tabs.map((tab) => tab.id),
        journal: entry,
        inMemoryBridgeRecovery: entry.beforeBridge,
      });
    },
    resumeSourceRemoved: (entry) => {
      const transfer = bridge?.tabTransfer;
      if (!transfer) return false;
      for (const id of transferWebIds(entry.payload)) transferLockedIds.add(id);
      void transfer
        .sourceRemoved(entry.token, true, entry.afterCenterTabs.tabs.length === 0)
        .then((ok) => {
          if (ok) return finalizeSourceCommit(bridge!, entry.token);
          restoreSourceBefore(bridge!, entry);
          return undefined;
        })
        .catch(() => restoreSourceBefore(bridge!, entry));
    },
    clearAccepted: (token) => {
      const accepted = acceptedTransfers.get(token);
      if (accepted) unlockTransfer(accepted.journal.payload);
      acceptedTransfers.delete(token);
      const journaled = readTransferJournal().entries[token];
      if (journaled) unlockTransfer(journaled.payload);
      sourceWaiterRecovery.delete(token);
    },
    deleteJournal: (token) => deleteTransferJournal(token),
    snapshotCenterTabs: snapshotCenterTabsPayload,
    snapshotSession: () => snapshotSessionTransfer([]),
  };
}

/** Fixed destination-undo order: bridge bookkeeping first, then the
 *  transient store/session/file-draft state, then accepted-map cleanup.
 *  The journal itself outlives this call — rolled-back deletes it. */
function undoDestinationLocal(
  bridge: DesktopBridge | null,
  entry: TransferJournalEntry,
): void {
  for (const id of transferWebIds(entry.payload)) {
    forgetTransferredWebView(bridge, id);
  }
  replaceCenterTabsPayload(
    rebaseCenterTabsPayload(snapshotCenterTabsPayload(), entry, "before"),
    { persist: false },
  );
  applySessionTransfer(
    rebaseSessionSnapshot(snapshotSessionTransfer([]), entry, "before"),
    { persist: false },
  );
  applyFileDraftSnapshot(entry.beforeFileDrafts);
  acceptedTransfers.delete(entry.token);
  unlockTransfer(entry.payload);
}

/** Same 25/50/25 drop intent, expressed as a main-process placement. */
export function placementForDropIntent(intent: TabDropIntent): TabDropPlacement {
  if (intent.mode === "merge") {
    const placement: TabDropPlacement = {
      kind: "merge",
      targetTabId: intent.targetTabId,
    };
    if (intent.groupId !== undefined) placement.groupId = intent.groupId;
    if (intent.memberIndex !== undefined) placement.memberIndex = intent.memberIndex;
    return placement;
  }
  return { kind: intent.mode, targetTabId: intent.targetTabId };
}

/** Pointer-down payload for tabTransfer.prepare — the dragged tabs plus
 *  every piece of chat/file draft state they own in this window. */
export function buildTransferPayload(
  subject: TabDragSubject,
  windowId: string,
): DesktopTransferPayload | null {
  const centerTabs = useCenterTabs.getState().tabs;
  const tabs = [];
  for (const tabId of subject.tabIds) {
    const tab = centerTabs.find((candidate) => candidate.id === tabId);
    if (!tab) return null;
    tabs.push(structuredClone(tab));
  }
  const session = useSessionStore.getState();
  const host = (typeof window === "undefined"
    ? {}
    : window) as unknown as DraftChannelChoiceHost;
  const chats: ChatTransferState[] = [];
  const payloadFileDrafts: DesktopTransferPayload["fileDrafts"] = [];
  for (const tab of tabs) {
    if (tab.kind === "session" && tab.sessionId) {
      const chatKey = tab.sessionId;
      const wasActive = session.activeChatKey === chatKey;
      const chat: ChatTransferState = { chatKey, wasActive };
      if (session.composerDrafts[chatKey] !== undefined) {
        chat.composerDraft = session.composerDrafts[chatKey];
      }
      if (session.composerSettingsBySession[chatKey]) {
        chat.composerSettings = structuredClone(
          session.composerSettingsBySession[chatKey],
        );
      }
      if (session.pendingProjectsByChat[chatKey]) {
        chat.pendingProjectId = session.pendingProjectsByChat[chatKey];
      }
      const choice = draftChannelChoiceFor(host, chatKey);
      if (choice) chat.draftChannelChoice = structuredClone(choice);
      if (wasActive) {
        chat.activeComposerInput = session.composerInput;
        chat.activeComposerSettings = structuredClone(session.composerSettings);
      }
      chats.push(chat);
    } else if (tab.kind === "file" && tab.projectId && tab.path) {
      const key = fileDraftKey(tab.projectId, tab.path);
      const value = fileDrafts.get(key);
      if (value) payloadFileDrafts.push({ key, value: structuredClone(value) });
    }
  }
  const source: DesktopTransferPayload["source"] = {
    windowId,
    kind: subject.kind,
  };
  if (subject.kind !== "tab") {
    source.groupId = subject.sourceGroup.id;
    source.memberIds = [...subject.sourceGroup.memberIds];
    source.visibleIds = [...subject.sourceGroup.visibleIds];
    if (subject.sourceGroup.focusedId !== undefined) {
      source.focusedId = subject.sourceGroup.focusedId;
    }
  }
  if (subject.kind === "segment") source.memberIndex = subject.memberIndex;
  return { tabs, source, fileDrafts: payloadFileDrafts, chats };
}

/** Destination staging. Returns true when destinationReady(true) was sent. */
export async function stageIncomingTransfer(
  bridge: DesktopBridge,
  token: string,
  placement: TabDropPlacement,
): Promise<boolean> {
  const transfer = bridge.tabTransfer;
  const inspected = await transfer.inspect(token);
  if (!inspected) return false;
  const validated = validateTransferredTabs(inspected.payload, placement);
  if (!validated.ok) {
    if (validated.reason === "duplicate" && validated.duplicateId) {
      useCenterTabs.getState().setActive(validated.duplicateId);
      await transfer.reject(token, "duplicate", validated.duplicateId);
    } else if (validated.reason === "group-full") {
      await transfer.reject(token, "group-full");
    }
    // "invalid" has no reject channel; the prepared token expires in main.
    return false;
  }
  const accepted = await transfer.accept(token, placement);
  if (!accepted) return false;
  const payload = accepted.payload;
  const webIds = transferWebIds(payload);
  for (const id of webIds) transferLockedIds.add(id);

  const beforeBridge = serializeWebViewBookkeeping();
  const beforeSession = snapshotSessionTransfer(
    payload.chats.map((chat) => chat.chatKey),
  );
  const afterSession = destinationSessionAfter(beforeSession, payload);
  const draftKeys = payload.fileDrafts.map((draft) => draft.key);
  const entry: TransferJournalEntry = {
    version: 1,
    token,
    role: "destination",
    phase: "staged",
    payload,
    placement: accepted.placement,
    beforeCenterTabs: snapshotCenterTabsPayload(),
    afterCenterTabs: validated.after,
    beforeSession,
    afterSession,
    beforeFileDrafts: snapshotFileDrafts(draftKeys),
    afterFileDrafts: payload.fileDrafts.map(({ key, value }) => ({
      key,
      existed: true,
      value,
    })),
    beforeBridge,
    afterBridge: {
      ...beforeBridge,
      liveIds: [...new Set([...beforeBridge.liveIds, ...webIds])],
    },
  };

  const fail = async () => {
    undoDestinationLocal(bridge, entry);
    unregisterPendingTransfer(token);
    deleteTransferJournal(token);
    await transfer.destinationReady(token, false);
    return false;
  };
  if (!writeTransferJournal(entry)) {
    unlockTransfer(payload);
    await transfer.destinationReady(token, false);
    return false;
  }
  registerPendingTransfer(entry);
  if (!(await transfer.journalOpened(token, "destination"))) return fail();
  try {
    const inserted = insertTransferredTabs(payload, accepted.placement, {
      persist: false,
    });
    if (!inserted.ok) return fail();
    if (!applySessionTransfer(afterSession, { persist: false })) return fail();
    applyFileDraftSnapshot(entry.afterFileDrafts);
    for (const id of webIds) liveViewIds.add(id);
  } catch {
    return fail();
  }
  acceptedTransfers.set(token, {
    token,
    insertedIds: payload.tabs.map((tab) => tab.id),
    journal: entry,
    inMemoryBridgeRecovery: beforeBridge,
  });
  return transfer.destinationReady(token, true);
}

function restoreSourceBefore(
  bridge: DesktopBridge,
  entry: TransferJournalEntry,
): void {
  replaceCenterTabsPayload(
    rebaseCenterTabsPayload(snapshotCenterTabsPayload(), entry, "before"),
    { persist: false },
  );
  applySessionTransfer(
    rebaseSessionSnapshot(snapshotSessionTransfer([]), entry, "before"),
    { persist: false },
  );
  applyFileDraftSnapshot(entry.beforeFileDrafts);
  applyWebViewBookkeeping(entry.beforeBridge, bridge);
  const waiters = sourceWaiterRecovery.get(entry.token);
  if (waiters) {
    for (const [id, set] of waiters) {
      if (set) webTabReadyWaiters.set(id, set);
    }
    sourceWaiterRecovery.delete(entry.token);
  }
  unlockTransfer(entry.payload);
  // The journal entry stays: main's rolled-back event finalizes it.
}

async function finalizeSourceCommit(
  bridge: DesktopBridge,
  token: string,
): Promise<void> {
  if (finalizeTransferJournal(token, "commit", transferRecoveryHandlers(bridge))) {
    await bridge.tabTransfer.journalFinalized(token, "source");
  }
}

/** Source side of remove-source. */
export async function handleRemoveSource(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const transfer = bridge.tabTransfer;
  const payload = detail.payload;
  const token = detail.token;
  if (!payload || !token) return;
  const ids = payload.tabs.map((tab) => tab.id);
  const webIds = transferWebIds(payload);
  for (const id of webIds) transferLockedIds.add(id);

  const beforeBridge = serializeWebViewBookkeeping();
  const beforeSession = snapshotSessionTransfer(
    payload.chats.map((chat) => chat.chatKey),
  );
  // Dry-run the removal to learn the post-removal payload for the journal,
  // then revert; the journal must be durable before the real mutation.
  const removal = removeTransferredTabs(ids, { persist: false });
  if (!removal.ok) {
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  replaceCenterTabsPayload(removal.before, { persist: false });
  const afterSession = sourceSessionAfter(beforeSession, payload, removal.after);
  const draftKeys = payload.fileDrafts.map((draft) => draft.key);
  const entry: TransferJournalEntry = {
    version: 1,
    token,
    role: "source",
    phase: "staged",
    payload,
    beforeCenterTabs: removal.before,
    afterCenterTabs: removal.after,
    beforeSession,
    afterSession,
    beforeFileDrafts: snapshotFileDrafts(draftKeys),
    afterFileDrafts: draftKeys.map((key) => ({ key, existed: false })),
    beforeBridge,
    afterBridge: {
      liveIds: beforeBridge.liveIds.filter((id) => !webIds.includes(id)),
      readyIds: beforeBridge.readyIds.filter((id) => !webIds.includes(id)),
      visibleBounds: beforeBridge.visibleBounds.filter(
        (item) => !webIds.includes(item.id),
      ),
    },
  };
  if (!writeTransferJournal(entry)) {
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  registerPendingTransfer(entry);
  if (!(await transfer.journalOpened(token, "source"))) {
    unregisterPendingTransfer(token);
    deleteTransferJournal(token);
    unlockTransfer(payload);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  sourceWaiterRecovery.set(
    token,
    new Map(webIds.map((id) => [id, webTabReadyWaiters.get(id)])),
  );
  let removed = false;
  try {
    removed = removeTransferredTabs(ids, { persist: false }).ok
      && applySessionTransfer(afterSession, { persist: false });
    if (removed) {
      applyFileDraftSnapshot(entry.afterFileDrafts);
      for (const id of webIds) forgetTransferredWebView(bridge, id);
    }
  } catch {
    removed = false;
  }
  if (!removed) {
    restoreSourceBefore(bridge, entry);
    await transfer.sourceRemoved(token, false, false);
    return;
  }
  const acknowledged = await transfer
    .sourceRemoved(token, true, removal.empty)
    .catch(() => false);
  if (!acknowledged) {
    // Stale/raced acknowledgement: restore locally right away.
    restoreSourceBefore(bridge, entry);
    return;
  }
  await finalizeSourceCommit(bridge, token);
}

/** Destination side of undo-destination. */
export async function handleUndoDestination(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const token = detail.token;
  const entry = acceptedTransfers.get(token)?.journal
    ?? readTransferJournal().entries[token];
  if (entry) undoDestinationLocal(bridge, entry);
  await bridge.tabTransfer.destinationUndone(token, true);
}

function transferRole(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): "source" | "destination" {
  return detail.sourceId === bridge.windowId ? "source" : "destination";
}

export async function handleTransferCommitted(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const handlers = transferRecoveryHandlers(bridge);
  const role = transferRole(bridge, detail);
  if (finalizeTransferJournal(detail.token, "commit", handlers)) {
    await bridge.tabTransfer.journalFinalized(detail.token, role);
  }
  handlers.clearAccepted?.(detail.token);
}

export async function handleTransferRolledBack(
  bridge: DesktopBridge,
  detail: DesktopTransferReceipt,
): Promise<void> {
  const handlers = transferRecoveryHandlers(bridge);
  const role = transferRole(bridge, detail);
  const finalized = finalizeTransferJournal(detail.token, "rollback", handlers);
  handlers.clearAccepted?.(detail.token);
  // The acknowledgement is mandatory for both roles — a source whose
  // journal never existed (pre-journal rollback) still acknowledges;
  // main ignores roles it never recorded.
  if (finalized || role === "source") {
    await bridge.tabTransfer.journalFinalized(detail.token, role);
  }
}

export function handleTransferRejected(detail: DesktopTransferReceipt): void {
  const prepared = dragCoordinator.current();
  if (prepared?.transferToken === detail.token) dragCoordinator.cancel();
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("op-tab-transfer-rejected", { detail }),
    );
  }
}

let transferHandlersInstalled = false;

/** Wire the transfer event channels. Idempotent per renderer. Returns a
 *  cleanup that cancels a still-prepared drag token and unsubscribes; it
 *  intentionally leaves unresolved journal entries for startup recovery. */
export function installTabTransferHandlers(bridge: DesktopBridge): () => void {
  const transfer = bridge.tabTransfer;
  if (!transfer || transferHandlersInstalled) return () => {};
  transferHandlersInstalled = true;
  const subscriptions = [
    transfer.onRemoveSource((detail) => {
      void handleRemoveSource(bridge, detail);
    }),
    transfer.onUndoDestination((detail) => {
      void handleUndoDestination(bridge, detail);
    }),
    transfer.onCommitted((detail) => {
      void handleTransferCommitted(bridge, detail);
    }),
    transfer.onRolledBack((detail) => {
      void handleTransferRolledBack(bridge, detail);
    }),
    transfer.onRejected((detail) => handleTransferRejected(detail)),
    // Pointer-driven cross-window drop: the source window delivered a
    // prepared token here; stage it at the end of this window's strip.
    transfer.onStageIncoming?.((detail) => {
      void stageIncomingTransfer(bridge, detail.token, { kind: "strip-end" });
    }) ?? (() => {}),
  ];
  return () => {
    transferHandlersInstalled = false;
    const prepared = dragCoordinator.current();
    if (prepared?.transferToken && !prepared.committed) {
      void transfer.cancel(prepared.transferToken);
      dragCoordinator.cancel();
    }
    for (const unsubscribe of subscriptions) unsubscribe();
  };
}

// --------------------------------------------------------- startup recovery

/** Finalize a destroyed participant's keyed journal on its behalf: apply the
 *  durable decision's outcome directly to the owner window's normal storage
 *  keys, then delete that keyed journal. Returns false when nothing durable
 *  could be written (the ack must then wait for the next recovery pass). */
export function finalizeOrphanTransferJournal(
  token: string,
  status: "committed" | "rolled-back",
  ownerWindowId: string,
): boolean {
  const entry = readTransferJournal(ownerWindowId).entries[token];
  if (!entry) return true; // journal already cleaned — ack is idempotent
  const target = status === "committed" ? "after" : "before";
  const center = target === "after" ? entry.afterCenterTabs : entry.beforeCenterTabs;
  const session = target === "after" ? entry.afterSession : entry.beforeSession;
  try {
    localStorage.setItem(`centerTabs:${ownerWindowId}`, JSON.stringify(center));
    localStorage.setItem(
      sessionDraftStorageKey(ownerWindowId),
      JSON.stringify({
        version: 1,
        composerDrafts: session.composerDrafts,
        composerSettingsBySession: session.composerSettingsBySession,
        pendingProjectsByChat: session.pendingProjectsByChat,
        draftChannelChoices: session.draftChannelChoices,
      }),
    );
  } catch {
    return false;
  }
  return deleteTransferJournal(token, ownerWindowId);
}

/** Renderer startup recovery, in the plan's fixed order: resolve every
 *  journal entry against main's token status, acknowledge terminal/orphan
 *  decisions, reconcile ordinary native views, then pull a detached
 *  window's pending token. */
export async function recoverPendingTabTransfers(
  bridge: DesktopBridge,
  reconcile?: () => void,
): Promise<void> {
  const transfer = bridge.tabTransfer;
  if (!transfer) {
    reconcile?.();
    return;
  }
  const handlers = transferRecoveryHandlers(bridge);
  const journaledTokens = new Set(Object.keys(readTransferJournal().entries));
  for (const token of journaledTokens) {
    const entry = readTransferJournal().entries[token];
    if (!entry) continue;
    let status: TransferMainStatus = "stale";
    try {
      const receipt = await transfer.status(token);
      if (receipt) status = receipt.status as TransferMainStatus;
    } catch {
      /* unreachable main — resolve as stale */
    }
    recoverTransferJournalEntry(entry, status, handlers);
  }
  try {
    for (const item of await transfer.pendingTerminal(bridge.windowId)) {
      if (item.orphaned) {
        if (!finalizeOrphanTransferJournal(item.token, item.status, item.windowId)) {
          continue;
        }
      } else if (readTransferJournal().entries[item.token]) {
        // Own journal still live (pre-commit): its normal commit/rollback
        // handler performs the mandatory journalFinalized ack.
        continue;
      }
      // Own role with a cleared journal: journals are deleted only after
      // their outcome persisted, so committed storage already matches the
      // decision and the ack is idempotent.
      await transfer.journalFinalized(item.token, item.role, item.windowId);
    }
  } catch {
    /* decision store unreadable — re-query on the next recovery pass */
  }
  reconcile?.();
  try {
    const token = await transfer.claimPending(bridge.windowId);
    if (token && !journaledTokens.has(token)) {
      await stageIncomingTransfer(bridge, token, { kind: "strip-end", consumePlaceholder: true });
    }
    // A token already represented by a recovered journal entry resumes
    // through that entry (idempotent) — staging again would double-insert.
  } catch {
    /* main's expiry/rollback closes the hidden window */
  }
}
