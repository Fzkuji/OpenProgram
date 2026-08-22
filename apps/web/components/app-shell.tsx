"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, usePathname } from "next/navigation";
import { PageShell } from "./page-shell";
import { Sidebar } from "./sidebar/sidebar";
import { RightSidebar } from "./right-sidebar/right-sidebar";
import { CenterTabStrip } from "./center-tabs/center-tab-strip";
import { BuiltinTabPane } from "./center-tabs/builtin-tab-pane";
import { BrowserHomePage } from "./center-tabs/browser-home-page";
import { FileTabPane } from "./center-tabs/file-tab-pane";
import { FilesPage } from "./center-tabs/files-page";
import { NewTabPage } from "./center-tabs/new-tab-page";
import { TerminalPage } from "./center-tabs/terminal-page";
import { WebTabPane } from "./center-tabs/web-tab-pane";
import { WebTabPip } from "./center-tabs/web-tab-pip";
import { useWebTabPip } from "@/lib/state/web-tab-pip-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import {
  findCenterTabGroup,
  resolveCenterTabPanes,
} from "@/lib/state/center-tab-groups";
import {
  desktopBridge,
  installDesktopMenuHandlers,
  setDesktopSplitLayoutAvailable,
} from "@/lib/desktop-bridge";
import { ToastHost } from "./ui/toast-host";
import { Composer } from "./chat/composer";
import { LegacyTopbarBridge } from "./chat/top-bar";
import { WelcomeScreen } from "./chat/welcome-screen";
import { MessageList } from "./chat/messages/message-list";
import { PeerSessionPane } from "./chat/peer-session-pane";
import { DagView } from "./chat/dag-view";
import { ViewControls } from "./chat/view-controls";
import { useSessionStore } from "@/lib/session-store";
import { SessionScopeProvider } from "@/lib/session-store/session-scope";
import { useColResize } from "@/lib/use-col-resize";
import { useTranslation } from "@/lib/i18n";
import {
  clampSplitRatioForWidth,
  createSplitLayoutMeasureScheduler,
  isSplitLayoutAvailable,
} from "@/lib/split-layout";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { setNavigate } from "@/lib/navigate";
import { setLastChatPath } from "@/lib/last-chat-path";
import { loadExternalLibs } from "@/lib/external-libs";
import { renderHistoryGraph } from "@/lib/runtime-bridge/dag";
import { initOverlayScrollbars } from "@/lib/runtime-bridge/scrollbar";

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
// CDN libs (marked / KaTeX) live in `lib/external-libs.ts`; the shared
// inline-script fetch+inject machinery is gone with the legacy JS.

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
  const { t } = useTranslation();

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
      "/skills",
      "/settings/providers",
      "/memory",
      "/scheduler",
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
      if (route !== pathname) {
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
  // Re-run when pathname changes so we don't waste a slot prefetching
  // the route we're already on. router is stable from useRouter().
  }, [pathname, router]);
  // Debug hooks for the desktop multi-window acceptance runs, which drive
  // the renderer over CDP and therefore can only reach the stores through
  // `window`. Not read by any application code.
  useEffect(() => {
    window.__centerTabs = useCenterTabs;
    import("@/lib/desktop-bridge").then((m) => {
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

  // Sync `.active` on sidebar nav items to the current route and close any
  // open popover when navigating. The functions page inline script also sets
  // `.active` on mount, but nothing removes it on navigation — we own that.
  useEffect(() => {
    const items: Array<[string, string]> = [
      ["navAbility", "/programs"],
      ["navHistory", "/chats"],
      ["navScheduler", "/scheduler"],
    ];
    for (const [id, path] of items) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("active", pathname === path);
    }
    // Remember the last chat route so a `/functions → run` hand-off can
    // return to the conversation the user came from, not a blank /chat.
    // It must survive leaving the chat route entirely (the store's
    // currentSessionId is nulled then), hence its own module.
    if (isChatRoute(pathname)) setLastChatPath(pathname);

    // The web-preview PiP is bound to the chat view. Leaving the chat
    // route must collapse it (tabId → backgroundTabId) instead of only
    // unmounting the component: otherwise the stale store state makes it
    // pop back up on return, and on desktop the native WebContentsView
    // can linger over the new page.
    if (!isChatRoute(pathname) && useWebTabPip.getState().tabId) {
      useWebTabPip.getState().hide();
    }

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
  }, [pathname, router, t]);

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

    // First-mount init: kick off the CDN libs (marked / KaTeX). Everything
    // else is a React component now, so nothing else is injected here.
    loadExternalLibs();

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
    if (desktopBridge()) {
      document.documentElement.classList.add("is-desktop");
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
  const setSplitRatio = useCenterTabs((s) => s.setSplitRatio);
  const centerBodyRef = useRef<HTMLDivElement | null>(null);
  const [centerBodyWidth, setCenterBodyWidth] = useState(0);
  useEffect(() => {
    const node = centerBodyRef.current;
    if (!node) return;
    const measureScheduler = createSplitLayoutMeasureScheduler(() => {
      setCenterBodyWidth(node.getBoundingClientRect().width);
    });
    const layoutRoot = node.closest(".app");
    measureScheduler.schedule();
    const observer = new ResizeObserver(measureScheduler.schedule);
    observer.observe(node);
    window.addEventListener("resize", measureScheduler.schedule);
    layoutRoot?.addEventListener("transitionend", measureScheduler.schedule);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measureScheduler.schedule);
      layoutRoot?.removeEventListener(
        "transitionend",
        measureScheduler.schedule,
      );
      measureScheduler.cancel();
    };
  }, []);
  const splitAvailable = isSplitLayoutAvailable(centerBodyWidth);
  const effectiveSplitRatio = clampSplitRatioForWidth(
    splitRatio,
    centerBodyWidth,
  );
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
  const panes = activeGroup && splitAvailable ? compoundPanes : focusedPanes;
  const showDivider = panes.length === 2;
  const sessionPaneIndex = panes.findIndex((pane) => pane.kind === "session");
  useEffect(() => {
    setDesktopSplitLayoutAvailable(
      isDesktop && activeKind === "session" && splitAvailable,
    );
  }, [isDesktop, activeKind, splitAvailable]);

  function updateSplitRatio(ratio: number) {
    const rect = centerBodyRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    setSplitRatio(clampSplitRatioForWidth(ratio, rect.width));
  }

  function centerPaneClassName(index: number) {
    if (!showDivider) return "center-single-pane";
    return index === 0 ? "center-split-primary" : "center-split-secondary";
  }

  function centerPaneStyle(index: number) {
    if (!showDivider) return undefined;
    return index === 0
      ? { order: 0, width: `${effectiveSplitRatio * 100}%` }
      : { order: 2 };
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
        />
      );
    }
    if (tab.kind === "file" && tab.projectId && tab.path) {
      return <FileTabPane projectId={tab.projectId} path={tab.path} />;
    }
    if (tab.kind === "web") {
      return <WebTabPane tabId={tab.id} url={tab.url ?? ""} />;
    }
    if (tab.kind === "ntp") return <NewTabPage />;
    if (tab.kind === "builtin" && tab.page) {
      if (tab.page === "browser") return <BrowserHomePage />;
      if (tab.page === "files") return <FilesPage />;
      if (tab.page === "terminal") return <TerminalPage preset="shell" />;
      if (tab.page === "claude") return <TerminalPage preset="claude" />;
      return <BuiltinTabPane page={tab.page} />;
    }
    return null;
  }

  const showChat = isChatRoute(pathname);
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
         row across the top with the macOS traffic lights at its left end
         (Chrome-style). Always visible, even on non-chat routes. The strip
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
      {/* Center column: browser-style tab strip over the active tab's
         pane. `order: 2` slots it where `.app .main` used to sit (the
         chat .main is now a grandchild, flexing inside .center-body).
         Hidden (not unmounted) on non-chat routes. */}
      <div
        className="center-col"
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
          {showChat && showDivider ? (
            <div
              className="center-split-divider"
              style={{ order: 1 }}
              role="separator"
              aria-orientation="vertical"
              aria-valuenow={Math.round(effectiveSplitRatio * 100)}
              tabIndex={0}
              onPointerDown={(e) => {
                if (e.button !== 0) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                e.preventDefault();
              }}
              onPointerMove={(e) => {
                if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
                const rect = centerBodyRef.current?.getBoundingClientRect();
                if (!rect || rect.width <= 0) return;
                updateSplitRatio((e.clientX - rect.left) / rect.width);
              }}
              onPointerUp={(e) => {
                if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                  e.currentTarget.releasePointerCapture(e.pointerId);
                }
              }}
              onKeyDown={(e) => {
                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                e.preventDefault();
                updateSplitRatio(
                  effectiveSplitRatio + (e.key === "ArrowLeft" ? -0.02 : 0.02),
                );
              }}
            />
          ) : null}
          {showChat
            ? panes.map((pane, index) => {
                if (pane.kind === "session") return null;
                const content = renderTabPane(pane.tabId, pane.kind);
                if (!content) return null;
                return (
                  <div
                    key={pane.key}
                    className={centerPaneClassName(index)}
                    style={centerPaneStyle(index)}
                  >
                    {content}
                  </div>
                );
              })
            : null}
          {showChat ? <WebTabPip /> : null}
        </div>
      </div>
      {/* Non-chat routes render their own page content via the router. */}
      {!showChat && children}
      {/* Right sidebar — persistent across conversations. Hidden (not
         unmounted) on non-chat routes so its state survives. */}
      <div style={{ display: showChat ? "contents" : "none" }}>
        <RightSidebar />
      </div>
      {composerMount && createPortal(<FocusedComposer />, composerMount)}
      {welcomeMount && createPortal(<WelcomeScreen />, welcomeMount)}
      {messagesMount && createPortal(<MessageList />, messagesMount)}
      {/* Headless — keeps the legacy topbar-updater wrappers installed
         (status / branch / agent state → zustand store) now that the
         visible topbar row is gone. Must live outside the chat-route
         conditional so the wrappers survive route switches. */}
      <LegacyTopbarBridge />
      {/* App-wide transient toasts — mounted at the shell so they show on
         every route (chat AND settings), not just where the TopBar is. */}
      <ToastHost />
      </div>
    </div>
  );
}
