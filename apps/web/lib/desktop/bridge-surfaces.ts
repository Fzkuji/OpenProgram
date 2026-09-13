// Desktop bridge surfaces responsibilities.
import { agentCanAccessWebTab } from "../browser/web-page-management";
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
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { peekLiveWebTabPipId, peekWebTabPipId, peekWebTabPipOwnerId } from "@/lib/browser/web-tab-pip-store";
import { centerTabStripEntries, findCenterTabGroup, resolveCenterTabPanes } from "@/lib/tabs/center-tab-groups";
import "@/lib/net/ws-events";
import type { DesktopWebTabApi } from "@/lib/desktop/desktop-bridge-types";
import { isWebTabReady, visibleWebBounds, webTabGeometryRevisions } from "./bridge-state";
import { desktopBridge } from "./bridge-api";



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
  const pipId = peekLiveWebTabPipId(state);
  if (pipId && isWebTabReady(pipId)) {
    return state.tabs.find((tab) => tab.id === pipId && tab.kind === "web") ?? null;
  }
  return null;
}

export function isWebTabActuallyVisible(tabId: string): boolean {
  return isWebTabReady(tabId) && visibleWebBounds.has(tabId);
}

export function visibleWebTabById(tabId: string) {
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
  const pipId = peekLiveWebTabPipId(state);
  if (pipId) visibleIds.add(pipId);
  return visibleIds.has(tabId) && isWebTabActuallyVisible(tabId)
    ? state.tabs.find((tab) => tab.id === tabId && tab.kind === "web") ?? null
    : null;
}

/** A selected image mirror refers to an existing Page without claiming native visibility. */
export function selectedMirrorTabById(tabId: string) {
  const state = useCenterTabs.getState();
  if (peekLiveWebTabPipId(state) !== tabId) return null;
  return state.tabs.find((tab) => tab.id === tabId && tab.kind === "web") ?? null;
}

export function finalizeWebTabPreview(
  tabId: string,
  expectedGeometryRevision: number,
  result: Record<string, unknown> | null,
  allowBackground = false,
): Record<string, unknown> {
  const tab = allowBackground ? selectedMirrorTabById(tabId) : visibleWebTabById(tabId);
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

export function finalizeBoundWebTabScreenshot(
  tabId: string,
  startedGeometryRevision: number,
  imageDataUrl: string | null,
): Record<string, unknown> {
  const tab = useCenterTabs.getState().tabs.find(
    (item) => item.id === tabId && item.kind === "web",
  );
  const geometryRevision = webTabGeometryRevisions.get(tabId) ?? 0;
  if (!tab || geometryRevision !== startedGeometryRevision) {
    return {
      ok: false,
      error: "web tab changed during capture",
      reason_code: "page_context_stale",
      geometry_revision: geometryRevision,
    };
  }
  if (!imageDataUrl) {
    return { ok: false, error: "desktop web tab capture failed" };
  }
  return {
    ok: true,
    window_id: desktopBridge()?.windowId,
    tab_id: tab.id,
    geometry_revision: geometryRevision,
    image_data_url: imageDataUrl,
  };
}

export interface TurnSurfaceRef {
  version: 1;
  /** Explicitly selected image mirror of an existing background Page. */
  background?: boolean;
  window_id: string;
  tab_id: string;
  region: "left" | "right" | "center";
  access: "enabled" | "disabled";
  focused: boolean;
  title: string;
  url: string;
  geometry_revision: number;
}

export interface TurnWindowRef {
  version: 1;
  window_id: string;
  access: "enabled" | "disabled";
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

export const browserInventoryRevisions = new Map<
  string,
  { fingerprint: string; revision: number }
>();

export function browserInventoryRevision(windowId: string, fingerprint: string): number {
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
  sessionId?: string | null,
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
      visible: visibleWebTabById(tab.id) !== null,
      geometryRevision: webTabGeometryRevisions.get(tab.id) ?? 0,
    }]));
  const layoutFingerprint = JSON.stringify({
    tabs: tabs.map((tab) => [tab.id, tab.kind, tab.agentSessionId, tab.webPinned]),
    sessionId,
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
    .filter((tab) => agentCanAccessWebTab(tab.id, sessionId, { tabs, groups }))
    .map(async (tab): Promise<BrowserPageInventoryItem | null> => {
      const nativePage = await bridge.webTab.inspect!(tab.id);
      if (!nativePage || !agentCanAccessWebTab(tab.id, sessionId, useCenterTabs.getState())) return null;
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
    (page): page is BrowserPageInventoryItem => page !== null
      && agentCanAccessWebTab(page.tab_id, sessionId, useCenterTabs.getState()),
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
  const chat = state.tabs.find(tab => tab.id === state.activeId
    && tab.kind === "session" && tab.sessionId === sessionId) ?? state.tabs.find(
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
    if (pipId && peekWebTabPipOwnerId() === chat.id) {
      web = state.tabs.find((tab) => tab.id === pipId && tab.kind === "web");
    }
  }
  if (!web || web.kind !== "web") return null;
  const mirror = selectedMirrorTabById(web.id);
  const scopedWeb = visibleWebTabById(web.id) ?? mirror;
  if (!scopedWeb) return null;
  web = scopedWeb;
  const region = !group
    ? "right"
    : group.visibleIds.length === 1
      ? "center"
      : group.visibleIds.indexOf(web.id) === 0 ? "left" : "right";
  return {
    version: 1,
    window_id: bridge.windowId,
    tab_id: web.id,
    ...(mirror && !isWebTabActuallyVisible(web.id) ? { background: true } : {}),
    region,
    access: toolsEnabled ? "enabled" : "disabled",
    focused: group?.focusedId === web.id,
    title: web.title,
    url: web.url || "",
    geometry_revision: webTabGeometryRevisions.get(web.id) ?? 0,
  };
}

/** The exact Page when present, otherwise only the chat's desktop window. */
export function surfaceOriginForChat(
  sessionId: string | null,
  toolsEnabled: boolean,
): TurnSurfaceRef | TurnWindowRef | null {
  const surface = surfaceRefForChat(sessionId, toolsEnabled);
  if (surface) return surface;
  const bridge = desktopBridge();
  if (!bridge || !sessionId) return null;
  const chat = useCenterTabs.getState().tabs.find(
    (tab) => tab.kind === "session" && tab.sessionId === sessionId,
  );
  if (!chat) return null;
  return {
    version: 1,
    window_id: bridge.windowId,
    access: toolsEnabled ? "enabled" : "disabled",
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

export function closeAgentWebTabResult(
  tabId: string | undefined,
  tabs: readonly { id: string; kind: string }[],
  groups: readonly { memberIds: readonly string[] }[],
): { ok: true } | { ok: false; reason: "tab_not_found" | "tab_in_user_layout" } {
  if (!tabId || !tabs.some((tab) => tab.id === tabId && tab.kind === "web")) {
    return { ok: false, reason: "tab_not_found" };
  }
  if (groups.some((group) => group.memberIds.includes(tabId))) {
    return { ok: false, reason: "tab_in_user_layout" };
  }
  return { ok: true };
}

export function sendWebTabResult(
  ws: Pick<WebSocket, "send">,
  reqId: string,
  active: { id: string; url?: string },
  targetId: string | null,
  ownership?: { created: boolean; reused: boolean },
  failure?: { error: string; reason_code?: string },
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
    ...ownership,
    ...(!ok ? {
      error: failure?.error || "desktop web tab did not expose a CDP target",
      ...(failure?.reason_code ? { reason_code: failure.reason_code } : {}),
    } : {}),
  }));
}
