// Desktop bridge events responsibilities.
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
import "@/lib/net/ws/ws-events";
import { ingestBrowserResource, listedBrowserResources, useBrowserResourceStore, type BackendResource } from "@/lib/chat/resources/session-resources";
import {
  agentHoldsPage,
  controlResourceFromSession,
  presentedControlState,
  recordOperationCue,
  showActionsEnabled,
  useBrowserControlStore,
} from "@/lib/browser/control/browser-control";
import { hasNavigate, navigate } from "@/lib/nav/navigate";
import type { DesktopWebTabApi } from "@/lib/desktop/desktop-bridge-types";
import { getSocket } from "@/lib/runtime-bridge/state";
import { type DesktopBridge } from "./bridge-api";
import { ensureWebView, nativeCueOperations, webTabGeometryRevisions } from "./bridge-state";



export function showCenterSurface(): boolean {
  if (
    typeof window !== "undefined"
    && (window.location.pathname === "/chat"
      || window.location.pathname.startsWith("/s/"))
  ) return true;
  if (!hasNavigate()) return false;
  navigate("/chat");
  return true;
}

export function subscribeWebTabPopups(
  bridge: Pick<DesktopBridge, "windowId"> & {
    webTab: Pick<DesktopWebTabApi, "onPopup"> & Partial<DesktopWebTabApi>;
  },
): () => void {
  return bridge.webTab.onPopup?.((popup) => {
    if (
      !popup
      || typeof popup.openerId !== "string"
      || typeof popup.url !== "string"
    ) return;
    const state = useCenterTabs.getState();
    const nativeId = typeof popup.id === "string" && popup.id ? popup.id : undefined;
    if (!state.tabs.some((tab) => tab.id === popup.openerId && tab.kind === "web")) {
      // Nothing will show the Page the desktop already made for it.
      if (nativeId) bridge.webTab.destroy?.(nativeId);
      return;
    }
    const opener = state.tabs.find((tab) => tab.id === popup.openerId);
    // A popup from the user's own page that an Agent is operating came from
    // the Agent's step: it opens beside the page and the user stays where they are.
    const held = !opener?.agentOpened && agentHoldsTab(bridge, popup.openerId);
    const id = state.openPopupWebTab(popup.url, popup.openerId, nativeId, held);
    if (held) return;
    if (!opener?.agentOpened) {
      showCenterSurface();
      return;
    }
    adoptAgentPopup(bridge, id, popup.openerId, popup.url);
  }) ?? (() => {});
}

/**
 * An Agent's popup is the next step of its task. Load it now, in the
 * background, and hand it to the worker so it joins the conversation's
 * Resources and the Agent's page list. Left unloaded, the Agent could not
 * see it and started the same flow again in its own page.
 */
function adoptAgentPopup(
  bridge: Parameters<typeof subscribeWebTabPopups>[0],
  id: string,
  openerId: string,
  url: string,
): void {
  if (!bridge.webTab.ensure || !bridge.webTab.resolve) return;
  ensureWebView(bridge as DesktopBridge, id, url);
  void Promise.resolve()
    .then(() => bridge.webTab.resolve?.(id) ?? null)
    .then((targetId) => {
      const ws = getSocket();
      if (!targetId || ws?.readyState !== WebSocket.OPEN) return;
      const tab = useCenterTabs.getState().tabs.find((item) => item.id === id);
      if (tab?.kind !== "web") return;
      ws.send(JSON.stringify({
        action: "webtab_popup", window_id: bridge.windowId, tab_id: id,
        opener_tab_id: openerId, target_id: targetId,
        url: tab.url || url, title: tab.title || "",
      }));
    })
    .catch(() => {});
}

/** The pages of this window an Agent holds now. */
function agentHeldTabs(bridge: Pick<DesktopBridge, "windowId">): Set<string> {
  const held = new Set<string>();
  for (const row of listedBrowserResources()) {
    if (!row.tabId || (row.windowId && row.windowId !== bridge.windowId)) continue;
    const control = controlResourceFromSession(row);
    if (control && agentHoldsPage(presentedControlState(control))) held.add(row.tabId);
  }
  return held;
}

function agentHoldsTab(bridge: Pick<DesktopBridge, "windowId">, tabId: string): boolean {
  return agentHeldTabs(bridge).has(tabId);
}

/** Tell the shell which of this window's pages the Agent holds, so their
 *  edge glows like a desktop window the Agent operates. */
export function subscribeAgentPageGlow(bridge: DesktopBridge): () => void {
  const setGlow = bridge.webTab.setControlGlow;
  if (!setGlow) return () => {};
  const held = new Set<string>();
  const sync = () => {
    const next = agentHeldTabs(bridge);
    for (const id of held) if (!next.has(id)) setGlow(id, false);
    for (const id of next) if (!held.has(id)) setGlow(id, true);
    held.clear();
    for (const id of next) held.add(id);
  };
  const stopResources = useBrowserResourceStore.subscribe(sync);
  const stopControl = useBrowserControlStore.subscribe(sync);
  sync();
  return () => { stopResources(); stopControl(); };
}

export function receiveBrowserResource(bridge: DesktopBridge, data: Record<string, unknown> | undefined) {
  if (!data || typeof data.session_id !== "string") return;
  const scope = typeof data.conversation_session_id === "string" ? data.conversation_session_id : data.session_id;
  const row = ingestBrowserResource(data as BackendResource, scope);
  if (!row?.resourceId || !row.tabId || row.windowId !== bridge.windowId
      || row.generation !== data.generation || row.sequence !== data.sequence) return;
  const operation = row.lastOperation;
  if (operation) recordOperationCue({ resourceId: row.resourceId,
    generation: row.generation || 0, operation, controlState: row.controlState,
    geometryRevision: webTabGeometryRevisions.get(row.tabId) ?? 0 });
  if (!bridge.webTab.showAction) return;
  const point = operation?.point;
  const fresh = row.controlState === "active"
    && (operation?.phase === "dispatched" || operation?.phase === "acknowledged")
    && operation.geometry_revision === (webTabGeometryRevisions.get(row.tabId) ?? 0)
    && !!operation.frame_id && showActionsEnabled();
  const marker = fresh && point && typeof point.width === "number" && typeof point.height === "number"
    ? { x: point.x, y: point.y, width: point.width, height: point.height, sequence: row.sequence || 0, generation: row.generation || 0, resourceId: row.resourceId, action: operation?.action }
    : null;
  if (marker && operation) {
    const key = `${row.resourceId}:${row.generation}:${operation.id}`;
    if (nativeCueOperations.get(row.tabId) === key) return;
    nativeCueOperations.set(row.tabId, key);
  }
  void bridge.webTab.showAction(row.tabId, marker).catch(() => {});
}
