// Desktop bridge state responsibilities.
import "@/lib/net/ws/ws-events";
import type { DesktopWebTabBounds, DesktopWebTabCorners } from "@/lib/desktop/desktop-bridge-types";
import { type DesktopBridge } from "./bridge-api";
import { CANVAS_FULLSCREEN_EVENT, canvasCoversWindow } from "@/lib/tabs/canvas/canvas-fullscreen";



/** Native views created by THIS renderer. Main-side views orphaned by
 *  a full renderer reload are main's cleanup problem — we can't
 *  enumerate them from here. */
export const liveViewIds = new Set<string>();
export const destroyInFlight = new Set<string>();
export const readyWebTabIds = new Set<string>();
export const webTabReadyWaiters = new Map<string, Set<(ready: boolean) => void>>();
export const visibleWebBounds = new Map<string, DesktopWebTabBounds>();
export const visibleWebCorners = new Map<string, DesktopWebTabCorners>();
export const webTabGeometryRevisions = new Map<string, number>();
export const nativeCueOperations = new Map<string, string>();
export let nextWebTabGeometryRevision = 1;
export let visibleWebFlushScheduled = false;
export let visibleWebFlushBridge: DesktopBridge | null = null;
export let desktopSplitLayoutAvailable = false;

/** The bridge of the last flush, so leaving fullscreen can flush again. */
let lastFlushBridge: DesktopBridge | null = null;
/** Views shown in the floating preview, which stays in the session pane's full screen. */
const floatingWebTabIds = new Set<string>();

export function setFloatingWebTab(id: string, floating: boolean): void {
  if (floating) floatingWebTabIds.add(id);
  else floatingWebTabIds.delete(id);
}

// Native web views sit above every page element. While an element (a
// video, say) is fullscreen, no native view may show. While the session
// pane's full screen covers the window (lib/tabs/canvas/canvas-fullscreen.ts)
// only the floating preview's page shows: it floats over that pane, while
// every other web pane is covered. Leaving either brings them back.
if (typeof document !== "undefined") {
  document.addEventListener("fullscreenchange", () => {
    if (lastFlushBridge) scheduleVisibleWebBoundsFlush(lastFlushBridge);
  });
}
if (typeof window !== "undefined") {
  window.addEventListener(CANVAS_FULLSCREEN_EVENT, () => {
    if (lastFlushBridge) scheduleVisibleWebBoundsFlush(lastFlushBridge);
  });
}

/** Sends every visible native view's bounds to the main process in a
 *  microtask, while the frame that measured them is still painting. The
 *  page is drawn by the browser process's compositor, which needs about a
 *  millisecond after the bounds arrive to deliver its frame, and the display
 *  draws as soon as the renderer's own frame arrives, about half a
 *  millisecond after the commit: bounds sent any later than this miss that
 *  draw and leave the page a refresh behind its frame. */
export function scheduleVisibleWebBoundsFlush(bridge: DesktopBridge): void {
  visibleWebFlushBridge = bridge;
  lastFlushBridge = bridge;
  if (visibleWebFlushScheduled) return;
  visibleWebFlushScheduled = true;
  queueMicrotask(() => {
    visibleWebFlushScheduled = false;
    const targetBridge = visibleWebFlushBridge;
    visibleWebFlushBridge = null;
    if (!targetBridge) return;
    const covered = typeof document !== "undefined" && !!document.fullscreenElement;
    const floatingOnly = typeof document !== "undefined" && canvasCoversWindow();
    targetBridge.webTab.syncVisible(covered ? [] :
      Array.from(visibleWebBounds)
        .filter(([id]) => !floatingOnly || floatingWebTabIds.has(id))
        .map(([id, bounds]) => ({
          id,
          bounds: { ...bounds },
          corners: visibleWebCorners.get(id) ?? null,
        })),
    );
  });
}

export function invalidateWebTabGeometry(bridge: DesktopBridge | null | undefined, id: string): void {
  const geometryRevision = nextWebTabGeometryRevision++;
  webTabGeometryRevisions.set(id, geometryRevision);
  if (bridge && typeof window !== "undefined") window.dispatchEvent(new CustomEvent("op:browser-geometry", {
    detail: { windowId: bridge.windowId, tabId: id, geometryRevision },
  }));
}

export function registerVisibleWebTabBounds(
  bridge: DesktopBridge,
  id: string,
  bounds: DesktopWebTabBounds,
  corners: DesktopWebTabCorners | null = null,
): void {
  const previous = visibleWebBounds.get(id);
  const sameCorners = JSON.stringify(visibleWebCorners.get(id) ?? null) === JSON.stringify(corners);
  if (previous && previous.x === bounds.x && previous.y === bounds.y
      && previous.width === bounds.width && previous.height === bounds.height) {
    if (sameCorners) return;
  } else {
    invalidateWebTabGeometry(bridge, id);
  }
  visibleWebBounds.set(id, { ...bounds });
  if (corners) visibleWebCorners.set(id, { ...corners });
  else visibleWebCorners.delete(id);
  scheduleVisibleWebBoundsFlush(bridge);
}

export function removeVisibleWebTabBounds(
  bridge: DesktopBridge,
  id: string,
): void {
  visibleWebCorners.delete(id);
  if (visibleWebBounds.delete(id)) {
    invalidateWebTabGeometry(bridge, id);
    scheduleVisibleWebBoundsFlush(bridge);
  }
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

/** Native closes confirmed while the socket was down; sent after the next register. */
export const unreportedClosedWebTabs = new Set<string>();

/** Native ids locked by an in-flight transfer transaction. */
export const transferLockedIds = new Set<string>();
