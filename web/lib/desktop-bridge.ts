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
import { useCenterTabs } from "@/lib/state/center-tabs-store";

export interface DesktopWebTabState {
  id: string;
  url?: string;
  title?: string;
  loading?: boolean;
  canGoBack?: boolean;
  canGoForward?: boolean;
}

export interface DesktopWebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DesktopWebTabApi {
  /** Create the WebContentsView for `id` if missing, then loadURL. */
  ensure(id: string, url: string): void;
  /** loadURL on the existing view (created if missing). */
  navigate(id: string, url: string): void;
  /** DIP rect relative to the window content area. */
  setBounds(id: string, bounds: DesktopWebTabBounds): void;
  show(id: string): void;
  hide(id: string): void;
  destroy(id: string): void;
  reload(id: string): void;
  goBack(id: string): void;
  goForward(id: string): void;
  /** Navigation/title/loading events pushed from main; returns the
   *  unsubscribe function. */
  onState(cb: (state: DesktopWebTabState) => void): () => void;
}

export interface DesktopBridge {
  isDesktop: true;
  /** shell.openExternal — http/https only. */
  openExternal(url: string): void;
  webTab: DesktopWebTabApi;
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
    if (!alive.has(id)) {
      bridge.webTab.destroy(id);
      liveViewIds.delete(id);
    }
  }
}

let installed = false;

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
  });
  window.addEventListener("op-desktop-close-tab", () => {
    const s = useCenterTabs.getState();
    if (s.activeId) s.closeTab(s.activeId);
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
    if (!d?.url || d.op !== "open") return;
    useCenterTabs.getState().openWebTab(d.url);
    const ws = (window as unknown as { ws?: WebSocket }).ws;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({ action: "webtab_result", req_id: d.req_id, ok: true }),
      );
    }
  });
  useCenterTabs.subscribe((s) => {
    destroyStaleWebViews(
      bridge,
      s.tabs.map((t) => t.id),
    );
  });
}
