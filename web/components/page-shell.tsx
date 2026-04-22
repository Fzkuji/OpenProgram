"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

type Page = "chat" | "settings" | "programs" | "chats";

const PAGE_HTML: Record<Page, string> = {
  chat: "/html/index.html",
  settings: "/html/settings.html",
  programs: "/html/programs.html",
  chats: "/html/chats.html",
};

// Only page-specific scripts — shared ones (state/helpers/sidebar/providers/
// settings/ui/scrollbar) are loaded once by AppShell.
const JS_FILES_BY_PAGE: Record<Page, string[]> = {
  chat: [
    "chat/tree.js", "chat/tree-render.js", "chat/tree-retry.js", "chat/tree-log.js",
    "chat/chat.js", "chat/chat-ws.js", "chat/workdir.js",
    "chat/message-actions.js", "chat/message-actions-edit.js", "chat/message-actions-nav.js",
    "chat/init.js",
  ],
  settings: [],
  programs: ["programs/programs.js"],
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

        // Wait for AppShell's shared JS to finish loading before running
        // page-specific scripts (which depend on globals like renderConversations,
        // loadProviders, escHtml, etc.).
        const w = window as unknown as { __sharedScriptsReady?: Promise<void> };
        if (w.__sharedScriptsReady) await w.__sharedScriptsReady;
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
      const w = window as unknown as {
        ws?: WebSocket;
        reconnectTimer?: ReturnType<typeof setTimeout> | null;
        _elapsedTimer?: ReturnType<typeof setInterval> | null;
      };
      try {
        if (w.ws) {
          w.ws.onclose = null;
          w.ws.close();
        }
      } catch {
        // ignore
      }
      if (w.reconnectTimer) {
        clearTimeout(w.reconnectTimer);
        w.reconnectTimer = null;
      }
      if (w._elapsedTimer) {
        clearInterval(w._elapsedTimer);
        w._elapsedTimer = null;
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
  //   * new chat (/chat) — call newConversation() which resets the
  //     chat area in place (welcome screen + cleared state)
  useEffect(() => {
    if (page !== "chat") return;
    const w = window as unknown as {
      ws?: WebSocket;
      currentConvId?: string | null;
      conversations?: Record<string, unknown>;
      renderConversationMessages?: (c: unknown) => void;
      newConversation?: () => void;
    };
    const m = pathname.match(/^\/c\/([^/]+)/);
    const target = m ? m[1] : null;
    if (w.currentConvId === target) return;
    w.currentConvId = target;

    if (target === null) {
      // /chat — reset in-place (welcome screen, clear messages, state).
      if (w.newConversation) w.newConversation();
      return;
    }

    // Optimistic render from cache — snaps the UI instantly; the WS
    // reply below still overwrites with the authoritative snapshot.
    const cached = w.conversations?.[target];
    if (cached && w.renderConversationMessages) {
      try { w.renderConversationMessages(cached); } catch {}
    }
    if (w.ws && w.ws.readyState === WebSocket.OPEN) {
      w.ws.send(JSON.stringify({
        action: "load_conversation",
        conv_id: target,
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
