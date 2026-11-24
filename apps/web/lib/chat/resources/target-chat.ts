import { commentChatKey } from "@/lib/browser/capture/page-comment";
import { getLastChatKey, useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

function chatTarget(
  fromTabId: string | undefined,
  order: (focused: string | null, owner: string | undefined, last: string | null) => (string | null | undefined)[],
): { key: string; title?: string } | null {
  const state = useSessionStore.getState();
  const tabs = useCenterTabs.getState().tabs;
  const chatTab = (key: string) => tabs.find((tab) => tab.kind === "session" && tab.sessionId === key);
  const owner = fromTabId ? tabs.find((tab) => tab.id === fromTabId)?.agentSessionId : undefined;
  const key = commentChatKey(
    order(state.activeChatKey ?? state.currentSessionId, owner, getLastChatKey()),
    (candidate) => candidate === state.activeChatKey || Boolean(chatTab(candidate)),
  );
  return key ? { key, title: chatTab(key)?.title } : null;
}

/** The conversation something picked in a side tab goes to: the one in
 *  focus, else the one whose agent opened that tab, else the one the user
 *  was last in. Only a conversation that is still open counts. */
export function targetChat(fromTabId?: string): { key: string; title?: string } | null {
  return chatTarget(fromTabId, (focused, owner, last) => [focused, owner, last]);
}

/** The conversation a page's notes are sent to: the one whose agent opened
 *  the page, else the one in focus, else the one the user was last in. The
 *  notes are a message, not a draft, so the page's own conversation wins. */
export function ownerChat(fromTabId: string): { key: string; title?: string } | null {
  return chatTarget(fromTabId, (focused, owner, last) => [owner, focused, last]);
}
