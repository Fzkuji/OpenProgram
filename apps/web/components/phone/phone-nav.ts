/**
 * Where the phone app is (components/phone/phone-app.tsx). The phone is a
 * stack of pages, as a phone app is: Projects at /chats, a project's
 * chats at /chats?project=<id>, a conversation at /s/<id> (a new one at /chat), its details at
 * /s/<id>?details and a page of the details at /s/<id>?details=<page>;
 * every other route is a page pushed over the list with a Back button.
 * Each step is a history entry, which Framework7's View follows
 * (phone-router.ts): its Back and its swipe from the left edge both go
 * one page back.
 */

import { useSessionHistory } from "@/lib/chat/history/session-history";
import { useSessionStore } from "@/lib/session-store";
import { openSession } from "@/lib/tabs/navigation/open-session";
import { preloadSession, sessionHeld } from "@/lib/tabs/navigation/preload-session";

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

export { goBack } from "./phone-router";

let opening: { id: string; stop: () => void } | null = null;

/**
 * Open a conversation once its messages are here, waiting at most
 * `OPEN_WAIT_MS`. The page slides in only after that: opened at once, a
 * chat the phone had not read yet slid in as an empty New chat (its
 * placeholder, its default model) and turned into the conversation a
 * moment later, which read as the page jumping. A chat already read opens
 * at once.
 */
const OPEN_WAIT_MS = 700;
export function openSessionWhenReady(sessionId: string): void {
  if (opening?.id === sessionId) return;
  opening?.stop();
  if (sessionHeld(sessionId)) { openSession(sessionId); return; }
  preloadSession(sessionId);
  let done = false;
  const finish = () => {
    if (done) return;
    stop();
    openSession(sessionId);
  };
  const check = () => { if (sessionHeld(sessionId)) finish(); };
  const offStore = useSessionStore.subscribe(check);
  const offHistory = useSessionHistory.subscribe(check);
  const timer = window.setTimeout(finish, OPEN_WAIT_MS);
  const stop = () => {
    done = true;
    offStore();
    offHistory();
    window.clearTimeout(timer);
    if (opening?.id === sessionId) opening = null;
  };
  opening = { id: sessionId, stop };
}
