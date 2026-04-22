"use client";

import { useEffect, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { PageShell } from "./page-shell";

// Scripts shared by every page — loaded once on shell mount and kept alive for
// the whole session. Page-specific scripts live in PageShell. Files sit in
// web/public/js/shared/ so the static tree groups them together.
const SHARED_JS = [
  "shared/state.js",
  "shared/helpers.js",
  "shared/sidebar.js",
  "shared/conversations.js",
  "shared/programs-panel.js",
  "shared/fn-form.js",
  "shared/providers.js",
  "shared/settings.js",
  "shared/settings-providers.js",
  "shared/settings-wizard.js",
  "shared/settings-general.js",
  "shared/ui.js",
  "shared/scrollbar.js",
  // Right sidebar scripts are shared so the panel survives navigation
  // between chat conversations (PageShell remounts per pathname but
  // the right sidebar DOM lives on AppShell, above the remount).
  "shared/right-dock.js",
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
  return pathname === "/chat" || pathname.startsWith("/c/");
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const sidebarRef = useRef<HTMLDivElement>(null);
  const rightSidebarRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const pathname = usePathname();

  // Sync `.active` on sidebar nav items to the current route and close any
  // open popover when navigating. The programs page inline script also sets
  // `.active` on mount, but nothing removes it on navigation — we own that.
  useEffect(() => {
    const items: Array<[string, string]> = [
      ["navPrograms", "/programs"],
      ["navChats", "/chats"],
    ];
    for (const [id, path] of items) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("active", pathname === path);
    }
    const close = (window as unknown as { _closeAllPopovers?: () => void })._closeAllPopovers;
    if (close) close();

    // New chat route (/chat, no conv_id): clear the persisted History
    // graph so the user doesn't see a stale DAG from whatever
    // conversation they were just on. /c/:id loads its own graph via
    // `load_conversation` → conversations.js → renderHistoryGraph.
    if (pathname === "/chat") {
      const render = (window as unknown as {
        renderHistoryGraph?: (g: unknown[], h: string | null) => void;
      }).renderHistoryGraph;
      if (render) render([], null);
    }
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

    // First-mount init: inject sidebar HTML + load external libs + shared JS.
    const w = window as unknown as { __sharedScriptsReady?: Promise<void> };
    if (!w.__sharedScriptsReady) {
      w.__sharedScriptsReady = (async () => {
        loadStylesheet(
          "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css"
        );

        // Kick off all network fetches in parallel: sidebar HTML, 3 CDN libs,
        // and 13 inline scripts. Serial `await` in the old version made this
        // ~13 sequential round trips on every hard refresh.
        const sidebarP = fetch("/html/_sidebar.html").then((r) => r.text());
        const externalsP = Promise.all(EXTERNAL_LIBS.map(loadExternalScript));
        const inlineSources = SHARED_JS.map((name) => `/js/${name}`);
        const inlineFetches = await Promise.all(inlineSources.map(fetchInlineScript));

        // Execute inline scripts in declared order to preserve global
        // dependencies (state.js defines vars other scripts reference).
        for (let i = 0; i < inlineSources.length; i++) {
          const f = inlineFetches[i];
          if (f) injectInlineScript(f.src, f.code);
        }

        const sidebarHtml = await sidebarP;
        if (sidebarRef.current) sidebarRef.current.innerHTML = sidebarHtml;
        // Right sidebar fetched + injected alongside the left. Kept on
        // AppShell so chat routes see one persistent instance across
        // conversation switches.
        const rightHtml = await fetch("/html/_right-sidebar.html").then((r) => r.text());
        if (rightSidebarRef.current) rightSidebarRef.current.innerHTML = rightHtml;
        await externalsP;
      })();
    } else {
      if (sidebarRef.current && !sidebarRef.current.innerHTML) {
        fetch("/html/_sidebar.html")
          .then((r) => r.text())
          .then((html) => {
            if (sidebarRef.current) sidebarRef.current.innerHTML = html;
          });
      }
      if (rightSidebarRef.current && !rightSidebarRef.current.innerHTML) {
        fetch("/html/_right-sidebar.html")
          .then((r) => r.text())
          .then((html) => {
            if (rightSidebarRef.current) rightSidebarRef.current.innerHTML = html;
          });
      }
    }

    return () => {
      document.removeEventListener("click", onClick);
    };
  }, [router]);

  const showChat = isChatRoute(pathname);
  return (
    <div className="app">
      <div ref={sidebarRef} style={{ display: "contents" }} />
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
      {/* Right sidebar — persistent across conversations. */}
      <div
        ref={rightSidebarRef}
        style={{ display: showChat ? "contents" : "none" }}
      />
    </div>
  );
}
