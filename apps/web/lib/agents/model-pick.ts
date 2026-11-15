import { useSessionStore, type AgentBadgeInfo } from "@/lib/session-store";
import { branchSettingsPatch, storedBranchSettings } from "@/lib/session-store/branch-settings";
import { api, type ModelPickReply } from "@/lib/net/api";
import { adoptBranchIdentity, conflictBranch } from "./branch-settings.ts";

/** Pin a model on the branch a pane shows, whatever branch is selected
 *  elsewhere: that branch stores it, and the pane's chip shows it.
 *
 *  When the branch is gone (or a conversation with no branch recorded has
 *  moved on), the pane takes the branch the conversation now shows, the way
 *  ``persistBranchSettings`` does: the pick is written there and the pane
 *  loads that branch's stored settings with it. */
export async function pickBranchModel(sessionId: string, provider: string, model: string): Promise<void> {
  const branchId = useSessionStore.getState().composerSettingsBySession[sessionId]?.active_branch_id ?? null;
  let reply: ModelPickReply;
  let moved = false;
  try {
    reply = await api.switchModel(provider, model, sessionId, branchId);
  } catch (error) {
    const shown = conflictBranch(error);
    if (shown === undefined) throw error;
    reply = await api.switchModel(provider, model, sessionId, shown);
    moved = true;
  }
  const store = useSessionStore.getState();
  // The pane moved to another branch meanwhile: this answer is not its.
  if ((store.composerSettingsBySession[sessionId]?.active_branch_id ?? null) !== branchId) return;
  const written = typeof reply.branch_id === "string" ? reply.branch_id : reply.active_branch_id;
  if (moved) {
    store.setComposerSettings(branchSettingsPatch(store.composerSettingsBySession[sessionId] ?? {},
      typeof written === "string" ? written : null, storedBranchSettings(reply.settings ?? {})), sessionId);
  } else {
    adoptBranchIdentity(sessionId, branchId, written);
  }
  // Only this conversation's model changes: the other split pane keeps its
  // own, and the global (focused) slot follows only for the focused
  // conversation. The reply carries the model's effort levels and Fast, so
  // the chip needs no read; a later read names this branch and agrees.
  const chat = { ...(reply.chat ?? {}), provider, model } as AgentBadgeInfo;
  const settled = useSessionStore.getState();
  settled.setSessionAgentSettings(sessionId, { ...settled.agentSettingsBySession[sessionId], chat });
  if (sessionId === (settled.activeChatKey ?? settled.currentSessionId)) settled.setAgentSettings({ chat });
  const selected = !reply.branch_id || reply.branch_id === reply.active_branch_id;
  if (selected) {
    const { loadSessionAgentSettings } = await import("@/lib/runtime-bridge/providers");
    void loadSessionAgentSettings(sessionId, { written: true });
  }
}
