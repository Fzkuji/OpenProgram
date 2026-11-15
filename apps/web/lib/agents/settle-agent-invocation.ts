/**
 * A draft started from an Agent carries its pick as `agentInvocation` until
 * the server accepts the first message. On that ACK the pick stops being a
 * pending intent and becomes the conversation's own Agent and model, so the
 * composer chips keep showing them instead of falling back to the default
 * Agent and the focused conversation's model.
 */
import { useSessionStore } from "@/lib/session-store";

export function settleAgentInvocation(sessionId: string): void {
  const store = useSessionStore.getState();
  const invocation = store.composerSettingsBySession[sessionId]?.agentInvocation;
  if (!invocation) return;
  store.setBoundAgent(sessionId, invocation.agentId);
  const summary = store.conversations[sessionId];
  if (summary && summary.agent_id !== invocation.agentId) {
    store.upsertConversation({ ...summary, agent_id: invocation.agentId });
  }
  const model = invocation.resolvedModel;
  const own = store.agentSettingsBySession[sessionId];
  if (model?.provider && model.id && !own?.chat) {
    // Until GET /api/agent_settings answers for this conversation.
    store.setSessionAgentSettings(sessionId, {
      ...own,
      chat: {
        provider: model.provider,
        model: model.id,
        thinking: invocation.thinkingLevels?.length
          ? { options: invocation.thinkingLevels.map((value) => ({ value })), default: invocation.defaultThinking }
          : undefined,
      },
    });
  }
  store.setComposerSettings({ agentInvocation: undefined }, sessionId);
}

type AgentState = Pick<ReturnType<typeof useSessionStore.getState>,
  "composerSettingsBySession" | "conversations" | "boundAgentBySession">;

/** The Agent a composer's conversation runs with: a draft's pending pick,
 *  else the conversation's own (the list's, or the one its first turn bound).
 *  Undefined means the default Agent. */
export function conversationAgentId(
  state: AgentState,
  sessionId: string | null,
  chatKey: string | null,
): string | undefined {
  const invocation = chatKey ? state.composerSettingsBySession[chatKey]?.agentInvocation : undefined;
  if (invocation) return invocation.agentId;
  if (!sessionId) return undefined;
  return state.conversations[sessionId]?.agent_id ?? state.boundAgentBySession[sessionId];
}
