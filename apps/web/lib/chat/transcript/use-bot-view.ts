"use client";

import { useAgents } from "@/lib/agents/agents-query";
import { conversationAgentId } from "@/lib/agents/settle-agent-invocation";
import { useSessionStore } from "@/lib/session-store";

/** The saved Agent a conversation runs with: a draft's pick, its own, else the default. */
export function useConversationAgent(sessionId: string | null, chatKey: string | null) {
  const own = useSessionStore((state) => conversationAgentId(state, sessionId, chatKey));
  const { data: agents } = useAgents();
  const id = own ?? agents?.find((agent) => agent.default)?.id ?? "main";
  return agents?.find((agent) => agent.id === id);
}

/** Whether this conversation belongs to an always-on Agent. */
export function useBotView(sessionId: string | null): boolean {
  return !!useConversationAgent(sessionId, null)?.always_on?.enabled;
}
