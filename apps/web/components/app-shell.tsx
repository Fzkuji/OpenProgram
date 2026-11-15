"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import dynamic from "next/dynamic";
import { useRouter, usePathname } from "next/navigation";
import { PageShell } from "./page-shell";
import { Sidebar } from "./sidebar/sidebar";
import { RightSidebar } from "./right-sidebar/right-sidebar";
import { ControlTips } from "./control-tips";
import { CenterTabStrip } from "./center-tabs/strip/center-tab-strip";
import { CanvasControls } from "./center-tabs/canvas/canvas-controls";
import { CanvasOverview } from "./center-tabs/canvas/canvas-overview";
import { playCanvasTransition } from "@/lib/tabs/canvas/canvas-transition";
import { sessionForShellReturn } from "@/lib/tabs/navigation/shell-session";
import { canvasCssGeometry, leaves, rowLayout } from "@/lib/tabs/canvas/canvas-layout";
import { smallCanvasPanes, useCenterBodySize, watchCenterBodySize } from "@/lib/tabs/canvas/center-body-size";
import { WebTabPip } from "./center-tabs/pip/web-tab-pip";
import { AppPreviewPip } from "./center-tabs/pip/app-preview-pip";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { topLevelTabs } from "@/lib/browser/web-tab/web-page-management";
import {
  findCenterTabGroup,
  resolveCenterTabPanes,
} from "@/lib/tabs/center-tab-groups";
import {
  desktopBridge,
  installDesktopMenuHandlers,
  setDesktopSplitLayoutAvailable,
} from "@/lib/desktop/desktop-bridge";
import { ToastHost } from "./ui/toast-host";
import { VersionNotices } from "./settings/version-notices";
import { RewindDialog } from "./chat/rewind/rewind-dialog";
import { RewindPicker } from "./chat/rewind/rewind-picker";
import { CommandPalette } from "./command-palette";
import { Composer } from "./chat/composer";
import { LegacyTopbarBridge } from "./chat/top-bar";
import { WelcomeScreen } from "./chat/welcome/welcome-screen";
import { MessageList } from "./chat/messages/list/message-list";
import { PeerSessionPane } from "./chat/pane/peer-session-pane";
import { DagView } from "./chat/dag/dag-view";
import { ViewControls } from "./chat/pane/view-controls";
import { useSessionStore } from "@/lib/session-store";
import { SessionScopeProvider } from "@/lib/session-store/session-scope";
import { useColResize } from "@/lib/hooks/use-col-resize";
import { useTranslation } from "@/lib/i18n";
import { isSplitLayoutAvailable } from "@/lib/tabs/canvas/split-layout";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { newSession } from "@/lib/runtime-bridge/session/conversations";
import { markSessionReadLocally, windowVisibilityChanged } from "@/lib/runtime-bridge/session/chat-handlers";
import { completeTabRouteNavigation } from "@/lib/tabs/navigation/route";
import { setNavigate } from "@/lib/nav/navigate";
import { startAttentionBridge } from "@/lib/desktop/attention";
import { openSession, reopenActiveSessionRoute } from "@/lib/tabs/navigation/open-session";
import { setLastChatPath } from "@/lib/tabs/navigation/last-chat-path";
import { enterExclusiveCoverageMode, renderHistoryGraph } from "@/lib/runtime-bridge/dag";
import { initOverlayScrollbars } from "@/lib/runtime-bridge/dom/scrollbar";
import { hostPaintsRows } from "@/lib/chat/history/message-window";

function DeferredPaneLoading() {
  return (
    <div className="flex h-full items-center justify-center text-fs-xs text-text-muted">
      Loading…
    </div>
  );
}

