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
  peekWebTabPipId,
  pipCoversCenter,
  useWebTabPip,
} from "@/lib/state/web-tab-pip-store";
import {
  centerTabStripEntries,
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
  transferJournalStorageKey,
  writeTransferJournal,
} from "@/lib/tab-transfer-journal";
import "@/lib/net/ws-events";
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
  draftChannelChoiceHost,
} from "@/lib/runtime-bridge/draft-channel-choice";
import { getSocket } from "@/lib/runtime-bridge/state";
import { hasNavigate, navigate } from "@/lib/navigate";
import type {
  DesktopBrowserDataApi,
  DesktopBrowserImportApi,
  DesktopDownloadsApi,
  DesktopHistoryApi,
  DesktopMainMenuApi,
  DesktopTerminalApi,
  DesktopThemeApi,
  DesktopUpdateApi,
  DesktopWebTabApi,
  DesktopWebTabBounds,
} from "@/lib/desktop-bridge-types";
import type {
  DesktopTabTransferApi,
  DesktopTransferReceipt,
} from "@/lib/desktop-transfer-types";
export type {
  DesktopBrowserDataApi,
  DesktopBrowserImportApi,
  DesktopBrowserImportBookmark,
  DesktopBrowserImportProfile,
  DesktopBrowserImportResult,
  DesktopBrowserImportSource,
  DesktopContextMenuItem,
  DesktopDownloadEntry,
  DesktopDownloadsApi,
  DesktopHistoryApi,
  DesktopHistoryEntry,
  DesktopMainMenuApi,
  DesktopTerminalApi,
  DesktopThemeApi,
  DesktopUpdateApi,
  DesktopUpdateRelease,
  DesktopUpdateState,
  DesktopVisibleWebView,
  DesktopWebTabApi,
  DesktopWebTabBounds,
  DesktopWebTabFindResult,
  DesktopWebTabState,
} from "@/lib/desktop-bridge-types";
export type {
  DesktopTabTransferApi,
  DesktopTransferReceipt,
} from "@/lib/desktop-transfer-types";

export interface DesktopBridge {
  readonly isDesktop: true;
  readonly windowId: string;
  /** Absolute native path for a user-selected/dropped File. */
  getPathForFile?(file: File): string;
  /** shell.openExternal — http/https only. */
  openExternal(url: string): void;
  /** Close this window (last tab closed → close window). */
  closeWindow?(): void;
  /** Move this window by a pixel delta (single-tab drag = move window). */
  moveWindowBy?(dx: number, dy: number): void;
  webTab: DesktopWebTabApi;
  tabTransfer: DesktopTabTransferApi;
  /** Top-layer ⋮ menu overlay. Absent in shells older than this build. */
  mainMenu?: DesktopMainMenuApi;
  /** Absent in shells older than the browsing-history build. */
  history?: DesktopHistoryApi;
  /** Desktop-only download history and active download controls. */
  downloads?: DesktopDownloadsApi;
  updates: DesktopUpdateApi;
  /** Desktop-only, explicit import from a detected local browser profile. */
  browserImport?: DesktopBrowserImportApi;
  /** Desktop-only clearing of the built-in browser profile. */
  browserData?: DesktopBrowserDataApi;
  /** Desktop-only local PTY. Never exposed by the Web server. */
  terminal?: DesktopTerminalApi;
  /** Keep the native window background aligned with the resolved web theme. */
  theme?: DesktopThemeApi;
}

/** The preload-exposed bridge, or null outside the desktop shell. */
export function desktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  return window.openprogramDesktop ?? null;
}

/** Native views created by THIS renderer. Main-side views orphaned by
 *  a full renderer reload are main's cleanup problem — we can't
 *  enumerate them from here. */
const liveViewIds = new Set<string>();
const readyWebTabIds = new Set<string>();
const webTabReadyWaiters = new Map<string, Set<(ready: boolean) => void>>();
const visibleWebBounds = new Map<string, DesktopWebTabBounds>();
const webTabGeometryRevisions = new Map<string, number>();
let nextWebTabGeometryRevision = 1;
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
  const previous = visibleWebBounds.get(id);
  if (!previous || previous.x !== bounds.x || previous.y !== bounds.y
      || previous.width !== bounds.width || previous.height !== bounds.height) {
    webTabGeometryRevisions.set(id, nextWebTabGeometryRevision++);
  }
  visibleWebBounds.set(id, { ...bounds });
  scheduleVisibleWebBoundsFlush(bridge);
}

