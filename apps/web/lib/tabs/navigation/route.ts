import { navigate } from "../../nav/navigate";
import { pushPath } from "../../nav/shallow-nav";

function detailHost(path: string): string | null {
  return ["/skills", "/settings/providers"].find(
    (host) => path === host || path.startsWith(`${host}/`),
  ) ?? null;
}

// Track only outstanding exported-host requests and the newest destination.
// A late host must not overwrite a newer tab/history navigation.
let pending: { hosts: Set<string>; target: string } | null = null;

export function cancelTabRouteNavigation(target: string): void {
  if (pending) pending.target = target;
}

/** Finish after an exported route commits, before recording tab history. */
export function completeTabRouteNavigation(pathname: string): boolean {
  if (!pending) return false;
  if (!pending.hosts.delete(pathname)) {
    pending.target = pathname; // A committed sidebar/deep-link navigation wins too.
    return false;
  }
  const target = pending.target;
  if (detailHost(target.split(/[?#]/, 1)[0]) === pathname) {
    if (target !== pathname) window.history.replaceState(null, "", target);
    if (pending.hosts.size === 0) pending = null;
  } else {
    // Keep outstanding newer hosts; their existing request owns the commit.
    navigateTabRoute(target);
    if (pending?.hosts.size === 0) pending = null;
  }
  return true;
}

/** Put the URL on /chat for a new chat. From /s/<id> this is the same chat
 *  page, so the URL moves at once (pushState, like every tab switch); a
 *  router push there runs as a Next transition, and the URL only follows
 *  when that transition commits — seconds later, or after the next input,
 *  while the tab strip already shows the draft. */
export function showNewChatRoute(): void {
  const current = window.location.pathname;
  if (pending) pending.target = "/chat";
  if (current === "/chat") window.history.replaceState(null, "", "/chat");
  else if (current.startsWith("/s/")) pushPath("/chat");
  else navigate("/chat");
}

/** A sidebar page needs its exported component before a shallow detail URL. */
export function navigateTabRoute(path: string): void {
  const pathname = path.split(/[?#]/, 1)[0];
  const current = typeof window !== "undefined" ? window.location.pathname : "";
  const host = detailHost(pathname);
  if (pending) pending.target = path;
  if (pathname === current) return;
  if (host !== null && detailHost(current) !== host && pathname !== host) {
    pending ??= { hosts: new Set(), target: path };
    if (!pending.hosts.has(host)) {
      pending.hosts.add(host);
      navigate(host);
    }
    return;
  }
  if (pathname === "/chat" || pathname.startsWith("/s/")
    || (host !== null && detailHost(current) === host)) {
    pushPath(path);
  } else {
    navigate(path);
  }
}
