"use client";

/**
 * "Copy conversation" in the conversation's ⋯ menu (pane/view-controls.tsx):
 * a new conversation holding this one's history up to its HEAD, in the
 * same project and environment with the same Agent, model and run
 * settings, opened like a sidebar row. The server picks the tip
 * (`/api/chat/branch_session` without a message): HEAD, or the last
 * finished turn while one runs here. It never touches this conversation,
 * so it works while a turn runs; the copy then opens beside it. The new
 * conversation's "Copied from" banner links back
 * (messages/cards/branched-from-banner.tsx).
 */

import { useSessionStore } from "@/lib/session-store";
import { showToast } from "@/lib/format-utils/toast";
import { openSession } from "@/lib/tabs/navigation/open-session";

type Text = (en: string, zh: string) => string;

/** With `msgId` the copy ends at that message rather than HEAD.
 *  Resolves to the new conversation's id, or null after a failure toast. */
export async function copyConversation(sessionId: string, text: Text, msgId?: string): Promise<string | null> {
  try {
    const response = await fetch("/api/chat/branch_session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, ...(msgId ? { msg_id: msgId } : { branch_id: useSessionStore.getState().composerSettingsBySession?.[sessionId]?.active_branch_id ?? undefined }) }),
    });
    const data = await response.json().catch(() => ({})) as {
      ok?: boolean; new_session_id?: string; title?: string; error?: string;
    };
    if (!response.ok || !data.ok || !data.new_session_id) {
      throw new Error(data.error || response.statusText || "no conversation was created");
    }
    const store = useSessionStore.getState();
    // The sessions_list broadcast may land after this reply: seed the
    // row so the tab and sidebar have a title at once.
    if (!store.conversations[data.new_session_id]) {
      store.upsertConversation({
        id: data.new_session_id,
        title: data.title || "",
        updated_at: Date.now() / 1000,
      });
    }
    openSession(data.new_session_id, { newTab: !!store.runningTasks[sessionId] });
    return data.new_session_id;
  } catch (err: unknown) {
    const reason = err instanceof Error ? err.message : String(err);
    showToast(
      text(`Could not copy the conversation: ${reason}`, `未能复制会话：${reason}`),
      { tone: "error" },
    );
    return null;
  }
}