export function removeVisibleWebTabBounds(
  bridge: DesktopBridge,
  id: string,
): void {
  if (visibleWebBounds.delete(id)) {
    webTabGeometryRevisions.set(id, nextWebTabGeometryRevision++);
  }
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
      webTabGeometryRevisions.delete(id);
    }
  }
}

export function desktopTerminalId(
  bridge: Pick<DesktopBridge, "windowId">,
  preset: "shell" | "claude",
): string {
  return `terminal:${bridge.windowId}:${preset}`;
}

/** Terminal processes follow tab lifetime, not component lifetime. Switching
 *  tabs unmounts inactive panes, so stopping from TerminalPage cleanup would
 *  terminate a still-open terminal. Store reconciliation stops only the two
 *  fixed presets whose built-in tab has actually been closed. */
export function destroyStaleTerminals(
  bridge: DesktopBridge,
  tabs: ReadonlyArray<{ kind: string; page?: string }>,
): void {
  if (!bridge.terminal) return;
  for (const preset of ["shell", "claude"] as const) {
    const page = preset === "shell" ? "terminal" : "claude";
    const alive = tabs.some((tab) => tab.kind === "builtin" && tab.page === page);
    if (!alive) bridge.terminal.stop(desktopTerminalId(bridge, preset));
  }
}

let installed = false;

function showCenterSurface(): boolean {
  if (!hasNavigate()) return false;
  navigate("/chat");
  return true;
}

export function subscribeWebTabPopups(
  bridge: { webTab: Pick<DesktopWebTabApi, "onPopup"> },
): () => void {
  return bridge.webTab.onPopup?.((popup) => {
    if (
      !popup
      || typeof popup.openerId !== "string"
      || typeof popup.url !== "string"
    ) return;
    const state = useCenterTabs.getState();
    if (!state.tabs.some((tab) => tab.id === popup.openerId && tab.kind === "web")) {
      return;
    }
    state.openPopupWebTab(popup.url, popup.openerId);
    showCenterSurface();
  }) ?? (() => {});
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
    const fromGroup = visibleWebTabs.find((tab) => tab?.id === group.focusedId)
      ?? visibleWebTabs[0]
      ?? null;
    if (fromGroup) return fromGroup;
  }
  const active = state.tabs.find((tab) => tab.id === state.activeId);
  if (active?.kind === "web" && isWebTabReady(active.id)) return active;
  const pipId = peekWebTabPipId();
  if (pipId && pipCoversCenter(pipId, state) && isWebTabReady(pipId)) {
    return state.tabs.find((tab) => tab.id === pipId && tab.kind === "web") ?? null;
  }
  return null;
}

function isWebTabActuallyVisible(tabId: string): boolean {
  return isWebTabReady(tabId) && visibleWebBounds.has(tabId);
}

function visibleWebTabById(tabId: string) {
  const state = useCenterTabs.getState();
  const group = state.activeId
    ? findCenterTabGroup(state.groups, state.activeId)
    : undefined;
  const visibleIds = new Set(
    resolveCenterTabPanes(group, state.tabs, state.activeId)
      .flatMap((pane) => pane.kind === "tab" ? [pane.tabId] : []),
  );
  if (state.activeId && state.splitWebTabId) {
    visibleIds.add(state.splitWebTabId);
  }
  const pipId = peekWebTabPipId();
  if (pipId && pipCoversCenter(pipId, state)) visibleIds.add(pipId);
  return visibleIds.has(tabId) && isWebTabActuallyVisible(tabId)
    ? state.tabs.find((tab) => tab.id === tabId && tab.kind === "web") ?? null
    : null;
}

export function finalizeWebTabPreview(
  tabId: string,
  expectedGeometryRevision: number,
  result: Record<string, unknown> | null,
): Record<string, unknown> {
  const tab = visibleWebTabById(tabId);
  const geometryRevision = webTabGeometryRevisions.get(tabId) ?? 0;
  if (!tab || (
    expectedGeometryRevision > 0
    && geometryRevision !== expectedGeometryRevision
  )) {
    return {
      ok: false,
      error: "web tab geometry changed during preview",
      reason_code: "page_context_stale",
      geometry_revision: geometryRevision,
    };
  }
  if (!result) {
    return { ok: false, error: "desktop web tab preview is unavailable" };
  }
  return {
    ok: true,
    ...result,
    window_id: desktopBridge()?.windowId,
    geometry_revision: geometryRevision,
  };
}

