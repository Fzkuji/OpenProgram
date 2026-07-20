"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, usePathname } from "next/navigation";
import { PageShell } from "./page-shell";
import { Sidebar } from "./sidebar/sidebar";
import { RightSidebar } from "./right-sidebar/right-sidebar";
import { CenterTabStrip } from "./center-tabs/center-tab-strip";
import { BuiltinTabPane } from "./center-tabs/builtin-tab-pane";
import { FileTabPane } from "./center-tabs/file-tab-pane";
import { NewTabPage } from "./center-tabs/new-tab-page";
import { WebTabPane } from "./center-tabs/web-tab-pane";
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
import { useSessionStore } from "@/lib/session-store";
import { applyChatWsMessage, appendLocalUserTurn } from "@/lib/net/chat-stream";
import { convToChatMsgs } from "@/lib/conv-mapper";
import { useColResize } from "@/lib/use-col-resize";
import { useTranslation } from "@/lib/i18n";
import {
  clampSplitRatioForWidth,
  createSplitLayoutMeasureScheduler,
  isSplitLayoutAvailable,
} from "@/lib/split-layout";
// Migrated legacy modules — imported for side effects (they install
// their `window.*` bridges for the still-legacy scripts).
import "@/lib/runtime-bridge/state";
import "@/lib/runtime-bridge/helpers";
import "@/lib/runtime-bridge/ui";
import "@/lib/runtime-bridge/providers";
import "@/lib/runtime-bridge/functions-panel";
import "@/lib/runtime-bridge/dag";
import { initOverlayScrollbars } from "@/lib/runtime-bridge/scrollbar";

// Scripts shared by every page — loaded once on shell mount and kept alive for
// the whole session. Page-specific scripts live in PageShell. Files sit in
// web/public/js/shared/ so the static tree groups them together.
// Legacy shared JS modules. We keep loading the ones that the
// not-yet-migrated chat page + sidebar + right rail still depend on;
// the settings/functions/chats trios are gone (migrated to React).
// `shared/conversations.js` is migrated — see `web/lib/conversations.ts`
// (imported for side effects by `useWS`).
// All legacy public/js scripts are migrated to lib/ — see the
// `import "@/lib/..."` side-effect imports above.
const SHARED_JS: string[] = [];

const EXTERNAL_LIBS = [
  "https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js",
];

function loadExternalScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = Array.from(document.scripts).find(
      (s) => s.getAttribute("data-src") === src
    );
    if (existing) {
      resolve();
      return;
    }
    const el = document.createElement("script");
    el.src = src;
    el.async = false;
    el.setAttribute("data-app-script", "1");
    el.setAttribute("data-src", src);
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(el);
  });
}