const BuiltinTabPane = dynamic(
  () => import("./center-tabs/pages/builtin-tab-pane").then((module) => module.BuiltinTabPane),
  { ssr: false, loading: DeferredPaneLoading },
);
const BrowserHomePage = dynamic(
  () => import("./center-tabs/browser/browser-home-page").then((module) => module.BrowserHomePage),
  { ssr: false, loading: DeferredPaneLoading },
);
const ApplicationTabPane = dynamic(
  () => import("./center-tabs/pages/application-tab-pane").then((module) => module.ApplicationTabPane),
  { ssr: false, loading: DeferredPaneLoading },
);
const FileTabPane = dynamic(
  () => import("./center-tabs/files/file-tab-pane").then((module) => module.FileTabPane),
  { ssr: false, loading: DeferredPaneLoading },
);
const ReviewTabPane = dynamic(
  () => import("./center-tabs/review/review-tab-pane").then((module) => module.ReviewTabPane),
  { ssr: false, loading: DeferredPaneLoading },
);
const FilesPage = dynamic(
  () => import("./center-tabs/files/files-page").then((module) => module.FilesPage),
  { ssr: false, loading: DeferredPaneLoading },
);
const NewTabPage = dynamic(
  () => import("./center-tabs/pages/new-tab-page").then((module) => module.NewTabPage),
  { ssr: false, loading: DeferredPaneLoading },
);
const TerminalPage = dynamic(
  () => import("./center-tabs/pages/terminal-page").then((module) => module.TerminalPage),
  { ssr: false, loading: DeferredPaneLoading },
);
const WebTabPane = dynamic(
  () => import("./center-tabs/browser/web-tab-pane").then((module) => module.WebTabPane),
  { ssr: false, loading: DeferredPaneLoading },
);

import { PersistentFilePanes } from "./center-tabs/files/persistent-file-panes";
import { CodeRainArrivals } from "./chat/welcome/code-rain-arrivals";

const LONE_PANE = { left: "0px", top: "0px", width: "100%", height: "100%" };

// Scripts shared by every page — loaded once on shell mount and kept alive for
// the whole session. Page-specific scripts live in PageShell. Files sit in
// apps/web/public/js/shared/ so the static tree groups them together.
// Legacy shared JS modules. We keep loading the ones that the
// not-yet-migrated chat page + sidebar + right rail still depend on;
// the settings/functions/chats trios are gone (migrated to React).
// `shared/conversations.js` is migrated — see `apps/web/lib/conversations.ts`
// (imported for side effects by `useWS`).
// All legacy public/js scripts are migrated to lib/ — see the
// `import "@/lib/..."` side-effect imports above.
// Markdown and KaTeX are bundled npm dependencies. There is no runtime
// script injection or external-library readiness gate.

// Routes where the right sidebar (History / Execution Detail) is
// relevant. Functions / Chats / Settings don't need it, so it's hidden
// there even though the DOM persists.
/**
 * The single-session composer, scoped to whichever chat is focused.
 *
 * Split panes each declare their own scope (`PeerSessionPane`); this is the
 * non-split counterpart, and its existence is what lets the Composer drop
 * every "no scope → read the focused global slice" fallback. `__new__` stands
 * in before any chat exists — the same placeholder key the draft maps use.
 */
function FocusedComposer() {
  const sid = useSessionStore(
    (s) => s.activeChatKey ?? s.currentSessionId ?? "__new__",
  );
  return (
    <SessionScopeProvider sid={sid}>
      <Composer />
    </SessionScopeProvider>
  );
}

function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/s/");
}