export function finalizeBoundWebTabActivation(
  tabId: string,
  expectedGeometryRevision: number,
  targetId: string | null,
): Record<string, unknown> {
  const tab = visibleWebTabById(tabId);
  const geometryRevision = webTabGeometryRevisions.get(tabId) ?? 0;
  if (!tab || (
    expectedGeometryRevision > 0
    && geometryRevision !== expectedGeometryRevision
  )) {
    return {
      ok: false,
      error: "web tab geometry changed during activation",
      reason_code: "page_context_stale",
      geometry_revision: geometryRevision,
    };
  }
  const url = tab.url || (tab.id.startsWith("w:") ? tab.id.slice(2) : "");
  if (!url || !targetId) {
    return { ok: false, error: "desktop web tab did not expose a CDP target" };
  }
  return {
    ok: true,
    window_id: desktopBridge()?.windowId,
    url,
    tab_id: tab.id,
    target_id: targetId,
    geometry_revision: geometryRevision,
  };
}

export interface TurnSurfaceRef {
  version: 1;
  window_id: string;
  tab_id: string;
  region: "left" | "right" | "center";
  access: "enabled" | "disabled";
  focused: boolean;
  title: string;
  url: string;
  geometry_revision: number;
}

export interface BrowserPageInventoryItem {
  tab_id: string;
  target_id: string;
  url: string;
  title: string;
  focused: boolean;
  visible: boolean;
  region: "left" | "right" | "center" | "background";
  geometry_revision: number;
  tab_entry_id: string;
  placement:
    | { mode: "single" }
    | { mode: "split"; pane_id?: string; order?: number };
  opener_tab_id?: string;
}

export interface BrowserPageInventoryTabEntry {
  id: string;
  mode: "single" | "split";
  tab_ids: string[];
  split?: {
    axis: "horizontal";
    ratio: number;
    panes: Array<{ pane_id: string; order: number; tab_id: string }>;
  };
}

export interface BrowserPageInventorySnapshot {
  window_id: string;
  inventory_revision: number;
  active_tab_entry_id: string;
  focused_tab_id: string;
  tab_entries: BrowserPageInventoryTabEntry[];
  pages: BrowserPageInventoryItem[];
}

const browserInventoryRevisions = new Map<
  string,
  { fingerprint: string; revision: number }
>();

function browserInventoryRevision(windowId: string, fingerprint: string): number {
  const prior = browserInventoryRevisions.get(windowId);
  if (prior?.fingerprint === fingerprint) return prior.revision;
  const revision = (prior?.revision ?? 0) + 1;
  browserInventoryRevisions.set(windowId, { fingerprint, revision });
  return revision;
}