async function fetchInlineScript(src: string): Promise<{ src: string; code: string } | null> {
  const w = window as unknown as { __scriptsLoaded?: Set<string> };
  if (!w.__scriptsLoaded) w.__scriptsLoaded = new Set<string>();
  if (w.__scriptsLoaded.has(src)) return null;
  const res = await fetch(src, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch ${src}: ${res.status}`);
  return { src, code: await res.text() };
}

function injectInlineScript(src: string, code: string) {
  const w = window as unknown as { __scriptsLoaded?: Set<string> };
  if (!w.__scriptsLoaded) w.__scriptsLoaded = new Set<string>();
  if (w.__scriptsLoaded.has(src)) return;
  const s = document.createElement("script");
  s.setAttribute("data-app-script", "1");
  s.setAttribute("data-src", src);
  s.text = code + `\n//# sourceURL=${src}\n`;
  document.head.appendChild(s);
  w.__scriptsLoaded.add(src);
}

function loadStylesheet(href: string) {
  const existing = Array.from(document.styleSheets).find(
    (s) => s.href === href
  );
  if (existing) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

declare global {
  interface Window {
    __sharedScriptsReady?: Promise<void>;
    __navigate?: (path: string) => void;
  }
}

// Routes where the right sidebar (History / Execution Detail) is
// relevant. Functions / Chats / Settings don't need it, so it's hidden
// there even though the DOM persists.
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
      "/settings/providers",
      "/functions",
      "/skills",
      "/settings/general",
      "/memory",
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
  // Expose the React store to the legacy JS scripts so they can write
  // through to it. Each legacy caller that touches React-owned state
  // (setWelcomeVisible, welcome example clicks once migrated, etc.)
  // goes through useSessionStore.getState(); this single global is
  // their access point. Removed once every legacy caller is migrated.
  useEffect(() => {
    interface ConvLike { id?: string; messages?: unknown[] }
    const w = window as unknown as {
      __sessionStore?: unknown;
      __applyChatWsMessage?: unknown;
      __appendLocalUserTurn?: unknown;
      __feedStoreFromConv?: unknown;
    };
    w.__sessionStore = useSessionStore;
    // Test hooks for desktop multi-window acceptance (driven over CDP).
    (w as Record<string, unknown>).__centerTabs = useCenterTabs;
    import("@/lib/desktop-bridge").then((m) => {
      (w as Record<string, unknown>).__desktopTransfer = {
        desktopBridge: m.desktopBridge,
        buildTransferPayload: m.buildTransferPayload,
        stageIncomingTransfer: m.stageIncomingTransfer,
        placementForDropIntent: m.placementForDropIntent,
        acceptedTransfers: m.acceptedTransfers,
      };
    });
    // Phase 3 bridge — legacy chat JS feeds the React message store
    // through these globals. Dormant until the MessageList portal is
    // mounted; populating the store in parallel is a no-op for the
    // still-live legacy DOM renderer.
    w.__applyChatWsMessage = (msg: { type: string; data?: unknown }) =>
      applyChatWsMessage(msg);
    w.__appendLocalUserTurn = (
      sessionId: string,
      msgId: string,
      text: string,
      display?: "runtime" | "normal",
    ) => appendLocalUserTurn(sessionId, msgId, text, display);
    w.__feedStoreFromConv = (conv: ConvLike) => {
      if (!conv || !conv.id) return;
      useSessionStore
        .getState()
        .setMessages(
          conv.id,
          convToChatMsgs((conv.messages as never[]) || []),
        );
    };
    return () => {
      delete w.__sessionStore;
      delete w.__applyChatWsMessage;
      delete w.__appendLocalUserTurn;
      delete w.__feedStoreFromConv;
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
      ["navPrograms", "/functions"],
      ["navMemory", "/memory"],
      ["navChats", "/chats"],
    ];
    for (const [id, path] of items) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("active", pathname === path);
    }
    const close = (window as unknown as { _closeAllPopovers?: () => void })._closeAllPopovers;
    if (close) close();

    // Remember the last chat route so a `/functions → run` hand-off can
    // return to the conversation the user came from, not a blank
    // /chat. Kept on window because it must survive leaving the chat
    // route entirely (the store's currentSessionId is nulled then).
    if (isChatRoute(pathname)) {
      (window as unknown as { __lastChatPath?: string }).__lastChatPath =
        pathname;
    }

    // Keep legacy `window.currentSessionId` in lockstep with the
    // Next.js client route. Legacy `init.js` parses the URL exactly
    // once at script load; SPA navigations between sessions don't
    // re-run it, so a click on a sidebar conv updates the URL but
    // `currentSessionId` stays pinned at whatever the previous
    // ``chat_ack`` last wrote. The legacy model picker reads that
    // bare global and was sending ``session_id`` of the OLD conv to
    // ``/api/model`` — every picker click silently switched the
    // wrong conversation.
    try {
      const m = pathname.match(/^\/s\/([^/]+)/);
      const sid = m ? m[1] : null;
      (window as unknown as { currentSessionId?: string | null }).currentSessionId = sid;
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
      const sock = (window as unknown as { ws?: WebSocket }).ws;
      if (sock && sock.readyState === WebSocket.OPEN) {
        sock.send(JSON.stringify({ action: "mark_session_read", session_id: sid }));
      }
    } catch {
      /* ignore */
    }

    // New chat route (/chat, no session_id): clear the persisted History
    // graph + Execution Detail panel so the user doesn't see stale
    // content from whatever conversation they were just on. /c/:id
    // reloads both via `load_session` → conversations.js →
    // renderHistoryGraph + subsequent node click → showDetail.
    if (pathname === "/chat") {
      const render = (window as unknown as {
        renderHistoryGraph?: (g: unknown[], h: string | null) => void;
      }).renderHistoryGraph;
      if (render) render([], null);

      // Execution Detail panel lives in the right sidebar. Reset it to
      // the empty-state placeholder the HTML template ships with. If
      // the DOM hasn't mounted yet (first render), the template
      // already has the empty state, so skipping is harmless.
      const detailBody = document.getElementById("detailBody");
      if (detailBody) {
        detailBody.innerHTML =
          `<div class="detail-empty">${t("right.no_execution")}<br/>` +
          `<span>${t("right.no_execution_hint")}</span></div>`;
      }
      const detailTitle = document.getElementById("detailTitle");
      if (detailTitle) detailTitle.textContent = "";
    }
    // The React `<Sidebar />` renders nav items synchronously on mount,
    // so depending on `pathname` alone is sufficient now — no need to
    // wait for an async HTML inject before the first .active sync.
  }, [pathname, router, t]);

  useEffect(() => {
    // Expose a client-side navigation helper vanilla JS can call instead of
    // `window.location.href = ...` (which would full-reload and kill the shell).
    window.__navigate = (path: string) => router.push(path);

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

    // First-mount init: load external libs + shared JS. Both the left
    // sidebar and the right sidebar are real React components now
    // (`<Sidebar />` / `<RightSidebar />` below), so no `_*.html`
    // fetch+inject is needed at this stage.
    const w = window as unknown as { __sharedScriptsReady?: Promise<void> };
    if (!w.__sharedScriptsReady) {
      w.__sharedScriptsReady = (async () => {
        loadStylesheet(
          "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css"
        );

        // Kick off all network fetches in parallel: 3 CDN libs + inline
        // scripts. Serial `await` in the old version made this ~13
        // sequential round trips on every hard refresh.
        const externalsP = Promise.all(EXTERNAL_LIBS.map(loadExternalScript));
        const inlineSources = SHARED_JS.map((name) => `/js/${name}`);
        const inlineFetches = await Promise.all(inlineSources.map(fetchInlineScript));

        // Execute inline scripts in declared order to preserve global
        // dependencies (state.js defines vars other scripts reference).
        for (let i = 0; i < inlineSources.length; i++) {
          const f = inlineFetches[i];
          if (f) injectInlineScript(f.src, f.code);
        }

        await externalsP;
      })();
    }

    return () => {
      document.removeEventListener("click", onClick);
    };
  }, [router]);

  // Column-resize handles (sidebar / right detail). Replaces the IIFE
  // at the bottom of init.js. Each call attaches a `mousedown` listener
  // to the handle and drags the target element's `width`.
  useColResize({
    handleId: "sidebarResize",
    targetId: "sidebar",
    direction: 1,
    minWidth: 180,
  });
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
  const panes =
    isDesktop && activeGroup && splitAvailable
      ? compoundPanes
      : focusedPanes;
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

  function renderTabPane(tabId: string) {
    const tab = tabs.find((candidate) => candidate.id === tabId);
    if (!tab) return null;
    if (tab.kind === "file" && tab.projectId && tab.path) {
      return <FileTabPane projectId={tab.projectId} path={tab.path} />;
    }
    if (tab.kind === "web") {
      return <WebTabPane tabId={tab.id} url={tab.url ?? ""} />;
    }
    if (tab.kind === "ntp") return <NewTabPage />;
    if (tab.kind === "builtin" && tab.page) {
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
          <CenterTabStrip />
        </div>
      ) : null}
      <div className="app">
      <Sidebar />
      <div className="col-resize" id="sidebarResize"></div>
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
             is what makes the WS + DOM + right sidebar state persist. */}
          <div
            className={
              sessionPaneIndex < 0
                ? "center-single-pane"
                : centerPaneClassName(sessionPaneIndex)
            }
            style={
              sessionPaneIndex < 0
                ? { display: "none" }
                : centerPaneStyle(sessionPaneIndex)
            }
          >
            <PageShell page="chat" />
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
                const content = renderTabPane(pane.tabId);
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
        </div>
      </div>
      {/* Non-chat routes render their own page content via the router. */}
      {!showChat && children}
      {/* Right sidebar — persistent across conversations. Hidden (not
         unmounted) on non-chat routes so its state survives. */}
      <div style={{ display: showChat ? "contents" : "none" }}>
        <RightSidebar />
      </div>
      {composerMount && createPortal(<Composer />, composerMount)}
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