export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  useLayoutEffect(() => {
    if (completeTabRouteNavigation(pathname)) return;
    useCenterTabs.getState().recordRouteNavigation(pathname);
  }, [pathname]);
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  // Desktop notifications open their session; keep-awake follows running sessions.
  useEffect(() => startAttentionBridge(openSession), []);

  // Background route warm-up. After AppShell paints and the main
  // thread goes idle, prefetch each commonly-used route in priority
  // order — most-visited first, one at a time, spaced 800ms apart so
  // we don't dogpile the dev server's webpack workers. In dev mode
  // each prefetch kicks off the on-demand compile; in prod it just
  // primes the chunk cache. Either way, by the time the user clicks
  // a sidebar item the route is usually already ready and the click
  // feels instant.
  useEffect(() => {
    // Ordered by observed click frequency. The current pathname is
    // skipped (already loaded) and /chat / /s/<id> don't need
    // prefetching (their UI is mounted inside AppShell directly).
    const WARM_ROUTES = [
      "/settings/general",
      "/functions",
      "/agents",
      "/programs",
      "/applications",
      "/skills",
      "/settings/providers",
      "/memory",
      "/scheduler",
      "/settings/environments",
      "/settings/voice",
      "/settings/appearance",
      "/settings/channels",
      "/settings/search",
      "/mcp",
      "/plugins",
      "/chats",
    ];
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    function warmNext(i: number) {
      if (cancelled) return;
      if (i >= WARM_ROUTES.length) return;
      const route = WARM_ROUTES[i];
      if (route !== pathnameRef.current) {
        try { router.prefetch(route); } catch { /* ignore */ }
      }
      timer = setTimeout(() => warmNext(i + 1), 800);
    }
    // Wait until the browser is idle so prefetches don't compete with
    // the initial render's JS work. Falls back to a 1.5s delay where
    // requestIdleCallback isn't available (Safari).
    const win = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    let idleId: number | null = null;
    if (typeof win.requestIdleCallback === "function") {
      idleId = win.requestIdleCallback(() => warmNext(0), { timeout: 3000 });
    } else {
      timer = setTimeout(() => warmNext(0), 1500);
    }
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (idleId != null && typeof win.cancelIdleCallback === "function") {
        win.cancelIdleCallback(idleId);
      }
    };
  // Once per shell mount. Pathname is read from the ref so a route
  // change does not restart the queue; the slot still skips whatever
  // page is current at that tick.
  }, [router]);
  // Debug hooks for the desktop multi-window acceptance runs, which drive
  // the renderer over CDP and therefore can only reach the stores through
  // `window`. Not read by any application code.
  useEffect(() => {
    window.__centerTabs = useCenterTabs;
    import("@/lib/desktop/desktop-bridge").then((m) => {
      window.__desktopTransfer = {
        desktopBridge: m.desktopBridge,
        buildTransferPayload: m.buildTransferPayload,
        stageIncomingTransfer: m.stageIncomingTransfer,
        placementForDropIntent: m.placementForDropIntent,
        acceptedTransfers: m.acceptedTransfers,
      };
    });
    return () => {
      delete window.__centerTabs;
      delete window.__desktopTransfer;
    };
  }, []);

  // Mount targets for chat-page React portals. PageShell injects
  // `<div id="composer-mount">` and `<div id="welcome-mount">`
  // placeholders into the legacy template; we portal React into each.
  // Re-checked on pathname changes because the chat page re-injects
  // its HTML on route entry.
  // Note: there is no topbar mount — the 48px topbar row is gone
  // (chat chrome is just the 40px tab strip); its chips moved to the
  // composer bottom row / History header. Nothing in the legacy JS
  // looks up `#mainTopbar`, so no hidden stand-in element is needed.
  const [composerMount, setComposerMount] = useState<HTMLElement | null>(null);
  const [welcomeMount, setWelcomeMount] = useState<HTMLElement | null>(null);
  const [messagesMount, setMessagesMount] = useState<HTMLElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    setComposerMount(null);
    setWelcomeMount(null);
    setMessagesMount(null);
    function findMounts() {
      const composer = document.getElementById("composer-mount");
      const welcome = document.getElementById("welcome-mount");
      const messages = document.getElementById("messages-mount");
      if (cancelled) return false;
      if (composer) setComposerMount(composer);
      if (welcome) setWelcomeMount(welcome);
      if (messages) setMessagesMount(messages);
      return !!(composer && welcome && messages);
    }
    if (findMounts()) return;
    const t = setInterval(() => {
      if (findMounts()) clearInterval(t);
    }, 100);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [pathname]);

  // A split that dissolves hands its remaining session back to the chat
  // shell, which sat hidden on whichever session was focused last. The
  // route reaches the shell renders later (activeId → URL → store), so
  // switch it in the same tick as the layout; otherwise its first frames
  // show the session that just left.
  useEffect(() => {
    document.addEventListener("visibilitychange", windowVisibilityChanged);
    return () => document.removeEventListener("visibilitychange", windowVisibilityChanged);
  }, []);

  useEffect(() => useCenterTabs.subscribe((s, prev) => {
    const sid = sessionForShellReturn(prev, s);
    if (!sid || !isChatRoute(window.location.pathname)) return;
    runtimeState.currentSessionId = sid;
    useSessionStore.getState().setCurrentConv(sid);
  }), []);

  // Sync `.active` on sidebar nav items to the current route and close any
  // open popover when navigating. The functions page inline script also sets
  // `.active` on mount, but nothing removes it on navigation — we own that.
  useEffect(() => {
    const items: Array<[string, string]> = [
      ["navAbility", "/programs"],
      ["navApplications", "/applications"],
      ["navHistory", "/chats"],
      ["navScheduler", "/scheduler"],
    ];
    for (const [id, path] of items) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("active", pathname === path);
    }
    // /chat under a tab that holds a conversation: the route is handed back
    // to that conversation and this effect runs again for it.
    if (pathname === "/chat" && reopenActiveSessionRoute()) return;
    // Remember the last chat route so a `/functions → run` hand-off can
    // return to the conversation the user came from, not a blank /chat.
    // It must survive leaving the chat route entirely (the store's
    // currentSessionId is nulled then), hence its own module.
    if (isChatRoute(pathname)) setLastChatPath(pathname);

    // Keep `runtimeState.currentSessionId` in lockstep with the Next.js
    // client route. It seeds itself from the URL once at module load;
    // SPA navigations between sessions don't re-run that, so a click on
    // a sidebar conv updates the URL while the id would otherwise stay
    // pinned at whatever the previous ``chat_ack`` wrote — the model
    // picker then sent ``session_id`` of the OLD conv to ``/api/model``.
    try {
      const m = pathname.match(/^\/s\/([^/]+)/);
      const sid = m ? m[1] : null;
      runtimeState.currentSessionId = sid;
      // Keep the React message store's active conversation in lockstep
      // with the route so <MessageList /> shows the right stream. A
      // brand-new chat (/chat → sid null) gets its real id later from
      // the `chat_ack` reducer.
      const sessionStore = useSessionStore.getState();
      if (sid) {
        sessionStore.setCurrentConv(sid);
      } else {
        const tabs = useCenterTabs.getState();
        const active = tabs.tabs.find((tab) => tab.id === tabs.activeId);
        if (active?.kind === "session" && active.draft && active.sessionId) {
          sessionStore.setCurrentDraft(active.sessionId);
        } else {
          sessionStore.setCurrentConv(null);
        }
      }
      // Tell the backend which conversation we're now viewing, so it clears
      // this conv's unread (blue) dot and won't re-mark it unread when a
      // background run here finishes. Opening a conv is otherwise pure
      // client-side routing — this is the only "seen" signal the server gets.
      // (On first load the socket may not be open yet; use-ws's onopen
      // re-sends this for the current conv.)
      if (sid && document.visibilityState !== "hidden") markSessionReadLocally(sid);
      const sock = getSocket();
      if (sock && sock.readyState === WebSocket.OPEN) {
        sock.send(JSON.stringify({ action: "mark_session_read", session_id: sid }));
      }
    } catch {
      /* ignore */
    }

    // New chat route (/chat, no session_id): clear the persisted History
    // graph + Execution Detail panel so the user doesn't see stale
    // content from whatever conversation they were just on. /c/:id
    // reloads both via `load_session` → conversations.ts →
    // renderHistoryGraph + subsequent node click → showDetail.
    if (pathname === "/chat") {
      renderHistoryGraph([], null);

      // Execution Detail panel lives in the right sidebar and is
      // rendered by React (`DetailPanel`). Clearing the store's
      // selection drops it back to the empty state — writing
      // `#detailBody.innerHTML` here would fight React for that node.
      useSessionStore.getState().closeDetail();
    }
    // The React `<Sidebar />` renders nav items synchronously on mount,
    // so depending on `pathname` alone is sufficient now — no need to
    // wait for an async HTML inject before the first .active sync.
  }, [pathname]);

  useEffect(() => {
    // Publish client-side navigation for non-React callers, which would
    // otherwise need `window.location.href` (a full reload killing the shell).
    setNavigate((path: string) => router.push(path));

    // Intercept clicks on anchor tags that point to internal paths so they go
    // through the Next.js router instead of a full page reload.
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as HTMLElement)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a) return;
      const href = a.getAttribute("href") || "";
      if (!href.startsWith("/") || href.startsWith("//")) return;
      if (a.target && a.target !== "" && a.target !== "_self") return;
      e.preventDefault();
      router.push(href);
    };
    document.addEventListener("click", onClick);

    // Overlay scrollbars (was shared/scrollbar.js).
    initOverlayScrollbars();

    return () => {
      document.removeEventListener("click", onClick);
    };
  }, [router]);

  // Legacy detail-panel resize handle. React side rails own their resize
  // behavior through `components/layout/use-resizable-rail.ts`.
  useColResize({
    handleId: "detailResize",
    targetId: "detailPanel",
    direction: -1,
    minWidth: 200,
  });

  // Desktop shell (Electron): tag <html> with `is-desktop` for global CSS,
  // flip the React layout to Chrome-macOS style (full-width tab row above
  // all three columns, traffic lights living in that row), and wire
  // File > New Tab / Close Tab menu accelerators at startup — installing
  // only from the web-tab pane would leave Cmd+T dead until a web tab was
  // opened once. First frame renders the browser layout, then snaps to
  // desktop — acceptable one-time flash inside Electron.
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    const bridge = desktopBridge();
    if (bridge) {
      document.documentElement.classList.add("is-desktop");
      if (bridge.platform === "win32") {
        document.documentElement.classList.add("desktop-windows");
      }
      setIsDesktop(true);
      installDesktopMenuHandlers();
    }
  }, []);

  // Center tab container — which pane the center column shows. The
  // chat surface is a SINGLETON: session tabs merely reveal it (it
  // stays mounted, display:none, under file / new-tab panes).
  const tabs = useCenterTabs((s) => s.tabs);
  const groups = useCenterTabs((s) => s.groups);
  const activeId = useCenterTabs((s) => s.activeId);
  const activeTab = tabs.find((tab) => tab.id === activeId);
  const activeKind = activeTab?.kind ?? "session";
  // Session tabs carry their own perspective (transcript / context DAG).
  const activeTabDagView = activeKind === "session" && !!activeTab?.dagView;
  const activeGroup = activeId
    ? findCenterTabGroup(groups, activeId)
    : undefined;
  const splitRatio = useCenterTabs((s) => s.splitRatio);
  const centerBodyRef = useRef<HTMLDivElement | null>(null);
  // The body's size, from its ResizeObserver's entries: no layout read in
  // the frames of a rail's move (lib/tabs/canvas/center-body-size.ts).
  useEffect(() => {
    const node = centerBodyRef.current;
    return node ? watchCenterBodySize(node) : undefined;
  }, []);
  const splitAvailable = useCenterBodySize((size) => isSplitLayoutAvailable(size.width));
  const compoundPanes = resolveCenterTabPanes(activeGroup, tabs, activeId);
  const focusedPanes = resolveCenterTabPanes(
    undefined,
    tabs,
    activeGroup?.focusedId ?? activeId,
  );
  // Split is a DOM layout concern, so it works in the browser too — web
  // tabs there are iframes, which pane like any other element. The only
  // desktop-specific part is the native web view, whose bounds already
  // follow the pane rect (WebTabPane → registerVisibleWebTabBounds).
  // Below the two panes' combined minimum width there is no room to split,
  // so fall back to the focused tab alone.
  const panes = topLevelTabs(tabs, groups).length === 0
    ? []
    : activeGroup ? compoundPanes : focusedPanes;
  const canvas = useMemo(() => activeGroup?.canvas ?? rowLayout(activeId ? [activeId] : [], splitRatio), [activeGroup?.canvas, activeId, splitRatio]);
  const canvasLeaves = leaves(canvas.root);
  const smallPanes = new Set(useCenterBodySize((size) => smallCanvasPanes(canvas.root, size.width, size.height)).split(" "));
  // Panes sit in the canvas as shares of it (calc(% + px)), so the browser
  // sizes them in the same layout pass as the canvas; the measured size
  // above only decides whether a pane is too small to show.
  const canvasInset = canvasLeaves.length > 1 ? 1 : 0;
  const cssGeometry = useMemo(() => canvasCssGeometry(canvas.root, canvasInset), [canvas.root, canvasInset]);
  const webIds = panes.flatMap(p => p.kind !== "session" && tabs.find(t => t.id === p.tabId)?.kind === "web" ? [p.tabId] : []);
  const liveWebIds = new Set([...(activeId && webIds.includes(activeId) ? [activeId] : []), ...webIds.filter(id => id !== activeId)].slice(0, 8));
  const sessionPaneIndex = panes.findIndex((pane) => pane.kind === "session");
  const showChat = isChatRoute(pathname);
  const paintRows = hostPaintsRows(showChat, sessionPaneIndex, activeTabDagView);
  // Paint-gate treats ancestor `display:none` (split / non-chat route) as
  // hidden. DagView only flushes on `visible`, which stays true across
  // those. Flush here when the host is actually on screen.
  useEffect(() => {
    if (showChat && sessionPaneIndex >= 0 && activeTabDagView) {
      enterExclusiveCoverageMode();
    }
  }, [showChat, sessionPaneIndex, activeTabDagView]);
  useEffect(() => {
    setDesktopSplitLayoutAvailable(
      isDesktop && activeKind === "session" && splitAvailable,
    );
  }, [isDesktop, activeKind, splitAvailable]);

  // A layout change the canvas buttons announced (red, yellow, green, the
  // tiling menu) animates from here, before its first frame paints.
  useLayoutEffect(() => { playCanvasTransition(); });

  function centerPaneClassName(_index: number) { return "center-canvas-content"; }
  function centerPaneStyle(index: number): CSSProperties {
    const item = panes[index];
    const tabId = item?.kind === "session" ? item.activeTabId : item?.tabId;
    const p = canvasLeaves.find(p => p.content === tabId);
    if (!p) return { display: "none" };
    const zoomed = canvas.zoomedPaneId === p.id;
    const hidden = (canvas.zoomedPaneId && !zoomed) || smallPanes.has(zoomed ? "viewport" : p.id);
    // No per-pane title bar: content fills its pane. In a multi-pane layout
    // every pane is its own floating card (see `.center-col[data-split]` in
    // styles/base/layout.css), so it keeps a 1px inset and the cards' 16px corners; a lone
    // pane is flush inside the centre card, whose rounded clip then holds
    // its content. A web tab's native view escapes that clip, so its page
    // meets the card edge with square corners, as a browser window does.
    const css = !canvasInset ? LONE_PANE : zoomed ? cssGeometry.viewport : cssGeometry.panes.get(p.id)!;
    return { position:"absolute",...css,minWidth:0,minHeight:0,overflow:"hidden",borderRadius:canvasInset ? 16 : undefined,display:hidden?"none":"flex",flexDirection:"column" };
  }

  function renderTabPane(tabId: string, kind: "peer" | "tab") {
    const tab = tabs.find((candidate) => candidate.id === tabId);
    if (!tab) return null;
    if (kind === "peer") {
      return (
        <PeerSessionPane
          tabId={tab.id}
          sessionId={tab.sessionId ?? null}
          title={tab.title}
          showTitle={false}
        />
      );
    }
    if (tab.kind === "file" && tab.path && (tab.projectId || (tab.readOnly && tab.fileSessionId))) {
      return <FileTabPane projectId={tab.projectId || ""} path={tab.path} sessionId={tab.fileSessionId} readOnly={tab.readOnly} previewSessionId={tab.previewOwnerSessionId} />;
    }
    if (tab.kind === "web") {
      return <WebTabPane tabId={tab.id} url={tab.url ?? ""} suspended={!liveWebIds.has(tab.id)} />;
    }
    if (tab.kind === "application" && tab.applicationInstanceId) {
      return <ApplicationTabPane instanceId={tab.applicationInstanceId} />;
    }
    if (tab.kind === "ntp") return <NewTabPage />;
    if (tab.kind === "builtin" && tab.page) {
      if (tab.page === "review") {
        return (
          <ReviewTabPane
            sessionId={tab.reviewSessionId ?? ""}
            assistantMsgId={tab.reviewMsgId}
            initialScope={tab.reviewScope}
            initialPath={tab.reviewPath}
          />
        );
      }
      if (tab.page === "browser") return <BrowserHomePage />;
      if (tab.page === "files") return <FilesPage />;
      if (tab.page === "terminal") return <TerminalPage preset="shell" />;
      if (tab.page === "claude") return <TerminalPage preset="claude" />;
      return <BuiltinTabPane page={tab.page} />;
    }
    return null;
  }

  return (
    <div
      className={isDesktop ? "desktop-frame" : undefined}
      style={
        isDesktop
          ? { display: "flex", flexDirection: "column", height: "100vh" }
          : undefined
      }
    >
      {/* Desktop (Electron): the tab strip is window chrome — one full-width
         row with macOS traffic lights or Windows caption controls embedded
         at the platform-native end. Always visible on every route. The strip
         is a singleton; when it lives here it must NOT also render inside
         center-col below. */}
      {isDesktop ? (
        <div className="desktop-tab-row">
          <span className="desktop-resize-edge desktop-resize-n" aria-hidden="true" />
          <span className="desktop-resize-edge desktop-resize-w" aria-hidden="true" />
          <span className="desktop-resize-edge desktop-resize-e" aria-hidden="true" />
          <CenterTabStrip />
        </div>
      ) : null}
      <div className="app">
      <Sidebar />
      <ControlTips />
      {/* Center column: browser-style tab strip over the active tab's
         pane. `order: 2` slots it where `.app .main` used to sit (the
         chat .main is now a grandchild, flexing inside .center-body).
         Hidden (not unmounted) on non-chat routes. */}
      <div
        className="center-col"
        data-split={canvasLeaves.length > 1 && !canvas.zoomedPaneId ? "" : undefined}
        style={{
          display: showChat ? "flex" : "none",
          flexDirection: "column",
          flex: "1 1 auto",
          minWidth: 0,
          order: 2,
        }}
      >
        {!isDesktop ? <CenterTabStrip /> : null}
        <div
          ref={centerBodyRef}
          className="center-body"
          data-canvas-root="true"
          style={{ flex: 1, minHeight: 0, display: "flex", position: "relative" }}
        >
          {/* Chat shell is mounted ONCE at the layout level and kept
             alive across session switches AND non-session tabs. This
             is what makes the WS + DOM + right sidebar state persist.
             The context-DAG perspective is a SIBLING of that shell,
             also always mounted: the DAG renderer paints into its host
             DOM on every capture, so unmounting it would blank the
             graph until the next one. `data-center-view` swaps which of
             the two is visible (see .center-pane-chat in chat.css) —
             a display flip, so the perspective toggle is instant. */}
          <div
            className={
              (sessionPaneIndex < 0
                ? "center-single-pane"
                : centerPaneClassName(sessionPaneIndex)) + " center-pane-chat"
            }
            data-center-tab-id={panes[sessionPaneIndex]?.kind === "session" ? panes[sessionPaneIndex].activeTabId : undefined}
            data-center-view={activeTabDagView ? "dag" : "session"}
            style={
              sessionPaneIndex < 0
                ? { display: "none" }
                : centerPaneStyle(sessionPaneIndex)
            }
          >
            <PageShell page="chat" />
            <DagView visible={activeTabDagView} />
            <ViewControls />
          </div>
          {showChat && activeId && panes.length > 0 ? <CanvasControls layout={canvas} targetId={activeId} /> : null}
          {showChat
            ? panes.map((pane, index) => {
                if (pane.kind === "session") return null;
                const paneTab = tabs.find((tab) => tab.id === pane.tabId);
                if (paneTab?.kind === "file") return null;
                const content = renderTabPane(pane.tabId, pane.kind);
                if (!content) return null;
                return (
                  <div
                    key={pane.key}
                    data-center-tab-id={pane.tabId}
                    data-page-surface={pane.kind === "peer" ? undefined : ""}
                    onPointerDownCapture={() => useCenterTabs.getState().setActive(pane.tabId)}
                    className={centerPaneClassName(index)}
                    style={centerPaneStyle(index)}
                  >
                    {content}
                  </div>
                );
              })
            : null}
          <PersistentFilePanes tabs={tabs} layouts={new Map(panes.flatMap((pane, index) => pane.kind === "session" ? [] : [[pane.tabId, { className: centerPaneClassName(index), style: centerPaneStyle(index) }]]))} activeFileIds={new Set(panes.flatMap((pane) => pane.kind === "session" ? [] : tabs.find((tab) => tab.id === pane.tabId)?.kind === "file" ? [pane.tabId] : []))} />
          {showChat ? <WebTabPip /> : null}
          {showChat ? <AppPreviewPip /> : null}
          {showChat && tabs.length === 0 ? <EmptyCenter /> : null}
          {showChat ? <CanvasOverview /> : null}
        </div>
      </div>
      {/* Non-chat routes render their own page content via the router,
         inside the same centre card the chat column uses. */}
      {!showChat && (
        <div
          className="center-col page-col"
          style={{ display: "flex", flex: "1 1 auto", minWidth: 0, order: 2 }}
        >
          {children}
        </div>
      )}
      {/* Right sidebar — persistent across conversations. Hidden (not
         unmounted) on non-chat routes so its state survives. */}
      <div style={{ display: showChat ? "contents" : "none" }}>
        <RightSidebar />
      </div>
      {composerMount && createPortal(<FocusedComposer />, composerMount)}
      {welcomeMount && createPortal(<WelcomeScreen />, welcomeMount)}
      {messagesMount && createPortal(<MessageList paintRows={paintRows} />, messagesMount)}
      {messagesMount && <CodeRainArrivals mount={messagesMount} />}
      {/* Headless — keeps the legacy topbar-updater wrappers installed
         (status / branch / agent state → zustand store) now that the
         visible topbar row is gone. Must live outside the chat-route
         conditional so the wrappers survive route switches. */}
      <LegacyTopbarBridge />
      {/* App-wide transient toasts — mounted at the shell so they show on
         every route (chat AND settings), not just where the TopBar is. */}
      <ToastHost />
      <VersionNotices />
      <RewindDialog />
      <RewindPicker />
      <CommandPalette />
      </div>
    </div>
  );
}

/** Shown when every center tab is closed, so the column is never blank. */
function EmptyCenter() {
  const { text } = useTranslation();
  return (
    <div className="center-empty">
      <p>{text("No open tabs", "没有打开的标签页")}</p>
      <button
        type="button"
        className="center-empty-btn"
        onClick={() => newSession(useCenterTabs.getState().openDraftSessionTab())}
      >
        {text("New chat", "新建对话")}
      </button>
    </div>
  );
}
