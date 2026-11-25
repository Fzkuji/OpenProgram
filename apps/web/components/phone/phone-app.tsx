"use client";

/**
 * OpenProgram on a touch phone: a phone app's structure instead of the
 * desktop's window. AppShell renders it in place of the sidebar, tab strip
 * and right panel when `useTouchPhone()` holds (lib/layout/phone.ts).
 *
 *   Projects (/chats) → a project's chats (/chats?project=<id>)
 *     → a conversation (/s/<id>, a new one at /chat)
 *     → its details (?details) → a panel of the details (?details=files …)
 *   every other route: a page with Back over the list
 *
 * The model, effort, permission and tools of a turn are in one sheet from
 * the composer (composer/controls/phone-composer-bar.tsx). The chat page's
 * shell stays mounted out of sight: it owns the connection and the load of
 * the conversation in the address (page-shell.tsx), as it does for the
 * split view. Peers: Happy, Paseo, the Claude and ChatGPT apps
 * (docs/reference/design/ui/phone-app.html).
 */

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { usePathname, useSearchParams } from "next/navigation";

import { PageShell } from "@/components/page-shell";
import { useTranslation } from "@/lib/i18n";

import { PhoneConversation } from "./phone-conversation";
import { PhoneDetails } from "./phone-details";
import { PhoneHome } from "./phone-home";
import type { DetailsPage } from "./phone-nav";
import { PhoneNavBar } from "./phone-nav-bar";
import { useEdgeSwipeBack } from "./use-edge-swipe-back";
import { prefetchAgents } from "@/lib/agents/agents-query";
import { PICKER_QUERY_KEY } from "@/lib/models/provider-state";
import { api } from "@/lib/net/api";
import { getQueryClient } from "@/lib/query-client";
import { useLeavingCopy, usePageEntry } from "./use-page-transitions";

const DETAILS_PAGES: DetailsPage[] = ["changes", "files", "activity", "resources", "step"];

/** The app opens on the list: the address a browser or the app starts at
 *  (/chat, a New chat) becomes /chats once, before anything else. */
let opened = false;

export function PhoneApp({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const { text } = useTranslation();
  const root = useRef<HTMLDivElement>(null);
  const atRoot = (pathname === "/chats" || pathname === "/") && !params.get("project");
  useEdgeSwipeBack(root, !atRoot);
  // The entry moves the moment the history does, which can be a render
  // before the router hands over the new address: the slot changes only
  // once the address it draws is the one the history is on, so a new page
  // never mounts showing the old one.
  const moved = usePageEntry();
  const shown = useRef({ entry: 0, url: "" });
  const url = pathname + "?" + params.toString();
  if (moved !== shown.current.entry && typeof window !== "undefined"
    && url === window.location.pathname + "?" + new URLSearchParams(window.location.search).toString()) {
    shown.current = { entry: moved, url };
  }
  const entry = shown.current.entry;

  useEffect(() => {
    if (opened) return;
    opened = true;
    // What the composer's settings sheet names (models, Agents) is read
    // at start, so a sheet opened at once shows names and every row
    // instead of ids and blanks.
    const client = getQueryClient();
    void client?.prefetchQuery({ queryKey: PICKER_QUERY_KEY, queryFn: api.listPickerModels }).catch(() => {});
    void prefetchAgents();
    if (window.location.pathname === "/chat") window.history.replaceState(null, "", "/chats");
  }, []);

  const session = pathname.match(/^\/s\/([^/]+)/);
  const sessionId = session ? decodeURIComponent(session[1]) : null;
  const details = params.get("details");
  const page: ReactNode = sessionId && details !== null
    ? <PhoneDetails sessionId={sessionId} page={DETAILS_PAGES.includes(details as DetailsPage) ? details as DetailsPage : null} />
    : sessionId || pathname === "/chat"
      ? <PhoneConversation sessionId={sessionId} />
      : pathname === "/chats" || pathname === "/"
        ? <PhoneHome projectId={params.get("project")} />
        : (
          <div className="phone-app-page phone-route-page">
            <PhoneNavBar back={text("Projects", "项目")} />
            <div className="phone-app-route">{children}</div>
          </div>
        );

  return (
    <div className="phone-app" ref={root}>
      <div className="phone-app-engine" aria-hidden="true">
        <PageShell page="chat" />
      </div>
      {/* Keyed by the history entry: each page pushed or returned to is its
          own element (so a page slid away by the swipe back is never reused),
          while an address replaced in place keeps its page. */}
      <PageSlot key={entry} root={root}>{page}</PageSlot>
    </div>
  );
}

/** One page. Its box is the page's own (`display: contents`); as it
 *  leaves, a copy of it plays the slide out (use-page-transitions.ts). */
function PageSlot({ children, root }: { children: ReactNode; root: RefObject<HTMLDivElement | null> }) {
  const slot = useRef<HTMLDivElement>(null);
  useLeavingCopy(slot, root);
  return <div className="phone-app-slot" ref={slot}>{children}</div>;
}
