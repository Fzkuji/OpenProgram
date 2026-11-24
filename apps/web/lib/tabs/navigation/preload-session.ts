// Show or start reading a conversation the moment the user picks it.
//
// Opening a conversation moves the URL with pushState; Next applies that as a
// transition, and the conversation became current only in the route effects
// after it committed (app-shell.tsx sets it, page-shell.tsx reads it), about
// 420 ms after the click. On the click itself:
//   * a conversation this client already holds becomes current at once, so
//     its rows draw on the next frame; the route effect still asks for a
//     fresh copy behind them;
//   * any other is asked for now, so its read is on the wire that much
//     earlier. The route effect's own request folds into this one
//     (session-load keeps one read in flight per session).
import { holdsHistoryWindow, useSessionHistory } from "@/lib/chat/history/session-history";
import { requestSessionLoad } from "@/lib/runtime-bridge/session/session-load";
import { useSessionStore } from "@/lib/session-store";
import { isDraftSession } from "@/lib/tabs/center-tabs-store";

/** Whether this client already holds the conversation's rows and window, so
 *  opening it draws at once (the same test page-shell.tsx makes). */
export function sessionHeld(sessionId: string): boolean {
  return holdsHistoryWindow(useSessionHistory.getState().pages[sessionId])
    && (useSessionStore.getState().messageOrder[sessionId]?.length ?? 0) > 0;
}

/** Make a held conversation current now, or ask for one not held yet.
 *  Nothing happens for the one on screen or a draft with nothing to read. */
export function preloadSession(sessionId: string): void {
  if (!sessionId || isDraftSession(sessionId)) return;
  const store = useSessionStore.getState();
  if (store.currentSessionId === sessionId) return;
  if (sessionHeld(sessionId)) {
    store.setCurrentConv(sessionId);
    return;
  }
  requestSessionLoad({ action: "load_session", session_id: sessionId });
}
