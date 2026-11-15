// Desktop bridge restoration responsibilities.
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
import { getSocket } from "@/lib/runtime-bridge/state";
import { isPlaceholderPageTitle, listedBrowserResources, recoverSessionResources } from "@/lib/chat/resources/session-resources";
import { type DesktopBridge } from "./bridge-api";
import { destroyInFlight, ensureWebView, liveViewIds, nativeCueOperations, removeVisibleWebTabBounds, transferLockedIds, unreportedClosedWebTabs, webTabGeometryRevisions } from "./bridge-state";



/** Scheme+host+path used to compare a display-only SQLite target with a full URL. */
export function displayPathOf(url: string): string {
  const value = (url || "").trim();
  if (!value) return "";
  try {
    const parsed = new URL(value);
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}`;
  } catch {
    return value;
  }
}

/**
 * Native live URL always wins. When the view is gone, keep the persisted
 * full URL (query/hash) if it is the same document as the display-only
 * resource target; only replace it when SQLite names a different document.
 */
export function reconcileRestoredWebUrl(
  nativeUrl: string | null | undefined,
  resourceTarget: string | null | undefined,
  persistedUrl: string | null | undefined,
  nativeConfirmed = false,
): string {
  const native = (nativeUrl || "").trim();
  if (native) return native;
  const persisted = (persistedUrl || "").trim();
  const stored = (resourceTarget || "").trim();
  if (nativeConfirmed && persisted) return persisted;
  if (persisted && stored) {
    const persistedPath = displayPathOf(persisted);
    const storedPath = displayPathOf(stored);
    if (persistedPath === storedPath || persistedPath === stored) return persisted;
    return stored;
  }
  return persisted || stored;
}

export function listedRowsForTab(tabId: string) {
  return listedBrowserResources().filter((row) => row.tabId === tabId && row.kind === "web");
}

export function tabIsExplicitlyClosed(tabId: string): boolean {
  const rows = listedRowsForTab(tabId);
  return rows.length > 0 && rows.every((row) => row.status === "closed");
}

export function restorableResourceForTab(tabId: string) {
  if (tabIsExplicitlyClosed(tabId)) return undefined;
  return listedRowsForTab(tabId).find((row) => row.status !== "closed");
}

export function webTabStillPresent(tabId: string) {
  return useCenterTabs.getState().tabs.some((tab) => tab.id === tabId && tab.kind === "web");
}

export async function restoreRetainedWebViews(
  bridge: DesktopBridge,
  opts: { tabIds?: readonly string[]; retryFailed?: boolean; canAccess?: (tabId: string) => boolean } = {},
): Promise<void> {
  const current = useCenterTabs.getState().tabs.filter((tab) => tab.kind === "web");
  const candidateIds = (opts.tabIds ?? current.map((tab) => tab.id)).filter((id, index, all) => (
    all.indexOf(id) === index
  ));
  const sessionIds = [...new Set(current.flatMap((tab) => (
    candidateIds.includes(tab.id) && tab.agentSessionId ? [tab.agentSessionId] : []
  )))];
  // One read at a time: each is costly on the server, and a burst of them
  // fills the browser's connections so the page's own requests (a video's
  // first frame) wait behind them.
  if (listedBrowserResources().length === 0) {
    for (const sessionId of sessionIds) {
      await recoverSessionResources(sessionId).catch(() => undefined);
    }
  }
  for (const tabId of candidateIds) {
    if (opts.canAccess && !opts.canAccess(tabId)) continue;
    if (!webTabStillPresent(tabId)) continue;
    if (tabIsExplicitlyClosed(tabId)) continue;
    let nativeUrl = "";
    let nativeTitle = "";
    try {
      const native = await bridge.webTab.inspect?.(tabId);
      nativeUrl = native?.url || "";
      nativeTitle = native?.title || "";
    } catch {
      nativeUrl = "";
    }
    if (!webTabStillPresent(tabId)) continue;
    if (opts.canAccess && !opts.canAccess(tabId)) continue;
    if (tabIsExplicitlyClosed(tabId)) continue;
    const tab = useCenterTabs.getState().tabs.find((item) => item.id === tabId && item.kind === "web");
    if (!tab) continue;
    const resource = restorableResourceForTab(tabId);
    const url = reconcileRestoredWebUrl(
      nativeUrl,
      resource?.target,
      tab.url,
      Number(tab.urlNativeAt) > 0,
    );
    if (!url) continue;
    const alreadyLive = liveViewIds.has(tabId) && !!nativeUrl;
    if (opts.retryFailed && liveViewIds.has(tabId) && resource?.status === "restore_failed") {
      bridge.webTab.navigate(tabId, url);
    } else if (!alreadyLive) {
      ensureWebView(bridge, tabId, url);
    }
    if (!webTabStillPresent(tabId)) continue;
    if (nativeUrl) {
      const title = nativeTitle && (!isPlaceholderPageTitle(nativeTitle) || isPlaceholderPageTitle(tab.title))
        ? nativeTitle : "";
      if (nativeUrl !== tab.url || (title && title !== tab.title)) {
        useCenterTabs.getState().updateWebTab(tabId, {
          url: nativeUrl,
          urlNativeAt: Date.now(),
          ...(title ? { title } : {}),
        });
      }
      continue;
    }
    if (url !== tab.url) {
      useCenterTabs.getState().updateWebTab(tabId, {
        url,
        ...(resource?.title ? { title: resource.title } : {}),
      });
    }
  }
}

export function persistNativeWebTabState(state: {
  id?: string;
  url?: string;
  title?: string;
  faviconUrl?: string;
}): void {
  if (!state.id) return;
  const patch: { url?: string; title?: string; faviconUrl?: string; urlNativeAt?: number } = {};
  if (state.url) {
    patch.url = state.url;
    patch.urlNativeAt = Date.now();
  }
  if (state.title) {
    // A recreated view reports its URL as the title until the document
    // loads; that must not replace the real title the tab already has.
    const current = useCenterTabs.getState().tabs.find((tab) => tab.id === state.id)?.title;
    if (!isPlaceholderPageTitle(state.title) || isPlaceholderPageTitle(current)) patch.title = state.title;
  }
  if (state.faviconUrl !== undefined) patch.faviconUrl = state.faviconUrl;
  if (Object.keys(patch).length === 0) return;
  useCenterTabs.getState().updateWebTab(state.id, patch);
}

export async function retryRestoreWebTab(
  bridge: DesktopBridge,
  tabId: string,
  canAccess?: (tabId: string) => boolean,
): Promise<void> {
  const tab = useCenterTabs.getState().tabs.find((item) => item.id === tabId && item.kind === "web");
  if (!tab || (canAccess && !canAccess(tabId))) return;
  const listed = listedBrowserResources().filter((row) => row.tabId === tabId && row.kind === "web");
  if (listed.length > 0 && listed.every((row) => row.status === "closed")) return;
  const resource = listed.find((row) => row.status !== "closed");
  const sessionId = tab.agentSessionId || resource?.conversationSessionId || resource?.sessionId;
  if (sessionId) {
    await recoverSessionResources(sessionId).catch(() => undefined);
  }
  if (!webTabStillPresent(tabId) || tabIsExplicitlyClosed(tabId) || (canAccess && !canAccess(tabId))) return;
  await restoreRetainedWebViews(bridge, { tabIds: [tabId], retryFailed: true, canAccess });
  reregisterDesktopWindow(bridge);
}

export function reregisterDesktopWindow(bridge: DesktopBridge): void {
  const ws = getSocket();
  if (ws?.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    action: "webtab_register",
    window_id: bridge.windowId,
  }));
  // The server only accepts a close from the registered socket, so closes
  // confirmed while it was down go out right after the register.
  for (const id of Array.from(unreportedClosedWebTabs)) {
    unreportedClosedWebTabs.delete(id);
    if (webTabStillPresent(id)) continue;
    ws.send(JSON.stringify({ action: "webtab_closed", window_id: bridge.windowId, tab_id: id }));
  }
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
      if (destroyInFlight.has(id)) continue;
      destroyInFlight.add(id);
      const finish = (ok: boolean) => {
        destroyInFlight.delete(id);
        if (!ok || transferLockedIds.has(id) || webTabStillPresent(id)) return;
        removeVisibleWebTabBounds(bridge, id);
        const ws = getSocket();
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: "webtab_closed", window_id: bridge.windowId, tab_id: id }));
        } else {
          unreportedClosedWebTabs.add(id);
        }
        liveViewIds.delete(id);
        webTabGeometryRevisions.delete(id);
        nativeCueOperations.delete(id);
      };
      if (typeof bridge.webTab.destroyConfirmed === "function") {
        void bridge.webTab.destroyConfirmed(id).then(finish).catch(() => finish(false));
      } else {
        try {
          bridge.webTab.destroy(id);
          // Older preloads cannot confirm native closure. Retire this local
          // view registration once; resource snapshots remain authoritative.
          removeVisibleWebTabBounds(bridge, id);
          liveViewIds.delete(id);
          webTabGeometryRevisions.delete(id);
          nativeCueOperations.delete(id);
        } finally {
          destroyInFlight.delete(id);
        }
      }
    }
  }
}