export async function browserPageInventory(
  bridge: {
    windowId?: string;
    webTab: Pick<DesktopWebTabApi, "inspect">;
  },
): Promise<BrowserPageInventorySnapshot> {
  const windowId = bridge.windowId ?? desktopBridge()?.windowId ?? "";
  const empty = (): BrowserPageInventorySnapshot => ({
    window_id: windowId,
    inventory_revision: 0,
    active_tab_entry_id: "",
    focused_tab_id: "",
    tab_entries: [],
    pages: [],
  });
  if (!bridge.webTab.inspect) return empty();
  const state = useCenterTabs.getState();
  const tabs = state.tabs.map((tab) => ({ ...tab }));
  const groups = state.groups.map((group) => ({
    ...group,
    memberIds: [...group.memberIds],
    visibleIds: [...group.visibleIds],
  }));
  const activeGroup = state.activeId
    ? findCenterTabGroup(groups, state.activeId)
    : undefined;
  const focusedTabId = activeGroup?.focusedId ?? state.activeId ?? "";
  const splitRatio = state.splitRatio;
  const webState = new Map(tabs
    .filter((tab) => tab.kind === "web")
    .map((tab) => [tab.id, {
      visible: isWebTabActuallyVisible(tab.id),
      geometryRevision: webTabGeometryRevisions.get(tab.id) ?? 0,
    }]));
  const layoutFingerprint = JSON.stringify({
    tabs: tabs.map((tab) => [tab.id, tab.kind]),
    groups: groups.map((group) => [
      group.id, group.memberIds, group.visibleIds, group.focusedId,
    ]),
    activeId: state.activeId,
    splitRatio,
    geometry: Array.from(webState, ([tabId, current]) => [
      tabId, current.visible, current.geometryRevision,
    ]),
  });
  const inventoryRevision = browserInventoryRevision(windowId, layoutFingerprint);
  const pages = await Promise.all(tabs
    .filter((tab) => tab.kind === "web")
    .map(async (tab): Promise<BrowserPageInventoryItem | null> => {
      const nativePage = await bridge.webTab.inspect!(tab.id);
      if (!nativePage) return null;
      const group = findCenterTabGroup(groups, tab.id);
      const current = webState.get(tab.id)!;
      const visible = current.visible;
      const focused = visible && focusedTabId === tab.id;
      const paneOrder = group?.visibleIds.indexOf(tab.id) ?? -1;
      const tabEntryId = group ? `group:${group.id}` : `tab:${tab.id}`;
      const region = !visible
        ? "background" as const
        : !group || group.visibleIds.length === 1
          ? "center" as const
          : group.visibleIds.indexOf(tab.id) === 0
            ? "left" as const
            : "right" as const;
      return {
        tab_id: tab.id,
        target_id: nativePage.target_id,
        url: nativePage.url,
        title: nativePage.title,
        focused,
        visible,
        region,
        geometry_revision: current.geometryRevision,
        tab_entry_id: tabEntryId,
        placement: group && paneOrder >= 0
          ? {
              mode: "split" as const,
              pane_id: `pane:${group.id}:${paneOrder}`,
              order: paneOrder,
            }
          : group ? { mode: "split" as const } : { mode: "single" as const },
        ...(tab.openerTabId ? { opener_tab_id: tab.openerTabId } : {}),
      };
    }));
  const validPages = pages.filter(
    (page): page is BrowserPageInventoryItem => page !== null,
  );
  const validTabIds = new Set(validPages.map((page) => page.tab_id));
  const tabEntries = centerTabStripEntries({
    tabIds: tabs.map((tab) => tab.id),
    groups,
  }).flatMap<BrowserPageInventoryTabEntry>((entry) => {
    if (entry.kind === "tab") {
      return validTabIds.has(entry.tabId) ? [{
        id: entry.id,
        mode: "single",
        tab_ids: [entry.tabId],
      }] : [];
    }
    const visibleTabIds = entry.group.visibleIds.filter(
      (id) => validTabIds.has(id),
    );
    const tabIds = [
      ...visibleTabIds,
      ...entry.group.memberIds.filter(
        (id) => validTabIds.has(id) && !visibleTabIds.includes(id),
      ),
    ];
    if (tabIds.length === 0) return [];
    return [{
      id: entry.id,
      mode: "split",
      tab_ids: tabIds,
      split: {
        axis: "horizontal",
        ratio: splitRatio,
        panes: visibleTabIds.map((tabId, order) => {
          return {
            pane_id: `pane:${entry.group.id}:${order}`,
            order,
            tab_id: tabId,
          };
        }),
      },
    }];
  });
  const activeTabEntryId = activeGroup
    ? `group:${activeGroup.id}`
    : state.activeId ? `tab:${state.activeId}` : "";
  return {
    window_id: windowId,
    inventory_revision: inventoryRevision,
    active_tab_entry_id: tabEntries.some((entry) => entry.id === activeTabEntryId)
      ? activeTabEntryId
      : "",
    focused_tab_id: validTabIds.has(focusedTabId) ? focusedTabId : "",
    tab_entries: tabEntries,
    pages: validPages,
  };
}

/** The visible web pane paired with this chat at send time. */
export function surfaceRefForChat(
  sessionId: string | null,
  toolsEnabled: boolean,
): TurnSurfaceRef | null {
  const bridge = desktopBridge();
  if (!bridge || !sessionId) return null;
  const state = useCenterTabs.getState();
  const chat = state.tabs.find(
    (tab) => tab.kind === "session" && tab.sessionId === sessionId,
  );
  if (!chat) return null;
  const group = findCenterTabGroup(state.groups, chat.id);
  let web = group?.visibleIds
    .map((id) => state.tabs.find((tab) => tab.id === id))
    .find((tab) => tab?.kind === "web");
  if (!web && state.activeId === chat.id && state.splitWebTabId) {
    web = state.tabs.find(
      (tab) => tab.id === state.splitWebTabId && tab.kind === "web",
    );
  }
  if (!web && state.activeId === chat.id) {
    const pipId = peekWebTabPipId();
    if (pipId) {
      web = state.tabs.find((tab) => tab.id === pipId && tab.kind === "web");
    }
  }
  if (!web || web.kind !== "web") return null;
  if (!isWebTabActuallyVisible(web.id)) return null;
  const region = !group
    ? "right"
    : group.visibleIds.length === 1
      ? "center"
      : group.visibleIds.indexOf(web.id) === 0 ? "left" : "right";
  return {
    version: 1,
    window_id: bridge.windowId,
    tab_id: web.id,
    region,
    access: toolsEnabled ? "enabled" : "disabled",
    focused: group?.focusedId === web.id,
    title: web.title,
    url: web.url || "",
    geometry_revision: webTabGeometryRevisions.get(web.id) ?? 0,
  };
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
    ...(ok ? {
      window_id: desktopBridge()?.windowId,
      url: activeUrl,
      tab_id: active.id,
      target_id: targetId,
      geometry_revision: webTabGeometryRevisions.get(active.id) ?? 0,
    } : {}),
    ...(!ok ? { error: "desktop web tab did not expose a CDP target" } : {}),
  }));
}

