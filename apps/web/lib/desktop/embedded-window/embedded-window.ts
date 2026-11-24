"use client";

/** Another application's window inside a tab of the desktop App.
 *
 *  The worker does the platform work (computer_use/desktop/embedding):
 *  on Windows and Linux the window becomes a child of the App window and the
 *  tab only reports where it should sit; on macOS the tab shows a live
 *  capture of the window, which the worker keeps exactly behind the tab, and
 *  sends what the user does in the tab back to it. */

import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import { onChanged } from "@/lib/net/ws/changes";
import { getSocket } from "@/lib/runtime-bridge/state";
import { wsRequest } from "@/lib/net/ws/ws-request";

export type EmbedMode = "stream" | "native";

export interface EmbeddableWindow {
  id: number;
  pid: number;
  app: string;
  title: string;
  /** PNG data URL of the application's icon; empty where the platform gives none. */
  icon: string;
  bounds: [number, number, number, number];
}

export interface EmbeddedWindowState {
  id: number;
  open: boolean;
  mode?: EmbedMode;
  pid?: number;
  app?: string;
  title?: string;
  /** The window's frame on screen: always on macOS, and in view mode everywhere. */
  frame?: [number, number, number, number] | null;
  /** Whether the page forwards pointer and keys to the window in this mode. */
  input?: boolean;
  /** The Worker follows the window by the operating system's events and says
   *  when it changed (`embedded_windows`); absent where it cannot. */
  follows?: boolean;
}

/** How a view holds the window. A tab places it (behind the tab on macOS,
 *  inside it elsewhere); a small preview only watches it, leaving the window
 *  where it is, and shows its live picture. Several views may hold one
 *  window; each names itself with a holder id. */
export interface Holding { holder: string; place: boolean }

/** What the App window tells the page about itself (main/windows/embedded-windows.js). */
export interface EmbedHost {
  platform: string;
  pid: number;
  handle: string;
  zoom: number;
  scale: number;
  content: { x: number; y: number; width: number; height: number };
}

export interface PanelRect { left: number; top: number; width: number; height: number }

/** The tab's rectangle as the worker places the window: screen points on
 *  macOS (the window goes behind the tab), device pixels of the App window's
 *  client area elsewhere (the window is its child). */
export function hostRect(host: EmbedHost, rect: PanelRect): [number, number, number, number] {
  const zoom = host.zoom || 1;
  if (host.platform === "darwin") {
    return [host.content.x + rect.left * zoom, host.content.y + rect.top * zoom, rect.width * zoom, rect.height * zoom]
      .map(Math.round) as [number, number, number, number];
  }
  const factor = zoom * (host.scale || 1);
  return [rect.left * factor, rect.top * factor, rect.width * factor, rect.height * factor]
    .map(Math.round) as [number, number, number, number];
}

/** A point in the tab (CSS px from its top-left) in window points: the
 *  capture is drawn at the window's own size from the tab's top-left, so the
 *  only conversion is the page zoom. */
export function windowPoint(host: EmbedHost, x: number, y: number): { x: number; y: number } {
  const zoom = host.zoom || 1;
  return { x: Math.round(x * zoom * 10) / 10, y: Math.round(y * zoom * 10) / 10 };
}

/** The modifier names the worker maps to key flags. */
export function heldModifiers(event: { shiftKey: boolean; ctrlKey: boolean; altKey: boolean; metaKey: boolean }): string[] {
  const held: string[] = [];
  if (event.shiftKey) held.push("shift");
  if (event.ctrlKey) held.push("ctrl");
  if (event.altKey) held.push("alt");
  if (event.metaKey) held.push("meta");
  return held;
}

/** Wheel deltas in pixels, whatever unit the event used. */
export function wheelPixels(event: { deltaX: number; deltaY: number; deltaMode: number }, pageHeight = 800): { dx: number; dy: number } {
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? pageHeight : 1;
  return { dx: event.deltaX * unit, dy: event.deltaY * unit };
}

interface Reply { request_id?: string; status?: string; error?: string; mode?: EmbedMode; windows?: EmbeddableWindow[]; window?: EmbeddedWindowState }

async function ask(action: string, payload: Record<string, unknown>, timeout = 10000): Promise<Reply> {
  const requestId = crypto.randomUUID();
  const reply = await wsRequest<Reply>(action, { ...payload, request_id: requestId }, "embedded_window",
    (data) => data.request_id === requestId, timeout);
  if (!reply) throw new Error("The worker did not answer.");
  if (reply.status !== "ok") throw new Error(reply.error || "The window could not be embedded.");
  return reply;
}

