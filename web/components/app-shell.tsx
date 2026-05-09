"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { AppSidebar } from "./app-sidebar";
import { ChatView } from "./chat/chat-view";

declare global {
  interface Window {
    __navigate?: (path: string) => void;
  }
}

function isChatRoute(pathname: string) {
  return pathname === "/chat" || pathname.startsWith("/c/");
}

function convIdFromPathname(pathname: string): string | null {
  const m = pathname.match(/^\/c\/([^/]+)/);
  return m ? m[1] : null;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    // Vanilla code (programs.js etc. that the not-yet-migrated parts
    // still use) calls window.__navigate to do client-side routing.
    // Keep the hook so legacy code paths don't full-reload.
    window.__navigate = (path: string) => router.push(path);

    // Intercept clicks on internal anchor tags so they go through the
    // Next.js router instead of full page reload.
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
    return () => document.removeEventListener("click", onClick);
  }, [router]);

  const showChat = isChatRoute(pathname);
  const convId = convIdFromPathname(pathname);

  return (
    <div className="app">
      <AppSidebar />
      {/* Chat shell stays mounted across /chat ↔ /c/:id navigations so
         the WS connection, Zustand store, and any component-local
         state (input draft, scroll position, ...) survive switches.
         Hidden via display:none on non-chat routes — same pattern the
         legacy AppShell used. */}
      <div
        style={{ display: showChat ? "contents" : "none" }}
        key="chat-shell"
      >
        <ChatView convId={convId} />
      </div>
      {!showChat && children}
    </div>
  );
}
