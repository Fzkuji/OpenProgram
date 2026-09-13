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
import "@/lib/net/ws-events";
import { ingestBrowserResource, type BackendResource } from "@/lib/chat/session-resources";
import { recordOperationCue, showActionsEnabled } from "@/lib/browser/browser-control";
import { hasNavigate, navigate } from "@/lib/navigate";
import type { DesktopWebTabApi } from "@/lib/desktop/desktop-bridge-types";
import { type DesktopBridge } from "./bridge-api";
import { nativeCueOperations, webTabGeometryRevisions } from "./bridge-state";



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
    const opener = state.tabs.find((tab) => tab.id === popup.openerId);
    state.openPopupWebTab(popup.url, popup.openerId);
    if (!opener?.agentOpened) showCenterSurface();
  }) ?? (() => {});
}

/** Kept for older bridge consumers; page interaction never controls execution. */
export function subscribeBrowserHumanInput(_bridge: DesktopBridge): () => void {
  return () => {};
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
    ? { x: point.x, y: point.y, width: point.width, height: point.height, sequence: row.sequence || 0, generation: row.generation || 0, resourceId: row.resourceId }
    : null;
  if (marker && operation) {
    const key = `${row.resourceId}:${row.generation}:${operation.id}`;
    if (nativeCueOperations.get(row.tabId) === key) return;
    nativeCueOperations.set(row.tabId, key);
  }
  void bridge.webTab.showAction(row.tabId, marker).catch(() => {});
}
