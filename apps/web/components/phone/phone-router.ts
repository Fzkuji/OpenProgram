"use client";

/**
 * The phone app's pages are Framework7's (phone-app.tsx): its View keeps
 * the stack of pages and plays the push, the Back and the swipe back from
 * the edge. Where the app is stays in the address, as everywhere else in
 * the app: Next's router, the chat's own navigation (openSession, the
 * shallow pushes) and the phone's links all change the history, and the
 * View follows it here:
 *
 *   a push      → the View pushes the page;
 *   going back  → the View goes back to it (to the page it names, when
 *                 that is not the one under the current page);
 *   a replace   → the page stays and only its address changes (a new chat
 *                 getting its id), or, when the address is another kind
 *                 of page, the View shows that page in its place.
 *
 * The other way, Back and the swipe back from the edge are Framework7's
 * own: once the View has gone back, the history goes back one entry too,
 * and the View is already where that entry is.
 */

import { useEffect, useState } from "react";
import type { Router } from "framework7/types";

/** The View's router once it is up. */
let router: Router.Router | null = null;
/** The entries of the history the app has seen, and the one it is on. */
const stack: string[] = [];
let index = -1;
/** The address a going back lands on that the View already shows. */
let expected: string | null = null;
/** A replace that stands for going back (Back on a page opened directly). */
let replaceGoesBack = false;

export const here = () => window.location.pathname + window.location.search;

/** Which page an address is: a replace within the same kind keeps the page. */
export function pageKind(url: string): string {
  const { pathname } = new URL(url, "http://x");
  if (pathname === "/chat" || pathname.startsWith("/s/")) {
    return new URL(url, "http://x").searchParams.has("details") ? "details" : "session";
  }
  if (pathname === "/chats" || pathname === "/") return "home";
  return "route";
}

type Change = { kind: "push" | "back" | "forward" | "replace"; url: string; goesBack?: boolean };

/** Each page's own address, which a replace in place updates. */
const pageUrls = new WeakMap<object, (url: string) => void>();

/** The address of the page this route drew, kept current by replaces. */
export function usePageUrl(route: { url: string }): string {
  const [url, setUrl] = useState(route.url);
  useEffect(() => {
    pageUrls.set(route, setUrl);
    return () => { pageUrls.delete(route); };
  }, [route]);
  return url;
}

function follow(change: Change): void {
  const r = router;
  if (!r) return;
  const current = r.currentRoute?.url;
  if (change.url === current) return;
  if (change.kind === "push" || change.kind === "forward") {
    r.navigate(change.url);
    return;
  }
  if (change.kind === "back") {
    const previous = r.history[r.history.length - 2];
    if (previous === change.url) r.back();
    else r.back(change.url, { force: true });
    return;
  }
  // A replace.
  if (change.goesBack) {
    r.back(change.url, { force: true });
  } else if (current && pageKind(current) === pageKind(change.url)) {
    r.updateCurrentUrl(change.url);
    if (r.currentRoute) pageUrls.get(r.currentRoute)?.(change.url);
  } else {
    r.navigate(change.url, { reloadCurrent: true, animate: false });
  }
}

/** After the commit that changed the address: Next changes the history
 *  inside its own commit, where the View must not be updated. */
function schedule(change: Change): void {
  queueMicrotask(() => follow(change));
}

let installed = false;
function install(): void {
  if (installed) return;
  installed = true;
  stack.push(here());
  index = 0;
  const push = window.history.pushState.bind(window.history);
  const replace = window.history.replaceState.bind(window.history);
  window.history.pushState = (...args: Parameters<History["pushState"]>) => {
    push(...args);
    const url = here();
    stack.splice(index + 1, stack.length, url);
    index = stack.length - 1;
    schedule({ kind: "push", url });
  };
  window.history.replaceState = (...args: Parameters<History["replaceState"]>) => {
    const before = stack[index];
    replace(...args);
    const url = here();
    const goesBack = replaceGoesBack;
    replaceGoesBack = false;
    // Next stores its own state with the same address often.
    if (url === before && !goesBack) return;
    stack[index] = url;
    schedule({ kind: "replace", url, goesBack });
  };
  window.addEventListener("popstate", () => {
    const url = here();
    let kind: Change["kind"] = "back";
    if (stack[index - 1] === url) index -= 1;
    else if (stack[index + 1] === url) { index += 1; kind = "forward"; }
    else {
      const at = stack.lastIndexOf(url);
      if (at >= 0) { kind = at < index ? "back" : "forward"; index = at; }
      else { stack.splice(index, 1, url); }
    }
    if (expected === url) { expected = null; return; }
    expected = null;
    schedule({ kind, url });
  });
}

/** The View's router, once it is up. When the View moves on its own (its
 *  Back, its swipe back), the history follows: one entry back when that
 *  is the page it went to, else the address in place (Back on a page the
 *  app opened on directly, which brings in Projects). */
export function attachRouter(next: Router.Router): void {
  router = next;
  next.on("routeChanged", (route: Router.Route) => {
    if (route.url === here()) return;
    if (stack[index - 1] === route.url) {
      expected = route.url;
      window.history.back();
    } else {
      window.history.replaceState(null, "", route.url);
    }
  });
}

/** What a page's Back is in Framework7's terms: the previous page, or
 *  Projects for a page the app opened on directly. */
export function backTo(label: string | true = true) {
  const opened = !router || router.history.length <= 1;
  return opened
    ? { backLink: label, backLinkUrl: "/chats", backLinkForce: true }
    : { backLink: label };
}

/** Called before the View mounts, so no change is missed. */
export function startPhoneRouter(): void {
  install();
}

/** One page back; Projects, coming in from the left, on a page the app
 *  opened on directly. */
export function goBack(): void {
  if (index > 0) {
    window.history.back();
    return;
  }
  replaceGoesBack = true;
  window.history.replaceState(null, "", "/chats");
}
