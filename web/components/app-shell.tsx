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
import { useSessionStore } from "@/lib/session-store";
import { useColResize } from "@/lib/use-col-resize";

// Scripts shared by every page — loaded once on shell mount and kept alive for
// the whole session. Page-specific scripts live in PageShell. Files sit in
// web/public/js/shared/ so the static tree groups them together.
// Legacy shared JS modules. We keep loading the ones that the
// not-yet-migrated chat page + sidebar + right rail still depend on;
// the settings/programs/chats trios are gone (migrated to React).
const SHARED_JS = [
  "shared/state.js",
  "shared/helpers.js",
  "shared/conversations.js",
  "shared/programs-panel.js",
  "shared/providers.js",
  "shared/ui.js",
  "shared/scrollbar.js",
  // `shared/right-dock.js` is no longer loaded — `<RightSidebar />`
  // owns open/close + view switching now and installs the
  // `window.rightDock` shim itself for any still-vanilla callers.
  "shared/history-graph.js",
];

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
// relevant. Programs / Chats / Settings don't need it, so it's hidden
// there even though the DOM persists.
function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/s/");
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  // Expose the React store to the legacy JS scripts so they can write
  // through to it. Each legacy caller that touches React-owned state
  // (setWelcomeVisible, welcome example clicks once migrated, etc.)
  // goes through useSessionStore.getState(); this single global is
  // their access point. Removed once every legacy caller is migrated.
  useEffect(() => {
    (window as unknown as { __sessionStore?: unknown }).__sessionStore =
      useSessionStore;
    return () => {
      delete (window as unknown as { __sessionStore?: unknown })
        .__sessionStore;
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
  useEffect(() => {
    let cancelled = false;
    setComposerMount(null);
    setWelcomeMount(null);
    setTopbarMount(null);
    function findMounts() {
      const composer = document.getElementById("composer-mount");
      const welcome = document.getElementById("welcome-mount");
      const topbar = document.getElementById("topbar-mount");
      if (cancelled) return false;
      if (composer) setComposerMount(composer);
      if (welcome) setWelcomeMount(welcome);
      if (topbar) setTopbarMount(topbar);
      return !!(composer && welcome && topbar);
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
  // open popover when navigating. The programs page inline script also sets
  // `.active` on mount, but nothing removes it on navigation — we own that.
  useEffect(() => {
    const items: Array<[string, string]> = [
      ["navPrograms", "/programs"],
      ["navMemory", "/memory"],
      ["navChats", "/chats"],
    ];
    for (const [id, path] of items) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("active", pathname === path);
    }
    const close = (window as unknown as { _closeAllPopovers?: () => void })._closeAllPopovers;
    if (close) close();

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
    </div>
  );
}