export function embedAvailable(): boolean {
  return Boolean(desktopBridge()?.embeddedWindow);
}

export async function embedHost(): Promise<EmbedHost> {
  const host = await desktopBridge()?.embeddedWindow?.host();
  if (!host) throw new Error("Only the desktop App can show other applications' windows.");
  return host;
}

export async function listWindows(host: EmbedHost): Promise<{ mode: EmbedMode; windows: EmbeddableWindow[] }> {
  const reply = await ask("embedded_windows", { exclude_pids: [host.pid] });
  return { mode: reply.mode ?? "native", windows: reply.windows ?? [] };
}

function hostPayload(host: EmbedHost, rect: PanelRect, visible: boolean) {
  return { handle: host.handle, pid: host.pid, rect: hostRect(host, rect), visible };
}

export async function attachWindow(id: number, host: EmbedHost, rect: PanelRect, visible: boolean, holding: Holding): Promise<EmbeddedWindowState> {
  const reply = await ask("embedded_window_attach", { window_id: id, host: hostPayload(host, rect, visible), ...holding }, 15000);
  return reply.window!;
}

export async function layoutWindow(id: number, host: EmbedHost, rect: PanelRect, visible: boolean, holder: string): Promise<EmbeddedWindowState> {
  const reply = await ask("embedded_window_layout", { window_id: id, host: hostPayload(host, rect, visible), holder });
  return reply.window!;
}

export async function windowState(id: number): Promise<EmbeddedWindowState> {
  return (await ask("embedded_window_state", { window_id: id })).window!;
}

export async function detachWindow(id: number, holder: string): Promise<void> {
  await ask("embedded_window_detach", { window_id: id, holder });
}

/** Where the window's picture is drawn inside a view of `box` CSS px when it
 *  is fitted whole (a preview): CSS px per window point and the offset. */
export function fittedFrame(box: { width: number; height: number }, frame: [number, number, number, number]) {
  const [, , width, height] = frame;
  if (!(width > 0 && height > 0) || !(box.width > 0 && box.height > 0)) return null;
  const scale = Math.min(box.width / width, box.height / height);
  return { scale, left: (box.width - width * scale) / 2, top: (box.height - height * scale) / 2, width: width * scale, height: height * scale };
}

/** A point in a fitted view (CSS px from its top-left) in window points;
 *  null outside the picture. */
export function fittedPoint(fit: NonNullable<ReturnType<typeof fittedFrame>>, x: number, y: number): { x: number; y: number } | null {
  const px = (x - fit.left) / fit.scale;
  const py = (y - fit.top) / fit.scale;
  if (px < 0 || py < 0 || x > fit.left + fit.width || y > fit.top + fit.height) return null;
  return { x: Math.round(px * 10) / 10, y: Math.round(py * 10) / 10 };
}

/** Pointer and key events for one window, sent once per animation frame,
 *  pointer moves and wheel turns folded so the socket never backs up. */
export function inputQueue(id: number) {
  let pending: Record<string, unknown>[] = [];
  let frame = 0;
  const flush = () => {
    frame = 0;
    if (!pending.length) return;
    const events = pending;
    pending = [];
    const socket = getSocket();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "embedded_window_input", window_id: id, events }));
    }
  };
  return {
    push(event: Record<string, unknown>) {
      const last = pending[pending.length - 1];
      if (last && event.type === "move" && last.type === "move" && last.buttons === event.buttons) {
        pending[pending.length - 1] = event;
      } else if (last && event.type === "wheel" && last.type === "wheel") {
        pending[pending.length - 1] = { ...event, dx: Number(last.dx) + Number(event.dx), dy: Number(last.dy) + Number(event.dy) };
      } else {
        pending.push(event);
      }
      if (!frame) frame = requestAnimationFrame(flush);
    },
    dispose() {
      if (frame) cancelAnimationFrame(frame);
      flush();
    },
  };
}

/** The windows embedded in this page, so the App can have them given back
 *  before it closes its window (a child window would close with it). */
const releasers = new Map<string, () => Promise<void>>();
let releaseHooked = false;

function reportCount() {
  desktopBridge()?.embeddedWindow?.setCount(releasers.size);
}

export function registerEmbedded(key: string, release: () => Promise<void>): () => void {
  const bridge = desktopBridge()?.embeddedWindow;
  if (bridge && !releaseHooked) {
    releaseHooked = true;
    bridge.onRelease(() => {
      void Promise.allSettled([...releasers.values()].map((fn) => fn())).then(() => bridge.released());
    });
  }
  releasers.set(key, release);
  reportCount();
  return () => {
    releasers.delete(key);
    reportCount();
  };
}

