/**
 * Where the phone app is (components/phone/phone-app.tsx). The phone is a
 * stack of pages, as a phone app is: Projects at /chats, a project's
 * chats at /chats?project=<id>, a conversation at /s/<id> (a new one at /chat), its details at
 * /s/<id>?details and a page of the details at /s/<id>?details=<page>;
 * every other route is a page pushed over the list with a Back button.
 * Each step is a history entry, so the system's swipe from the left edge
 * (the app's back-forward gesture) and Back both go one page back.
 */

/** `step`: one step of the transcript a tap opened, which the desktop
 *  shows in the right panel's Details. */
export type DetailsPage = "changes" | "files" | "activity" | "resources" | "step";

export function pushUrl(url: string): void {
  if (window.location.pathname + window.location.search === url) return;
  window.history.pushState(null, "", url);
}

export function openDetails(sessionId: string, page?: DetailsPage): void {
  pushUrl(`/s/${encodeURIComponent(sessionId)}?details${page ? `=${page}` : ""}`);
}

/** One page back; the list when there is nothing to go back to. */
export function goBack(): void {
  if (window.history.length > 1) window.history.back();
  else window.history.replaceState(null, "", "/chats");
}
