// Open a session or a new chat from outside the sidebar rows: a
// notification click, the command palette or the sidebar's New chat.
import { displayTitle, wsSend } from "@/components/sidebar/sessions/helpers";
import { hasPendingFirstAck } from "@/lib/chat/send/pending-user-text";
import { releaseSubAgentTranscripts } from "@/lib/chat/transcript/sub-agent-transcripts";
import { newSession } from "@/lib/runtime-bridge/session/conversations";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { useSessionStore } from "@/lib/session-store";
import { isDraftSession, sessionWasSent, useCenterTabs, type CenterTab } from "@/lib/tabs/center-tabs-store";
import { pushPath } from "@/lib/nav/shallow-nav";

/** Mirrors a sidebar row click: the active session tab navigates (a web or
 *  file tab gets a session tab), then the route follows. ``newTab`` keeps
 *  the active tab and opens the session beside it. */
export function openSession(sessionId: string, options?: { newTab?: boolean }): void {
  const conv = useSessionStore.getState().conversations[sessionId];
  useCenterTabs.getState().openSessionTab(sessionId, (conv && displayTitle(conv)) || "", options);
  pushPath("/s/" + sessionId);
}

/** An empty New chat: a draft whose first message has not left yet. A
 *  draft waiting for its first acknowledgement already holds a turn. */
export function isReusableDraft(tab: CenterTab | undefined): boolean {
  if (tab?.kind !== "session" || !tab.draft || !tab.sessionId) return false;
  const id = tab.sessionId;
  return !sessionWasSent(id) && !hasPendingFirstAck(id)
    && !(useSessionStore.getState().messageOrder[id]?.length);
}

/** Focus-or-create, like a browser's address bar: an open unsent draft tab
 *  or New tab page is reused instead of adding another empty tab. Returns
 *  the draft id. The tab strip's + still opens extra empty chats.
 *  ``fresh`` always opens a distinct draft (a project group's +). */
export function openNewChat(options?: { fresh?: boolean }): string {
  const s = useCenterTabs.getState();
  const active = s.tabs.find((t) => t.id === s.activeId);
  const existingDraft = options?.fresh
    ? undefined
    : isReusableDraft(active) ? active : s.tabs.find(isReusableDraft);
  const existingNtp = existingDraft
    ? null
    : active?.kind === "ntp" ? active : s.tabs.find((t) => t.kind === "ntp");
  let draftId: string;
  if (existingDraft) {
    if (s.activeId !== existingDraft.id) s.setActive(existingDraft.id);
    draftId = existingDraft.sessionId!;
  } else if (existingNtp) {
    // The New tab page becomes the draft, as when New session is clicked on it.
    if (s.activeId !== existingNtp.id) s.setActive(existingNtp.id);
    draftId = useCenterTabs.getState().claimDraftSessionTab();
  } else {
    draftId = s.openDraftSessionTab();
  }
  newSession(draftId);
  return draftId;
}

/** /chat under a tab that holds a real conversation (browser Back past the
 *  entry its first acknowledgement replaced) shows that conversation again
 *  instead of an empty chat titled with its name. True when the route was
 *  handed back to the tab's conversation. */
export function reopenActiveSessionRoute(): boolean {
  const s = useCenterTabs.getState();
  const active = s.tabs.find((t) => t.id === s.activeId);
  if (active?.kind !== "session" || active.draft || !active.sessionId || active.navigationRoute) return false;
  // A conversation that just left the list (deleted, cleared) is not reopened.
  if (!useSessionStore.getState().conversations[active.sessionId]) return false;
  if (window.location.pathname === "/chat") {
    window.history.replaceState(null, "", "/s/" + active.sessionId);
  }
  return true;
}

/** Drop a conversation that no longer exists from every tab and focus what
 *  remains, as closing its tab would: a tab that showed it goes back to its
 *  previous page or closes with focus on its neighbour. When that closes the
 *  last tab a fresh New chat opens. Tab activation then routes to the focused tab. */
export function forgetSessionAndRefocus(sessionId: string): void {
  delete runtimeState.conversations[sessionId];
  const store = useSessionStore.getState();
  if (store.conversations[sessionId]) store.removeConversation(sessionId);
  releaseSubAgentTranscripts(sessionId);
  const hadTabs = useCenterTabs.getState().tabs.length > 0;
  useCenterTabs.getState().removeSessionFromHistory(sessionId);
  const tabs = useCenterTabs.getState();
  if (hadTabs && !tabs.tabs.length) {
    newSession(tabs.openDraftSessionTab());
    return;
  }
  const active = tabs.tabs.find((t) => t.id === tabs.activeId);
  const shown = useSessionStore.getState();
  // A focused tab that shows no conversation (a web page, files) leaves
  // the chat shell on nothing rather than on the removed conversation.
  if (active?.kind !== "session"
    && (runtimeState.currentSessionId === sessionId
      || (shown.activeChatKey ?? shown.currentSessionId) === sessionId)) {
    runtimeState.currentSessionId = null;
    shown.setCurrentConv(null);
  }
}

/** The server answered a load with "no such session". A draft's own key
 *  is expected to be unknown until its first message, so it stays. */
export function forgetMissingSession(sessionId: string): void {
  if (isDraftSession(sessionId)) return;
  forgetSessionAndRefocus(sessionId);
}

/** Delete a conversation (sidebar menu, chat title menu) and refocus. */
export function deleteSessionAndRefocus(sessionId: string): void {
  wsSend({ action: "delete_session", session_id: sessionId });
  forgetSessionAndRefocus(sessionId);
}