/**
 * Wire the desktop shell into the tabs store. Idempotent; no-op
 * outside the desktop shell. Currently called on web-pane mount
 * (web-tab-pane.tsx); app-shell should ALSO call it once at startup
 * so the app menu works before the first web tab ever opens.
 *
 *  - "op-desktop-new-tab" CustomEvent (dispatched on window by preload for
 *    menu accelerators) → openNewTabPage. The strip lifecycle owns the close
 *    event because it must close a composite as one dirty-checked entry.
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
  subscribeWebTabPopups(bridge);
  // Agent 控制面：后端广播 webtab.command(op=open) → 在可见 UI 里开
  // web tab，并经同一条 WS 回执 webtab_result(req_id)。非桌面客户端不装
  // 本 handler（上面 bridge 为空即返回），该消息自然被忽略。
  window.addEventListener("op:ws-message", (e) => {
    const detail = e.detail;
    if (detail?.type !== "webtab.command") return;
    const d = detail.data as
      | { op?: string; url?: string; window_id?: string; tab_id?: string; req_id?: string; expected_geometry_revision?: number }
      | undefined;
    if (!d?.req_id || !["open", "active", "activate", "preview", "list", "resolve"].includes(d.op || "")) return;
    const ws = getSocket();
    if (ws?.readyState !== WebSocket.OPEN) return;

    if (d.op === "list") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        ws.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ok: false }));
        return;
      }
      void browserPageInventory(bridge).then((inventory) => {
        ws.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: true,
          ...inventory,
        }));
      });
      return;
    }

    if (d.op === "resolve") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        ws.send(JSON.stringify({ action: "webtab_result", req_id: d.req_id, ok: false }));
        return;
      }
      const tab = d.tab_id
        ? useCenterTabs.getState().tabs.find((item) => item.id === d.tab_id && item.kind === "web")
        : null;
      void (tab && bridge.webTab.resolve
        ? bridge.webTab.resolve(tab.id)
        : Promise.resolve(null)
      ).then((targetId) => sendWebTabResult(
        ws,
        d.req_id!,
        tab?.kind === "web" ? tab : { id: d.tab_id ?? "", url: "" },
        targetId,
      ));
      return;
    }

    if (d.op === "preview" || d.op === "activate") {
      if (d.window_id && d.window_id !== bridge.windowId) {
        ws.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab belongs to another window",
        }));
        return;
      }
      const tab = d.tab_id ? visibleWebTabById(d.tab_id) : null;
      if (!tab || tab.kind !== "web") {
        ws.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "requested web tab is not visible in this window",
        }));
        return;
      }
      const geometryRevision = webTabGeometryRevisions.get(tab.id) ?? 0;
      if (d.expected_geometry_revision
          && d.expected_geometry_revision !== geometryRevision) {
        ws.send(JSON.stringify({
          action: "webtab_result",
          req_id: d.req_id,
          ok: false,
          error: "web tab geometry changed",
          reason_code: "page_context_stale",
          geometry_revision: geometryRevision,
        }));
        return;
      }
      if (d.op === "preview") {
        void bridge.webTab.preview(tab.id).then((result) => {
          ws.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ...finalizeWebTabPreview(
              tab.id,
              d.expected_geometry_revision ?? 0,
              result,
            ),
          }));
        });
      } else {
        void bridge.webTab.activate(tab.id, d.url, true).then((targetId) => {
          ws.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ...finalizeBoundWebTabActivation(
              tab.id,
              d.expected_geometry_revision ?? 0,
              targetId,
            ),
          }));
        }).catch(() => {
          ws.send(JSON.stringify({
            action: "webtab_result",
            req_id: d.req_id,
            ok: false,
            error: "desktop web tab did not expose a CDP target",
          }));
        });
      }
      return;
    }

    if (d.op === "open") {
      if (!d.url) return;
      const state = useCenterTabs.getState();
      const active = state.tabs.find((tab) => tab.id === state.activeId);
      const priorActiveId = state.activeId;
      const activeGroup = active
        ? findCenterTabGroup(state.groups, active.id)
        : undefined;
      const activeGroupHasWeb = activeGroup?.memberIds.some((memberId) =>
        state.tabs.some((tab) => tab.id === memberId && tab.kind === "web"),
      ) ?? false;
      const split = active?.kind === "session"
        && isDesktopSplitLayoutAvailable()
        && activeGroupHasWeb;
      const usePip = active?.kind === "session" && !split;
      const routeVisible =
        window.location.pathname === "/chat" ||
        window.location.pathname.startsWith("/s/");
      let id: string | null;
      if (split) {
        id = state.openWebTabInSplit(d.url);
      } else if (usePip) {
        id = state.ensureWebTab(d.url);
        useWebTabPip.getState().show(id, active.id);
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
    const reconcileNativeResources = () => {
      const tabs = useCenterTabs.getState().tabs;
      destroyStaleWebViews(
        bridge,
        tabs.map((t) => t.id),
      );
      destroyStaleTerminals(bridge, tabs);
    };
    useCenterTabs.subscribe(reconcileNativeResources);
    reconcileNativeResources();
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
  webTabGeometryRevisions.clear();
  for (const id of snapshot.liveIds) liveViewIds.add(id);
  for (const id of snapshot.readyIds) readyWebTabIds.add(id);
  for (const { id, bounds } of snapshot.visibleBounds) {
    visibleWebBounds.set(id, { ...bounds });
    webTabGeometryRevisions.set(id, nextWebTabGeometryRevision++);
  }
  if (bridge) scheduleVisibleWebBoundsFlush(bridge);
}

export function forgetTransferredWebView(
  bridge: DesktopBridge | null,
  id: string,
): void {
  liveViewIds.delete(id);
  readyWebTabIds.delete(id);
  webTabGeometryRevisions.delete(id);
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
      // The focused chat's live draft and settings are no longer separate
      // fields — they ARE `composerDrafts[activeChatKey]` /
      // `composerSettingsBySession[activeChatKey]`, already written above.
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
    // Nothing else to move: the newly focused chat's draft and settings are
    // whatever its entries in the keyed maps already say.
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
  const host = draftChannelChoiceHost;
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
  let cleaned = true;
  if (detail.discardWindowState) {
    const keys = [
      `centerTabs:${bridge.windowId}`,
      sessionDraftStorageKey(bridge.windowId),
      transferJournalStorageKey(bridge.windowId),
    ];
    try {
      for (const key of keys) localStorage.removeItem(key);
      cleaned = keys.every((key) => localStorage.getItem(key) === null);
    } catch {
      cleaned = false;
    }
  }
  await bridge.tabTransfer.destinationUndone(token, cleaned);
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

export async function handleFinalizeOrphaned(
  bridge: DesktopBridge,
  detail: {
    token: string;
    status: "committed" | "rolled-back";
    role: "source" | "destination";
    windowId: string;
    orphaned: boolean;
    discardWindowState?: boolean;
  },
): Promise<void> {
  if (!detail.orphaned) return;
  if (!finalizeOrphanTransferJournal(
    detail.token,
    detail.status,
    detail.windowId,
    detail.discardWindowState,
  )) return;
  await bridge.tabTransfer.journalFinalized(
    detail.token,
    detail.role,
    detail.windowId,
  );
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
    transfer.onFinalizeOrphaned((detail) =>
      handleFinalizeOrphaned(bridge, detail)),
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
  discardWindowState = false,
): boolean {
  if (status === "rolled-back" && discardWindowState) {
    const keys = [
      `centerTabs:${ownerWindowId}`,
      sessionDraftStorageKey(ownerWindowId),
      transferJournalStorageKey(ownerWindowId),
    ];
    try {
      for (const key of keys) localStorage.removeItem(key);
      return keys.every((key) => localStorage.getItem(key) === null);
    } catch {
      return false;
    }
  }
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
        if (!finalizeOrphanTransferJournal(
          item.token,
          item.status,
          item.windowId,
          item.discardWindowState,
        )) {
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
