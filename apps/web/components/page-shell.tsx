"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import { usePendingRunFunction } from "@/lib/use-pending-run-function";
import { useWS } from "@/lib/net/use-ws";
import { runtimeState, getSocket } from "@/lib/runtime-bridge/state";
import { useSessionStore } from "@/lib/session-store";
import {
  newSession,
  renderSessionMessages,
} from "@/lib/runtime-bridge/conversations";
import { showHistorySkeleton } from "@/lib/runtime-bridge/dag/pipeline";

type Page = "chat" | "settings" | "chats";

const PAGE_HTML: Record<Page, string> = {
  chat: "/html/index.html",
  settings: "/html/settings.html",
  chats: "/html/chats.html",
};

// Only page-specific scripts — shared ones (state/helpers/sidebar/providers/
// settings/ui/scrollbar) are loaded once by AppShell.
const JS_FILES_BY_PAGE: Record<Page, string[]> = {
  // Chat WS handlers are TS now — see apps/web/lib/chat-handlers.ts
  // (imported by useWS).
  chat: [],
  settings: [],
  chats: [],
};

// Page-specific scripts are re-executed on every mount — they wire listeners
// to DOM nodes that just got rendered, so caching them would leave dead
// references after navigation. Shared scripts live in AppShell and are
// loaded once.
async function fetchPageScript(src: string): Promise<{ src: string; code: string }> {
  const res = await fetch(src, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch ${src}: ${res.status}`);
  return { src, code: await res.text() };
}

function injectPageScript(src: string, code: string) {
  const s = document.createElement("script");
  s.setAttribute("data-page-script", "1");
  s.setAttribute("data-src", src);
  s.text = code + `\n//# sourceURL=${src}\n`;
  document.head.appendChild(s);
}

function runInlineScript(code: string) {
  const s = document.createElement("script");
  s.setAttribute("data-inline-script", "1");
  s.text = code;
  document.head.appendChild(s);
  s.remove();
}

// Extract the main content area from a page's <body>. AppShell provides the
// outer `<div class="app">` + sidebar + sidebar-resize handle, so we strip
// those from the per-page HTML.
function extractMainArea(bodyHtml: string): { main: string; inlineScripts: string[] } {
  let body = bodyHtml;

  // Pull out scripts first (we execute them separately).
  const inlineScripts: string[] = [];
  body = body.replace(
    /<script\b([^>]*)>([\s\S]*?)<\/script>/gi,
    (_m, attrs: string, content: string) => {
      if (/\bsrc\s*=/i.test(attrs)) return "";
      inlineScripts.push(content);
      return "";
    }
  );
  body = body.replace(/<link[^>]+rel=["']stylesheet["'][^>]*>/gi, "");

  // Strip the outer `<div class="app">` wrapper. The opening tag and the
  // matching close (the last `</div>` of the body) are what AppShell
  // already provides.
  body = body.replace(/<div\s+class=["']app["'][^>]*>/i, "");
  body = body.replace(/<\/div>\s*$/i, "");

  // Strip sidebar placeholder + sidebar-resize handle (also provided by shell).
  body = body.replace(/<!--\s*SIDEBAR\s*-->/gi, "");
  body = body.replace(
    /<div\s+class=["']col-resize["']\s+id=["']sidebarResize["'][^>]*>\s*<\/div>/gi,
    ""
  );
  body = body.replace(
    /<div\s+id=["']sidebarResize["']\s+class=["']col-resize["'][^>]*>\s*<\/div>/gi,
    ""
  );

  return { main: body.trim(), inlineScripts };
}

/* The legacy `.input-area`, `#welcomeScreen`, and `.welcome-examples`
   blocks all contain nested DOM that regex can't reliably balance.
   Strip them post-injection in the DOM and drop in mount placeholders
   so the React portals (<Composer />, <WelcomeScreen />) land in the
   right spots inside #chatView. */
function stripLegacyChatChrome(host: HTMLElement) {
  const inputArea = host.querySelector(".input-area");
  if (inputArea) {
    const mount = document.createElement("div");
    mount.id = "composer-mount";
    inputArea.replaceWith(mount);
  }
  const welcome = host.querySelector("#welcomeScreen");
  if (welcome) welcome.remove();
  const welcomeExamples = host.querySelector("#welcomeExamples");
  if (welcomeExamples) welcomeExamples.remove();
  // Where the welcome screen used to live: inside #chatMessages. The
  // React portal renders into a new placeholder appended there so the
  // logo + buttons sit in roughly the same vertical region.
  const chatMessages = host.querySelector("#chatMessages");
  if (chatMessages) {
    // `#messages-mount` hosts the React <MessageList /> portal — the
    // message stream. `display: contents` lets each rendered bubble
    // become a direct flex child of `#chatMessages`, matching the
    // layout the legacy renderer produced. It sits BEFORE the welcome
    // mount so messages render above the (hidden-when-non-empty)
    // welcome panel.
    const mmount = document.createElement("div");
    mmount.id = "messages-mount";
    mmount.style.display = "contents";
    chatMessages.appendChild(mmount);

    const wmount = document.createElement("div");
    wmount.id = "welcome-mount";
    // `display: contents` makes the mount point invisible to layout —
    // its child (<WelcomeScreen />) becomes a direct flex child of
    // `#chatMessages` so the welcome panel can grow to fill the
    // remaining height (and its bottom-anchored examples row sits
    // right above the composer).
    wmount.style.display = "contents";
    chatMessages.appendChild(wmount);
  }
}

export function PageShell({ page }: { page: Page }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        // Kick off the page HTML fetch AND all page-JS fetches in parallel.
        // The old version awaited HTML first, then fetched scripts serially —
        // chat page has 8 scripts so that was 9 sequential round trips.
        const pageHtmlP = fetch(PAGE_HTML[page]).then((r) => r.text());
        const scriptSources = JS_FILES_BY_PAGE[page].map((n) => `/js/${n}`);
        const scriptFetchesP = Promise.all(scriptSources.map(fetchPageScript));

        const pageHtml = await pageHtmlP;
        if (cancelled) return;

        const bodyMatch = pageHtml.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
        const rawBody = bodyMatch ? bodyMatch[1] : pageHtml;
        const { main, inlineScripts } = extractMainArea(rawBody);

        if (!hostRef.current || cancelled) return;
        hostRef.current.innerHTML = main;
        if (page === "chat") stripLegacyChatChrome(hostRef.current);

        // AppShell used to publish `window.__sharedScriptsReady` from the
        // legacy public/js bundle and this awaited it. That bundle is gone
        // and nothing assigns the promise, so the await was a no-op.
        if (cancelled) return;

        const fetchedScripts = await scriptFetchesP;
        if (cancelled) return;
        // Execute in declared order: init.js must see globals defined by
        // chat.js / chat-ws.js / tree*.js.
        for (const f of fetchedScripts) {
          injectPageScript(f.src, f.code);
        }

        for (const raw of inlineScripts) {
          const code = raw.replace(
            /document\s*\.\s*addEventListener\s*\(\s*['"]DOMContentLoaded['"]\s*,\s*(function\s*\(\s*\)\s*\{[\s\S]*?\})\s*\)/g,
            "($1)()"
          );
          runInlineScript(code);
        }
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    }

    init();

    return () => {
      cancelled = true;
      try {
        const sock = getSocket();
        if (sock) {
          sock.onclose = null;
          sock.close();
        }
      } catch {
        // ignore
      }
      if (runtimeState.reconnectTimer) {
        clearTimeout(runtimeState.reconnectTimer);
        runtimeState.reconnectTimer = null;
      }
      if (runtimeState._elapsedTimer) {
        clearInterval(runtimeState._elapsedTimer);
        runtimeState._elapsedTimer = null;
      }
      if (hostRef.current) hostRef.current.innerHTML = "";
    };
    // Deliberately NOT depending on pathname — all chat routes (/chat,
    // /c/:id) share the same page="chat" HTML, so re-running this
    // effect on every conv-switch would needlessly tear down the WS,
    // wipe the DOM, and rebuild everything. Conv switching is handled
    // in the pathname-keyed effect below, which reuses the existing WS.
  }, [page]);

  // Lightweight path-change handler: on chat pages, when the URL's
  // conv id changes, we reuse the already-open WebSocket + keep the
  // DOM mounted. Two fast paths to avoid WS-roundtrip lag:
  //   * known conv — render from the local cache immediately, server
  //     reply later overwrites with canonical state
  //   * new chat (/chat) — call newSession() which resets the
  //     chat area in place (welcome screen + cleared state)
  // SPA hand-off from /programs → /chat lives in its own hook —
  // see lib/use-pending-run-function.ts.
  usePendingRunFunction(pathname);

  // Own the chat WebSocket lifecycle (slice A of the WS-layer
  // migration). PageShell is only ever instantiated as the chat shell,
  // and mounted once for the session, so this opens exactly one
  // socket. The legacy `init.js` no longer calls `connect()`.
  useWS();

  useEffect(() => {
    if (page !== "chat") return;
    // Only react when the URL is on a chat route. SPA-routing away
    // (e.g. user clicks Functions from /s/<id>) leaves this PageShell
    // mounted for one tick before the new route's PageShell takes
    // over; without this guard, that tick would call newSession()
    // and rewrite the URL back to /chat.
    if (pathname !== "/chat" && !pathname.startsWith("/s/")) return;
    const m = pathname.match(/^\/s\/([^/]+)/);
    const target = m ? m[1] : null;
    if (runtimeState.currentSessionId === target) return;
    runtimeState.currentSessionId = target;

    if (target === null) {
      // /chat — reset in-place (welcome screen, clear messages, state).
      newSession();
      return;
    }

    // Optimistic render from cache — snaps the UI instantly; the WS
    // reply below still overwrites with the authoritative capture.
    // TODO(store-migration): reads the legacy heavy conv (messages/graph)
    // for the legacy renderSessionMessages DOM path — stays on
    // runtimeState.conversations until that renderer moves into the store.
    //
    // Only render when the cache holds a FULL capture (messages
    // present). Sidebar entries are lightweight (no messages) — feeding
    // one to renderSessionMessages wipes the transcript and flashes the
    // welcome screen for a frame. In that case flip to the skeleton
    // state instead and let loadSessionData clear it when the WS reply
    // lands.
    const cached = runtimeState.conversations[target] as
      | { messages?: unknown[] }
      | undefined;
    const store = useSessionStore;
    if (
      cached
      && Array.isArray(cached.messages)
      && cached.messages.length > 0
    ) {
      try { renderSessionMessages(cached as never); } catch {}
      try { store.getState().setTranscriptLoading(null); } catch {}
    } else {
      try {
        store.getState().setTranscriptLoading(target);
        store.getState().setWelcomeVisible(false);
      } catch {}
      // Clear the DAG panel to a skeleton too — otherwise the previous
      // session's graph lingers next to the transcript skeleton.
      try { showHistorySkeleton(); } catch {}
    }
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({
        action: "load_session",
        session_id: target,
      }));
    }
  }, [page, pathname]);

  if (err) {
    return (
      <div className="p-6 text-sm" style={{ color: "var(--accent-red)" }}>
        Page shell failed: {err}
      </div>
    );
  }

  return <div ref={hostRef} style={{ display: "contents" }} />;
}