/** Icons from the last window list, by window id, for the tab strip. */
export const windowIcons = new Map<number, string>();

/** Runs `run` once in the next animation frame however often it is asked
 *  before then. */
export function frameCoalesced(
  run: () => void,
  frames: { request: (fn: () => void) => number; cancel: (id: number) => void } = {
    request: (fn) => requestAnimationFrame(fn), cancel: (id) => cancelAnimationFrame(id),
  },
) {
  let id = 0;
  return {
    schedule() {
      if (!id) id = frames.request(() => { id = 0; run(); });
    },
    cancel() {
      if (id) frames.cancel(id);
      id = 0;
    },
  };
}

/** The Worker's topic for another application's window changing, keyed by its id. */
export const WINDOW_CHANGED_TOPIC = "embedded_windows";
/** How often a view looks at a window the Worker cannot follow by events. */
export const UNFOLLOWED_LOOK_MS = 1500;

interface FollowDeps {
  onChanged: (topic: string, handler: (keys: readonly string[] | null) => void) => () => void;
  setInterval: (fn: () => void, ms: number) => unknown;
  clearInterval: (id: unknown) => void;
}

const followDeps: FollowDeps = {
  onChanged,
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (id) => clearInterval(id as ReturnType<typeof setInterval>),
};

/** Calls `changed` when the window may have moved, been resized, minimized,
 *  restored, renamed or closed: on the Worker's word when it `follows` the
 *  window's events, else every `UNFOLLOWED_LOOK_MS` while the view is open.
 *  Returns the stop. */
export function followWindow(id: number, follows: boolean, changed: () => void, deps: FollowDeps = followDeps): () => void {
  if (!follows) {
    const timer = deps.setInterval(changed, UNFOLLOWED_LOOK_MS);
    return () => deps.clearInterval(timer);
  }
  const key = String(id);
  // A reconnect (keys null) may have lost a change: look again then too.
  return deps.onChanged(WINDOW_CHANGED_TOPIC, (keys) => {
    if (!keys || keys.includes(key)) changed();
  });
}

/** Calls `moved` when `el` may have moved or changed size in the window:
 *  it is resized, the page is resized or zoomed, something scrolls, the
 *  page shows or hides, or a layout change shifts it without resizing it
 *  (an intersection observer whose root is cropped to the element's own
 *  rectangle, the way Floating UI notices layout shifts). Returns the stop. */
export function watchPlacement(el: HTMLElement, moved: () => void): () => void {
  let shift: IntersectionObserver | null = null;
  // `threshold` is how much of the element the cropped root sees while it
  // stays put: all of it, or less where an ancestor clips it.
  const arm = (threshold = 1) => {
    shift?.disconnect();
    shift = null;
    const rect = el.getBoundingClientRect();
    const root = document.documentElement;
    if (rect.width < 1 || rect.height < 1) return;
    const inset = [
      Math.floor(rect.top), Math.floor(root.clientWidth - rect.right),
      Math.floor(root.clientHeight - rect.bottom), Math.floor(rect.left),
    ];
    // Wholly or partly off screen: the next resize or scroll arms it again.
    if (inset.some((value) => value < 0)) return;
    let first = true;
    shift = new IntersectionObserver(([entry]) => {
      const ratio = entry?.intersectionRatio ?? 0;
      if (first) {
        first = false;
        if (Math.abs(ratio - threshold) < 0.001) return;
        // Wholly clipped: nothing to see until a resize or scroll.
        if (ratio <= 0) { shift?.disconnect(); shift = null; return; }
        moved();
        arm(ratio);
        return;
      }
      moved();
      arm();
    }, { rootMargin: inset.map((value) => `${-value}px`).join(" "), threshold: Math.min(1, Math.max(0, threshold)) });
    shift.observe(el);
  };
  const changed = () => { moved(); arm(); };
  const size = new ResizeObserver(changed);
  size.observe(el);
  window.addEventListener("resize", changed);
  window.addEventListener("scroll", changed, { capture: true, passive: true });
  document.addEventListener("visibilitychange", changed);
  arm();
  return () => {
    shift?.disconnect();
    size.disconnect();
    window.removeEventListener("resize", changed);
    window.removeEventListener("scroll", changed, { capture: true });
    document.removeEventListener("visibilitychange", changed);
  };
}
