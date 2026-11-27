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
 * The app is Framework7's (framework7.io, its iOS theme): its View holds
 * the pages and plays their push, Back and swipe back; the pages are its
 * Page, Navbar, List, Sheet and the rest, drawn as it draws them. The
 * View follows the address (phone-router.ts). The chat page's shell stays
 * mounted out of sight: it owns the connection and the load of the
 * conversation in the address (page-shell.tsx), as it does for the split
 * view. Peers: Happy, Paseo, the Claude and ChatGPT apps
 * (docs/reference/design/ui/phone-app.html).
 */

import "framework7/css/bundle";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import Framework7 from "framework7/lite-bundle";
import Framework7React, { App, Navbar, Page, View, f7ready } from "framework7-react";

import { PageShell } from "@/components/page-shell";
import { useTranslation } from "@/lib/i18n";
import { prefetchAgents } from "@/lib/agents/agents-query";
import { PICKER_QUERY_KEY } from "@/lib/models/provider-state";
import { api } from "@/lib/net/api";
import { getQueryClient } from "@/lib/query-client";
import { reopenRails, railChosenFrom } from "@/components/ui/secondary-rail";
import { PhoneRouteContext, type PhoneRouteBack } from "@/lib/layout/phone-route";

import { PhoneConversation } from "./phone-conversation";
import { PhoneDetails } from "./phone-details";
import { PhoneHome } from "./phone-home";
import { type DetailsPage } from "./phone-nav";
import { attachRouter, backTo, goBack, here, startPhoneRouter, usePageUrl } from "./phone-router";

// Framework7's React plugin, installed once (`use` here is Framework7's
// plugin call, not a React hook).
const installFramework7 = Framework7.use.bind(Framework7);
installFramework7(Framework7React);

const DETAILS_PAGES: DetailsPage[] = ["changes", "files", "activity", "resources", "step"];

type RouteProps = { f7route: { url: string } };

/** The page Next's router drew for the address (Settings, Agents …). */
const RouteChildren = createContext<ReactNode>(null);

function HomeRoute({ f7route }: RouteProps) {
  const url = new URL(usePageUrl(f7route), "http://x");
  return <PhoneHome projectId={url.searchParams.get("project")} />;
}

function SessionRoute({ f7route }: RouteProps) {
  const url = new URL(usePageUrl(f7route), "http://x");
  const match = url.pathname.match(/^\/s\/([^/]+)/);
  const sessionId = match ? decodeURIComponent(match[1]) : null;
  const details = url.searchParams.get("details");
  if (sessionId && details !== null) {
    return <PhoneDetails sessionId={sessionId} page={DETAILS_PAGES.includes(details as DetailsPage) ? details as DetailsPage : null} />;
  }
  return <PhoneConversation sessionId={sessionId} />;
}

/** Settings, Agents and the other pages: Framework7's bar over the page,
 *  whose own header leaves out its bar (lib/layout/phone-route.ts). On a
 *  list's content page, Back names the list and goes back to it. */
function NextRoute() {
  const { text } = useTranslation();
  const children = useContext(RouteChildren);
  const [back, setBack] = useState<PhoneRouteBack | null>(null);
  const toList = () => {
    if (!back) return;
    const from = railChosenFrom();
    if (from && from !== here()) goBack();
    else back.open();
  };
  return (
    <Page pageContent={false} className="phone-route-page">
      {back ? (
        // Back to the list is the page's own: the View stays on this page.
        <Navbar backLink={back.label} backLinkUrl="#" onBackClick={toList} />
      ) : (
        <Navbar {...backTo(text("Projects", "项目"))} />
      )}
      <PhoneRouteContext.Provider value={setBack}>
        <div className="phone-route-body" data-not-f7>{children}</div>
      </PhoneRouteContext.Provider>
    </Page>
  );
}

const ROUTES = [
  { path: "/", component: HomeRoute },
  { path: "/chats", component: HomeRoute },
  { path: "/chat", component: SessionRoute },
  { path: "/s/:id", component: SessionRoute },
  { path: "(.*)", component: NextRoute },
];

/** The app's theme mode, which Framework7 takes as its `dark` class. */
function useDarkMode(): boolean {
  const read = () => document.documentElement.dataset.themeMode === "dark";
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme-mode"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** The app opens on the list: the address a browser or the app starts at
 *  (/chat, a New chat) becomes /chats once, before anything else. */
let opened = false;
function openOnce(): string {
  if (!opened) {
    opened = true;
    if (window.location.pathname === "/chat") window.history.replaceState(null, "", "/chats");
    startPhoneRouter();
    // What the composer's settings sheet names (models, Agents) is read
    // at start, so a sheet opened at once shows names and every row
    // instead of ids and blanks.
    const client = getQueryClient();
    void client?.prefetchQuery({ queryKey: PICKER_QUERY_KEY, queryFn: api.listPickerModels }).catch(() => {});
    void prefetchAgents();
  }
  return here();
}

export function PhoneApp({ children }: { children: ReactNode }) {
  const [url] = useState(openOnce);
  const dark = useDarkMode();

  // Going back in history (Back, or the edge swipe) returns a page to its
  // list. Listened for here, not by the page: going back remounts the page
  // during the event, and a listener removed then is not called.
  useEffect(() => {
    window.addEventListener("popstate", reopenRails);
    return () => window.removeEventListener("popstate", reopenRails);
  }, []);

  return (
    <>
      <div className="phone-app-engine" aria-hidden="true">
        <PageShell page="chat" />
      </div>
      <RouteChildren.Provider value={children}>
        <App
          theme="ios"
          dark={dark}
          className={dark ? "phone-app dark" : "phone-app"}
          routes={ROUTES}
          // Links the app draws itself (the transcript's, Next's own
          // pages', menus outside the app) are left to the browser and to
          // React; Framework7 routes only its own.
          clicks={{ externalLinks: "a.external, [data-not-f7] a, a:not(.framework7-root a)" }}
        >
          <View
            main
            url={url}
            browserHistory={false}
            onViewInit={(view) => { if (view) f7ready(() => attachRouter(view.router)); }}
          />
        </App>
      </RouteChildren.Provider>
    </>
  );
}
