"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, usePathname } from "next/navigation";
import { PageShell } from "./page-shell";
import { Sidebar } from "./sidebar/sidebar";
import { RightSidebar } from "./right-sidebar/right-sidebar";
import { Composer } from "./chat/composer";
import { TopBar } from "./chat/top-bar";
import { WelcomeScreen } from "./chat/welcome-screen";
import { MessageList } from "./chat/messages/message-list";
import { useSessionStore } from "@/lib/session-store";
import { applyChatWsMessage, appendLocalUserTurn } from "@/lib/chat-stream";
import { convToChatMsgs } from "@/lib/conv-mapper";
import { useColResize } from "@/lib/use-col-resize";
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
  const [composerMount, setComposerMount] = useState<HTMLElement | null>(null);
  const [welcomeMount, setWelcomeMount] = useState<HTMLElement | null>(null);
  const [topbarMount, setTopbarMount] = useState<HTMLElement | null>(null);
  const [messagesMount, setMessagesMount] = useState<HTMLElement | null>(null);
  useEffect(() => {
    let cancelled = false;
    setComposerMount(null);
    setWelcomeMount(null);
    setTopbarMount(null);
    setMessagesMount(null);
    function findMounts() {
      const composer = document.getElementById("composer-mount");
      const welcome = document.getElementById("welcome-mount");
      const topbar = document.getElementById("topbar-mount");
      const messages = document.getElementById("messages-mount");
      if (cancelled) return false;
      if (composer) setComposerMount(composer);
      if (welcome) setWelcomeMount(welcome);
      if (topbar) setTopbarMount(topbar);
      if (messages) setMessagesMount(messages);
      return !!(composer && welcome && topbar && messages);
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
      useSessionStore.getState().setCurrentConv(sid);
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
          '<div class="detail-empty">No execution selected.<br/>' +
          "<span>Click a node in the conversation tree to inspect " +
          "its context and output.</span></div>";
      }
      const detailTitle = document.getElementById("detailTitle");
      if (detailTitle) detailTitle.textContent = "";
    }
    // The React `<Sidebar />` renders nav items synchronously on mount,
    // so depending on `pathname` alone is sufficient now — no need to
    // wait for an async HTML inject before the first .active sync.
  }, [pathname]);

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

  const showChat = isChatRoute(pathname);
  return (
    <div className="app">
      <Sidebar />
      <div className="col-resize" id="sidebarResize"></div>
      {/* Chat shell is mounted ONCE at the layout level and kept alive
         across /chat ↔ /c/:id navigations. Hidden (not unmounted) when
         visiting non-chat routes. This is what makes the WS + DOM +
         right sidebar state persist — same pattern as the left sidebar. */}
      <div style={{ display: showChat ? "contents" : "none" }}>
        <PageShell page="chat" />
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
      {topbarMount && createPortal(<TopBar />, topbarMount)}
      {messagesMount && createPortal(<MessageList />, messagesMount)}
    </div>
  );
}
