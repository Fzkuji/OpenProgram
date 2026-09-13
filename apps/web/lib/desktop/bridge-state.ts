// Desktop bridge state responsibilities.
import "@/lib/net/ws-events";
import type { DesktopWebTabBounds } from "@/lib/desktop/desktop-bridge-types";
import { type DesktopBridge } from "./bridge-api";



/** Native views created by THIS renderer. Main-side views orphaned by
 *  a full renderer reload are main's cleanup problem — we can't
 *  enumerate them from here. */
export const liveViewIds = new Set<string>();
export const destroyInFlight = new Set<string>();
export const readyWebTabIds = new Set<string>();
export const webTabReadyWaiters = new Map<string, Set<(ready: boolean) => void>>();
export const visibleWebBounds = new Map<string, DesktopWebTabBounds>();
export const webTabGeometryRevisions = new Map<string, number>();
export const nativeCueOperations = new Map<string, string>();
export let nextWebTabGeometryRevision = 1;
export let visibleWebFlushScheduled = false;
export let visibleWebFlushBridge: DesktopBridge | null = null;
export let desktopSplitLayoutAvailable = false;

export function scheduleVisibleWebBoundsFlush(bridge: DesktopBridge): void {
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
): void {
  const previous = visibleWebBounds.get(id);
  if (!previous || previous.x !== bounds.x || previous.y !== bounds.y
      || previous.width !== bounds.width || previous.height !== bounds.height) {
    invalidateWebTabGeometry(bridge, id);
  }
  visibleWebBounds.set(id, { ...bounds });
  scheduleVisibleWebBoundsFlush(bridge);
}

export function removeVisibleWebTabBounds(
  bridge: DesktopBridge,
  id: string,
): void {
  if (visibleWebBounds.delete(id)) {
    invalidateWebTabGeometry(bridge, id);
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

/** Native ids locked by an in-flight transfer transaction. */
export const transferLockedIds = new Set<string>();
