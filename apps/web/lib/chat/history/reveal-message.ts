/**
 * Scroll the transcript to a message and flash it. The context breakdown's
 * rows and a fork's "Back to original" both land here; find-in-conversation
 * takes the same route (load the page of history around the message first
 * when it isn't in the transcript yet).
 */

import { useSessionStore } from "@/lib/session-store";
import { loadSessionHistoryWindow } from "@/lib/runtime-bridge/session/session-history-loader";
import { announceReaderSeek } from "@/lib/chat/history/chat-scroll";
import { openSession } from "@/lib/tabs/navigation/open-session";

export async function revealMessage(sessionId: string, id: string): Promise<void> {
  const loaded = useSessionStore.getState().messageOrder[sessionId]?.includes(id);
  if (!loaded) await loadSessionHistoryWindow(sessionId, "around", id);
  const esc = CSS.escape(id);
  const find = () => document.querySelector<HTMLElement>(
    `#chatMessages [data-msg-id="${esc}"], #chatMessages [data-msg-ids~="${esc}"]`,
  );
  // A recycled row is a sized slot until it is live; bring it near first.
  for (let i = 0; i < 20 && !find(); i += 1) {
    const slot = document.querySelector<HTMLElement>(`[data-msg-slot="${esc}"]`);
    // A seek started outside the transcript: a growing reply must not pull
    // the view back to the latest while it moves.
    if (slot) announceReaderSeek(slot);
    slot?.scrollIntoView({ block: "center" });
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  const el = find();
  if (!el) return;
  announceReaderSeek(el);
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("dag-flash");
  window.setTimeout(() => el.classList.remove("dag-flash"), 1400);
}

/** How long a just-opened conversation may take to read before the reveal is dropped. */
const OPEN_WAIT_MS = 10_000;
/** The transcript's first paint settles (it opens at the latest) before the seek. */
const SETTLE_MS = 250;

/** Open a conversation the way a sidebar row does, then reveal ``msgId`` in
 *  it once its transcript is on screen. Without ``msgId``, or when the
 *  reader moves elsewhere first, it only opens. */
export function openSessionAtMessage(sessionId: string, msgId?: string | null): void {
  openSession(sessionId);
  if (!msgId) return;
  const ready = () => {
    const s = useSessionStore.getState();
    return s.currentSessionId === sessionId
      && s.transcriptLoadingId !== sessionId
      && s.transcriptReadStatus[sessionId] !== "loading"
      && (s.messageOrder[sessionId]?.length ?? 0) > 0;
  };
  let done = false;
  let unsubscribe = () => {};
  const finish = (reveal: boolean) => {
    if (done) return;
    done = true;
    unsubscribe();
    window.clearTimeout(timer);
    if (!reveal) return;
    window.setTimeout(() => {
      if (useSessionStore.getState().currentSessionId === sessionId) void revealMessage(sessionId, msgId);
    }, SETTLE_MS);
  };
  const timer = window.setTimeout(() => finish(false), OPEN_WAIT_MS);
  unsubscribe = useSessionStore.subscribe(() => { if (ready()) finish(true); });
  if (ready()) finish(true);
}
