import { commentChatKey } from "@/lib/browser/capture/page-comment";
import { getLastChatKey, useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

/** The conversation something picked in a side tab goes to: the one in
 *  focus, else the one whose agent opened that tab, else the one the user
 *  was last in. Only a conversation that is still open counts. */
export function targetChat(fromTabId?: string): { key: string; title?: string } | null {
  const state = useSessionStore.getState();
  const tabs = useCenterTabs.getState().tabs;
  const chatTab = (key: string) => tabs.find((tab) => tab.kind === "session" && tab.sessionId === key);
  const key = commentChatKey([
    state.activeChatKey ?? state.currentSessionId,
    fromTabId ? tabs.find((tab) => tab.id === fromTabId)?.agentSessionId : undefined,
    getLastChatKey(),
  ], (candidate) => candidate === state.activeChatKey || Boolean(chatTab(candidate)));
  return key ? { key, title: chatTab(key)?.title } : null;
}
